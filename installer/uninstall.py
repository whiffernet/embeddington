"""Interactive uninstall: remove what embeddington owns, name what it merely uses.

Safety rules (spec §7 + critique): manifest first; per-item consent defaulting to No;
inspection before any volume deletion (foreign Qdrant collections / Arango databases
are named and must be interactively acknowledged); the data volumes require typing
`delete`; --yes never reads input and never deletes data without --really-delete-data
(and never deletes foreign data or a dirty clone at all); every destructive action is
rc-checked into removed/failed; shared runtimes are never removed.
"""

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from consumer import state_paths
from installer import ui
from installer.cron import CRON_MARKER, strip_cron_lines
from installer.errors import SetupError

KNOWN_QDRANT_COLLECTIONS = {"technology"}
KNOWN_ARANGO_DBS = {"technology_kg", "_system"}


@dataclass(frozen=True)
class ManifestItem:
    key: str
    label: str
    detail: str
    kind: str  # "safe" (recoverable) | "data" (irreversible) | "info" (never offered)


def inspect_stores(http_get, list_databases):
    """Look inside both stores; return (inspected, foreign_names).

    inspected=False means we could not look (daemon down) — the caller must warn that
    contents are unknown rather than pretend they are clean.
    """
    foreign = []
    try:
        import json

        status, body = http_get("http://localhost:6333/collections")
        if status != 200:
            return False, []
        names = {c["name"] for c in json.loads(body)["result"]["collections"]}
        foreign += [f"qdrant collection: {n}" for n in sorted(names - KNOWN_QDRANT_COLLECTIONS)]
        dbs = set(list_databases())
        foreign += [f"arango db: {n}" for n in sorted(dbs - KNOWN_ARANGO_DBS)]
    except Exception:
        return False, []
    return True, foreign


def resolve_volume_names(run):
    """Actual volume names, resolved from docker itself.

    [CRITIC] compose prefixes volumes with the project name (default: the compose
    file's directory, `consumer`), but COMPOSE_PROJECT_NAME changes it — a hardcoded
    `consumer_*` rm would silently no-op while the receipt claimed deletion. Falls back
    to the defaults when docker can't answer (the rm's rc-check still protects us).
    """
    res = run(["docker", "volume", "ls", "-q"])
    names = res.out.split() if res.rc == 0 else []

    def pick(suffix, default):
        matches = [n for n in names if n.endswith(suffix)]
        return matches if matches else [default]

    return {
        "data": pick("_qdrant_storage", "consumer_qdrant_storage")
        + pick("_arango_data", "consumer_arango_data"),
        "cache": pick("_embed_models", "consumer_embed_models"),
    }


def dirty_files(run, repo_root):
    """(first 20 modified/untracked names, total count) for the clone."""
    res = run(["git", "status", "--porcelain"], cwd=repo_root)
    if res.rc != 0:
        return [], 0
    lines = [line[3:] for line in res.out.splitlines() if line.strip()]
    return lines[:20], len(lines)


def build_manifest(repo_root, run, *, env=None, home=None, crontab_text=None):
    """Everything embeddington owns on this machine, with best-effort sizes.

    Volume sizes are labeled estimates (measuring named volumes needs root on the
    docker dirs) — an announced deviation from the spec's "measured sizes"; the state
    dir and clone ARE measured via du.
    """
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    state_dir = state_paths.resolve_state_dir(env, home)

    def du(path):
        try:
            res = run(["du", "-sh", str(path)])
            return res.out.split()[0] if res.rc == 0 and res.out.split() else "?"
        except Exception:
            return "?"

    items = []
    if crontab_text and CRON_MARKER in crontab_text:
        items.append(ManifestItem("cron", "daily-update crontab line", "1 line", "safe"))
    items.append(
        ManifestItem(
            "containers",
            "docker containers (qdrant, arango, embed)",
            "stopped & removed, images kept",
            "safe",
        )
    )
    items.append(
        ManifestItem(
            "embed_models",
            "embed_models volume (bge-m3 model cache)",
            "~2 GB (estimate), re-downloads on reinstall",
            "safe",
        )
    )
    items.append(
        ManifestItem(
            "data_volumes",
            "qdrant_storage + arango_data volumes (THE knowledge graph)",
            "re-import costs ~828 MB",
            "data",
        )
    )
    if state_dir.exists():
        items.append(ManifestItem("state", f"state dir {state_dir}", du(state_dir), "safe"))
    items.append(
        ManifestItem(
            "mcp", "MCP server deps (inside the clone's .venv)", "removed with the clone", "info"
        )
    )
    items.append(ManifestItem("clone", f"the clone at {repo_root}", du(repo_root), "data"))
    return items


def _rm_volumes(run, names):
    """docker volume rm, rc-checked — a failed rm must never be reported as removed."""
    return run(["docker", "volume", "rm", *names]).rc == 0


