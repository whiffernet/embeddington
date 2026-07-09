"""CLI: `embeddington-consume update` — pull and apply the shared knowledge graph.

A single ``update`` brings the local stack current: on a fresh install it restores the
latest baseline (snapshot + dump + named graph), then applies any newer diffs; on later
runs it applies only the diffs since the local cursor. It is idempotent and resumable.

The repo is public; release assets are fetched via plain HTTPS GET, no credentials required.
"""

import argparse
import base64
import os
import sys
import urllib.error
import urllib.request

from consumer import release_client, restore_ops, updater, writers
from consumer.fetcher import HttpFetcher


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


def _cmd_update(args):
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
        result = updater.update(rc, qdrant, arango, args.cursor, args.work_dir, baseline_importer)
    except updater.BaselineRequired as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    print(_format_update(result))
    return 0


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
        lines.append(f"  Action:  restored full baseline ({b['tag']})")
        lines.append(
            f"  Loaded:  {b['points']:,} vectors · {b['entities']:,} entities · "
            f"{b['edges']:,} edges"
        )
        lines.append(f"  Version: {result['cursor']}")
        lines.append(f"  Diffs:   {result['applied']} applied on top of the baseline")
        lines.append(
            "  Note:    a one-time full re-download is expected after a compaction — "
            "existing installs re-restore the latest snapshot in a single step."
        )
    elif mode == "diffs":
        lines.append(f"  Action:  applied {result['applied']} incremental update(s)")
        lines.append(f"  Version: {result['cursor']}")
    else:  # up_to_date
        lines.append("  Action:  no changes — already the latest")
        lines.append(f"  Version: {result['cursor']}")
    return "\n".join(lines)


def _build_parser():
    """Build the argument parser (separate so tests can reach it).

    Returns:
        The configured ``argparse.ArgumentParser``.
    """
    parser = argparse.ArgumentParser(prog="embeddington-consume")
    sub = parser.add_subparsers(dest="command", required=True)

    p_up = sub.add_parser("update", help="pull and apply the latest diffs")
    p_up.add_argument(
        "--repo",
        default="whiffernet/embeddington",
        help="owner/name of the releases repo (default: %(default)s)",
    )
    p_up.add_argument("--cursor", default="data/.cursor")
    p_up.add_argument("--work-dir", default="data/work")
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
    return parser


def main(argv=None):
    """Parse args and dispatch. Returns a process exit code."""
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
