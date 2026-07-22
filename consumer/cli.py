"""CLI: `embeddington-consume update` — pull and apply the shared knowledge graph.

A single ``update`` brings the local stack current: on a fresh install it restores the
latest baseline (snapshot + dump + named graph), then applies any newer diffs; on later
runs it applies only the diffs since the local cursor. It is idempotent and resumable.

Release assets are fetched via plain HTTPS GET; no credentials required.
"""

import argparse
import base64
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from consumer import lexical_index, release_client, restore_ops, state_paths, updater, writers
from consumer.fetcher import HttpFetcher
from embeddington import SchemaVersionError

PROG = "embeddington-consume"


def _subparser(name):
    """Return one subcommand's own ``ArgumentParser``.

    Used to compare resolved args against argparse's own defaults, so target-echo
    code isn't hand-maintaining a second copy of the default strings already
    declared in ``_build_parser``.

    Args:
        name: The subcommand name (e.g. ``"update"`` or ``"ensure-index"``).

    Returns:
        The subcommand's ``ArgumentParser``.
    """
    parser = _build_parser()
    sub_action = next(
        a
        for a in parser._subparsers._group_actions
        if a.dest == "command"  # noqa: SLF001
    )
    return sub_action.choices[name]


def _flag_tag(value, default):
    """Tag a resolved value as having come from its argparse default or an explicit flag.

    Args:
        value: The value in play (from the parsed/resolved args).
        default: The value argparse would have used had the flag been omitted.

    Returns:
        ``"(default)"`` or ``"(explicit)"``.
    """
    return "(default)" if value == default else "(explicit)"


def _echo_update_targets(args):
    """Print what `update` is about to write to, before the reachability checks run.

    Exporting ``ARANGO_ROOT_PASSWORD`` configures only the Arango *password* -- every
    URL, the collection, the db/user, and the cursor are flag-only, and most of them
    default to the same local stack. That's easy to miss: the MCP server in this repo
    DOES read ``QDRANT_URL``/``ARANGO_URL`` from the environment, so the habit carries
    over here even though the CLI never reads those vars. This is the one line that
    shows a stranger exactly what's about to be written, while it's still just a print.

    Args:
        args: The parsed, path-resolved CLI namespace (post ``_resolve_paths``).
    """
    up = _subparser("update")
    cursor_tag = "(default)" if getattr(args, "cursor_was_default", True) else "(explicit)"
    print(f"{PROG} update — targets")
    print(
        f"  qdrant   {args.qdrant_url} {_flag_tag(args.qdrant_url, up.get_default('qdrant_url'))}"
        f"   collection={args.collection}"
    )
    print(
        f"  arango   {args.arango_url} {_flag_tag(args.arango_url, up.get_default('arango_url'))}"
        f"   db={args.arango_db} user={args.arango_user}"
    )
    print(f"  cursor   {args.cursor} {cursor_tag}")


def _echo_ensure_index_targets(args):
    """Print the Qdrant target `ensure-index` is about to write to.

    Args:
        args: The parsed CLI namespace for ``ensure-index``.
    """
    ei = _subparser("ensure-index")
    tag = _flag_tag(args.qdrant_url, ei.get_default("qdrant_url"))
    print(f"{PROG} ensure-index — targets")
    print(f"  qdrant   {args.qdrant_url} {tag}   collection={args.collection}")


def _preflight(args):
    """Fail fast -- before any download -- on the two mistakes strangers make.

    ``writers.*.connect`` are lazy (python-arango defers auth to the first
    request), so without this check a wrong password surfaces only AFTER the
    828 MB baseline has been pulled, inside a subprocess whose stderr is
    captured. Ten seconds of checking saves that.

    Args:
        args: Parsed CLI namespace (urls + credentials).

    Raises:
        SystemExit: With an actionable message when Qdrant is unreachable,
            ArangoDB is unreachable, or the Arango credentials are rejected.
    """
    _echo_update_targets(args)
    try:
        with urllib.request.urlopen(f"{args.qdrant_url}/collections", timeout=10):
            pass
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(
            f"Qdrant is not reachable at {args.qdrant_url} — is the local stack up?\n"
            f"  cd consumer && docker compose up -d\n  ({exc})"
        )

    req = urllib.request.Request(f"{args.arango_url}/_api/version")
    cred = base64.b64encode(f"{args.arango_user}:{args.arango_password}".encode()).decode()
    req.add_header("Authorization", f"Basic {cred}")
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise SystemExit(
                "ArangoDB rejected the credentials. Did you load consumer/.env into "
                "this shell first?\n  set -a; . consumer/.env; set +a"
            )
        raise
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(
            f"ArangoDB is not reachable at {args.arango_url} — is the local stack up?\n"
            f"  cd consumer && docker compose up -d\n  ({exc})"
        )