def _self_delete(console, repo_root, *, mkstemp, execv):
    """Hand the clone's deletion to a throwaway script (we're running from inside it).

    [CRITIC] mkstemp, never a fixed /tmp name (predictable paths are symlink-attack
    targets on shared boxes), and the clone path travels as "$1" — never interpolated
    into the script text, where an apostrophe in the path would break the quoting and
    delete the wrong directory.
    """
    fd, script = mkstemp(suffix=".sh", prefix="embeddington-farewell-")
    with os.fdopen(fd, "w") as handle:
        handle.write(
            "#!/bin/sh\n"
            "sleep 1\n"
            'rm -rf "$1"\n'
            "echo 'embeddington has left the building. The Dude abides.'\n"
            'rm -- "$0"\n'
        )
    # 0o700 is owner-only; the execute bit is required (this is a script) and mkstemp
    # created the file 0o600, so there is no permissive window. The nosemgrep waives
    # python.lang.security.audit.insecure-file-permissions (false positive here).
    os.chmod(script, 0o700)  # nosemgrep
    console.print(f"[dim]Handing off to {script} to remove the clone. So long, man.[/dim]")
    try:
        execv("/bin/sh", ["/bin/sh", str(script), str(repo_root)])
    except OSError:
        raise SetupError(
            "EMB-63",
            "The self-delete handoff failed.",
            f"Remove the clone yourself: rm -rf {repo_root}",
        )