def _resolve_paths(args, env=None, home=None, cwd=None, install_root_dir=None):
    """Fill in cursor/work_dir from the state dir when the flags were not passed.

    The stores are machine-global (named Docker volumes on fixed ports), so the cursor is
    too: it lives in one per-user state dir, NOT in the current working directory. An
    explicitly passed flag always wins.

    Args:
        args: The parsed CLI namespace (mutated in place and returned).
        env: Environment mapping; defaults to os.environ.
        home: The user's home directory; defaults to Path.home().
        cwd: The current working directory; defaults to Path.cwd().
        install_root_dir: Override for the install root used when probing for a
            pre-v0.2 cursor; defaults to None, which leaves production behavior
            (probe the real install root) unchanged. Tests inject this to stay
            isolated from whatever is actually on disk under the real clone.

    Returns:
        The same namespace, with ``cursor`` and ``work_dir`` as Paths and ``legacy_cursors``
        holding any pre-v0.2 cursors found on disk.
    """
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    cwd = Path.cwd() if cwd is None else Path(cwd)

    args.cursor_was_default = args.cursor is None
    args.cursor = Path(args.cursor) if args.cursor else state_paths.default_cursor_path(env, home)
    args.work_dir = (
        Path(args.work_dir) if args.work_dir else state_paths.default_work_dir(env, home)
    )
    args.legacy_cursors = state_paths.legacy_cursor_candidates(
        cwd, home, install_root_dir=install_root_dir
    )
    return args


def _cmd_update(args):
    args = _resolve_paths(args)
    _preflight(args)
    fetcher = HttpFetcher()
    rc = release_client.ReleaseClient(fetcher, repo=args.repo)
    qdrant = writers.QdrantConsumerWriter.connect(args.qdrant_url, args.collection)
    arango = writers.ArangoConsumerWriter.connect(
        args.arango_url, args.arango_db, args.arango_user, args.arango_password
    )
    baseline_importer = restore_ops.make_baseline_importer(
        rc,
        args.work_dir,
        args.qdrant_url,
        args.collection,
        args.arango_url,
        args.arango_db,
        args.arango_user,
        args.arango_password,
    )
    try:
        result = updater.update(
            rc,
            qdrant,
            arango,
            args.cursor,
            args.work_dir,
            baseline_importer,
            legacy_cursors=args.legacy_cursors,
            force_baseline=args.force_baseline,
            ensure_index=lambda: lexical_index.incremental_chunk_text_index(
                args.qdrant_url, args.collection
            ),
        )
    except SchemaVersionError:
        print(
            "error: this embeddington install is out of date — the published data "
            "format is newer than this code understands.\n"
            "  Fix: re-run the install one-liner (or `embeddington-setup` and choose "
            "Update) — it pulls new code first; updates then resume automatically.",
            file=sys.stderr,
        )
        return 4
    except updater.BaselineRequired as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    except updater.BaselineRefused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    print(_format_update(result))
    return 0


def _cmd_ensure_index(args):
    """Warm the local chunk_text lexical index; print and return its status.

    Standalone entry point for the same warm-up ``update`` runs automatically
    after a baseline restore -- useful to re-run by hand (e.g. after a manual
    Qdrant snapshot restore outside ``embeddington-consume``) without a full
    ``update``.

    Args:
        args: Parsed CLI namespace (qdrant_url, collection).

    Returns:
        0 if the index reached "ready", 1 for any degraded status.
    """
    _echo_ensure_index_targets(args)
    status = lexical_index.ensure_chunk_text_index(args.qdrant_url, args.collection)
    print(f"chunk_text index: {status}")
    return 0 if status == "ready" else 1


def _format_update(result):
    """Render an update result as a human-readable, mode-specific summary block.

    Leads with the *action* taken (not the diff count) so a full baseline restore never
    reads as a no-op — "applied 0" alone had misled users into thinking nothing happened.

    Args:
        result: The dict returned by ``updater.update`` (mode/applied/cursor/baseline).

    Returns:
        A multi-line string suitable for printing to a terminal or a cron log.
    """
    mode = result["mode"]
    lines = ["Embeddington update complete."]
    if mode == "baseline":
        b = result["baseline"]
        lines.append(f"  Action:   restored full baseline ({b['tag']})")
        lines.append(
            f"  Loaded:   {b['points']:,} vectors · {b['entities']:,} entities · "
            f"{b['edges']:,} edges"
        )
        lines.append(f"  Version:  {result['cursor']}")
        lines.append(f"  Diffs:    {result['applied']} applied on top of the baseline")
        lines.append(
            "  Note:     a one-time full re-download is expected after a compaction — "
            "existing installs re-restore the latest baseline in a single step."
        )
    elif mode == "diffs":
        lines.append(f"  Action:   applied {result['applied']} incremental update(s)")
        lines.append(f"  Version:  {result['cursor']}")
    else:  # up_to_date
        lines.append("  Action:   no changes — already the latest")
        lines.append(f"  Version:  {result['cursor']}")
    if result.get("adopted_from"):
        lines.append(f"  Migrated: adopted the cursor from {result['adopted_from']}")
    return "\n".join(lines)


def _build_parser():
    """Build the argument parser (separate so tests can reach it).

    Returns:
        The configured ``argparse.ArgumentParser``.
    """
    parser = argparse.ArgumentParser(prog=PROG)
    sub = parser.add_subparsers(dest="command", required=True)

    p_up = sub.add_parser("update", help="pull and apply the latest diffs")
    p_up.add_argument(
        "--repo",
        default="whiffernet/embeddington",
        help="owner/name of the releases repo (default: %(default)s)",
    )
    p_up.add_argument(
        "--cursor",
        default=None,
        help=(
            "cursor file (default: $EMBEDDINGTON_HOME, else $XDG_DATA_HOME/embeddington, "
            "else ~/.local/share/embeddington/.cursor)"
        ),
    )
    p_up.add_argument(
        "--work-dir",
        default=None,
        help="scratch dir for downloads (default: <state dir>/work)",
    )
    p_up.add_argument(
        "--force-baseline",
        action="store_true",
        help="ignore the local cursor and re-restore the full baseline (~828 MB)",
    )
    p_up.add_argument("--qdrant-url", default="http://localhost:6333")
    p_up.add_argument("--collection", default="technology")
    p_up.add_argument("--arango-url", default="http://localhost:8529")
    p_up.add_argument("--arango-db", default="technology_kg")
    p_up.add_argument("--arango-user", default="root")
    p_up.add_argument(
        "--arango-password",
        # Same var the consumer docker-compose uses, so one .env serves both.
        default=os.environ.get("ARANGO_ROOT_PASSWORD") or os.environ.get("ARANGO_PASSWORD", ""),
    )
    p_up.set_defaults(func=_cmd_update)

    p_ei = sub.add_parser(
        "ensure-index",
        help="warm the local chunk_text lexical index (materialize + full-text index)",
        description=(
            "Materialize the chunk_text payload field and build its full-text index on "
            "the local Qdrant collection, then print the resulting status. `update` runs "
            "this automatically after a baseline restore; this is for re-running it by "
            "hand. Exit code: 0 when the index reaches 'ready', 1 for any degraded "
            "status ('building', 'absent', or 'unavailable')."
        ),
    )
    p_ei.add_argument("--qdrant-url", default="http://localhost:6333")
    p_ei.add_argument("--collection", default="technology")
    p_ei.set_defaults(func=_cmd_ensure_index)
    return parser


def main(argv=None):
    """Parse args and dispatch. Returns a process exit code."""
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