def run_uninstall(
    console,
    run,
    repo_root,
    *,
    assume_yes,
    really_delete_data,
    env=None,
    home=None,
    http_get=None,
    list_databases=None,
    crontab_text=None,
    input_fn=input,
    rmtree=None,
    execv=None,
    mkstemp=None,
):
    """Walk the manifest with per-item consent; return 0 (a skipped item is not an error).

    Args:
        console / run / repo_root: as elsewhere.
        assume_yes: unattended — no prompt is EVER read (there is no stdin to read).
            Safe items default-remove; data items are kept unless really_delete_data.
        really_delete_data: unlocks unattended deletion of the data volumes (only when
            no foreign data was found) and the clone (only when git is clean).
        env / home: state-dir resolution seams.
        http_get / list_databases: store-inspection seams (production: runner.http_get
            and a python-arango _system databases() call built from consumer/.env).
        crontab_text: injected crontab contents (production: `crontab -l`).
        input_fn / rmtree / execv / mkstemp: prompt + destruction seams (production:
            input, shutil.rmtree, os.execv, tempfile.mkstemp).

    Returns:
        0 always — a declined or failed item lands in the receipt, not the exit code.
    """
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    rmtree = shutil.rmtree if rmtree is None else rmtree
    execv = os.execv if execv is None else execv
    mkstemp = tempfile.mkstemp if mkstemp is None else mkstemp
    repo_root = Path(repo_root)

    if crontab_text is None:
        res = run(["crontab", "-l"])
        crontab_text = res.out if res.rc == 0 else ""
    if http_get is None:
        from installer import runner as _runner

        http_get = _runner.http_get
    if list_databases is None:
        list_databases = _production_list_databases(repo_root)

    ui.rule(console, "Uninstall — the manifest")
    manifest = build_manifest(repo_root, run, env=env, home=home, crontab_text=crontab_text)
    for item in manifest:
        tag = {
            "data": "[red]irreversible[/red]",
            "safe": "[green]recoverable[/green]",
            "info": "[dim]info[/dim]",
        }[item.kind]
        console.print(f"  • {item.label}  [dim]({item.detail})[/dim]  {tag}")
    console.print()

    volumes = resolve_volume_names(run)
    # [CRITIC] resolve_volume_names suffix-matches and can over-match a second compose
    # project's stopped volume; naming the resolved list at BOTH the prompt and the
    # receipt makes an over-match visible before and after, not just discoverable by
    # re-running `docker volume ls`.
    data_label = f"data_volumes ({', '.join(volumes['data'])})"
    keys = {i.key for i in manifest}
    removed, kept, failed = [], [], []

    def record(key, ok):
        (removed if ok else failed).append(key)

    def offer(key, prompt, action, *, default=False):
        if key not in keys:
            return
        if ui.confirm(console, prompt, default=default, assume_yes=assume_yes, input_fn=input_fn):
            record(key, action())
        else:
            kept.append(key)

    # 1. crontab line
    def rm_cron():
        new_tab = strip_cron_lines(crontab_text)
        with tempfile.NamedTemporaryFile("w", suffix=".cron", delete=False) as f:
            f.write(new_tab)
        if run(["crontab", f.name]).rc != 0:
            ui.show_error(
                console,
                SetupError(
                    "EMB-62",
                    "Rewriting your crontab failed.",
                    "Run `crontab -e` and remove the embeddington line yourself.",
                ),
            )
            return False
        return True

    offer("cron", "Remove the daily-update crontab line?", rm_cron, default=assume_yes)

    # 2. containers
    offer(
        "containers",
        "Stop and remove the containers (docker compose down)?",
        lambda: run(["docker", "compose", "down"], cwd=repo_root / "consumer").rc == 0,
        default=assume_yes,
    )

    # 3. embed_models cache volume (recoverable -> ordinary y/N)
    offer(
        "embed_models",
        "Remove the embed_models volume (~2 GB, re-downloads on reinstall)?",
        lambda: _rm_volumes(run, volumes["cache"]),
        default=assume_yes,
    )

    # 4. data volumes — inspection, acknowledgment, typed delete
    inspected, foreign = inspect_stores(http_get, list_databases)
    if not inspected:
        # EMB-61's registered condition, shown with its code (non-fatal warning).
        ui.show_error(
            console,
            SetupError(
                "EMB-61",
                "I couldn't look inside the stores (daemon down?), so I can't prove the "
                "volumes hold only embeddington data.",
                "For an inspected deletion: cd consumer && docker compose up -d, re-run "
                "the uninstall. Or proceed knowing the contents are unverified.",
            ),
        )

    if assume_yes:
        # [CRITIC] Unattended mode NEVER reads a prompt here. Foreign data is never
        # acknowledged on the user's behalf — not even with --really-delete-data.
        if foreign:
            console.print(
                "[bold yellow]Foreign data found — data volumes KEPT "
                "(unattended mode never acknowledges foreign-data "
                "destruction):[/bold yellow]"
            )
            for name in foreign:
                console.print(f"    • {name}")
            kept.append("data_volumes")
        elif really_delete_data:
            console.print("  volumes to delete: " + ", ".join(volumes["data"]))
            record(data_label, _rm_volumes(run, volumes["data"]))
        else:
            kept.append("data_volumes")
            console.print(
                "[dim]Data volumes kept (--yes without --really-delete-data "
                "never deletes data).[/dim]"
            )
    else:
        proceed_to_gate = True
        if foreign:
            console.print(
                "[bold red]Hold on — these stores contain data embeddington "
                "didn't put there:[/bold red]"
            )
            for name in foreign:
                console.print(f"    [red]•[/red] {name}")
            proceed_to_gate = ui.confirm(
                console,
                "I will NOT offer volume deletion unless you acknowledge that the data "
                "above will be destroyed with it. Acknowledge?",
                default=False,
                input_fn=input_fn,
            )
        if proceed_to_gate:
            console.print("  volumes to delete: " + ", ".join(volumes["data"]))
        if proceed_to_gate and ui.typed_confirm(
            console,
            "Delete qdrant_storage + arango_data — the knowledge graph itself? "
            "This cannot be undone.",
            input_fn=input_fn,
        ):
            record(data_label, _rm_volumes(run, volumes["data"]))
        else:
            kept.append("data_volumes")

    # 5. state dir — warn when it would strand a kept graph at the guard (EMB-43).
    state_dir = state_paths.resolve_state_dir(env, home)
    if state_dir.exists():
        warning = ""
        if "data_volumes" in kept:
            warning = (
                " [yellow]Careful: you're keeping the graph — deleting its cursor makes "
                "the next install refuse (EMB-43) until you restore the cursor or use "
                "--force-baseline.[/yellow]"
            )

        def rm_state():
            try:
                rmtree(state_dir)
                return True
            except OSError:
                return False

        offer(
            "state",
            f"Remove the state dir {state_dir} (cursor + scratch)?{warning}",
            rm_state,
            default=assume_yes,
        )

    # 6. the clone decision (deletion itself happens after the receipt).
    names, total = dirty_files(run, repo_root)
    if names:
        console.print("[yellow]The clone has local changes/untracked files:[/yellow]")
        for name in names:
            console.print(f"    • {name}")
        if total > len(names):
            console.print(f"    … and {total - len(names)} more")
    if assume_yes:
        delete_clone = really_delete_data and total == 0
        if really_delete_data and total:
            console.print(
                "[bold yellow]Clone KEPT: it holds local/untracked files, and "
                "unattended mode never deletes unknown data.[/bold yellow]"
            )
    else:
        delete_clone = ui.typed_confirm(
            console,
            f"Delete the clone at {repo_root} (including .venv"
            f"{' and the files above' if names else ''})? This is irreversible.",
            input_fn=input_fn,
        )
    (removed if delete_clone else kept).append("clone")

    # 7. receipt — AFTER every decision, including the clone's, and BEFORE the handoff.
    ui.rule(console, "Receipt")
    console.print(f"  Removed: {', '.join(removed) or 'nothing'}")
    if failed:
        console.print(f"  [red]FAILED (still present!): {', '.join(failed)}[/red]")
    console.print(f"  Kept:    {', '.join(kept) or 'nothing'}")
    console.print(
        "\n[bold]Deliberately not touched[/bold] (shared infrastructure — remove "
        "yourself if truly unused):\n"
        "  • Docker / OrbStack / Colima / Homebrew (e.g. `brew uninstall --cask orbstack`)\n"
        "  • Python and pip\n"
    )

    if delete_clone:
        _self_delete(console, repo_root, mkstemp=mkstemp, execv=execv)
    else:
        console.print(f"[dim]Clone kept at {repo_root}.[/dim]")
    return 0


def _production_list_databases(repo_root):
    """Lazy Arango _system databases() using the local root credentials."""

    def list_databases():
        from arango import ArangoClient

        from installer import stack

        password = stack.read_password(repo_root / "consumer" / ".env")
        sys_db = ArangoClient(hosts="http://localhost:8529").db(
            "_system", username="root", password=password
        )
        return sys_db.databases()

    return list_databases
