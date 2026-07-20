"""embeddington-setup entry point: parse flags, thread dependencies, run the flow.

Dependency injection happens at THIS layer: main() builds a deps dict of production
step functions and hands it down, so tests swap any step for a stub. Each step module
stays import-free of the others.
"""

import argparse
import time
from pathlib import Path

from consumer import lexical_index
from installer import (
    claude_step,
    cron,
    docker_ladder,
    errors,
    import_step,
    preflight,
    runner,
    stack,
    state,
    ui,
)
from installer.cron import cron_line, install_cron


def _repo_root():
    """The clone root: this file lives at <root>/installer/cli.py."""
    return Path(__file__).resolve().parent.parent


def _production_deps(repo_root, args):
    """The real step functions, partially applied with production wiring."""
    from consumer import writers

    def counters():
        password = stack.read_password(repo_root / "consumer" / ".env")
        qdrant = writers.QdrantConsumerWriter.connect(
            import_step.QDRANT_URL, import_step.COLLECTION
        )
        arango = writers.ArangoConsumerWriter.connect(
            import_step.ARANGO_URL, import_step.ARANGO_DB, "root", password
        )
        return qdrant.point_count, arango.entity_count

    def detect(_console):
        try:
            points, entities = counters()
        except Exception:
            points, entities = (lambda: 0), (lambda: 0)
        return state.detect_state(repo_root, runner.run, points, entities)

    def proof(_console):
        try:
            points, entities = counters()
        except errors.SetupError:
            raise
        except Exception as exc:
            raise errors.SetupError(
                "EMB-44",
                f"Post-import verification could not reach the stores: {exc}",
                "Give the containers a few seconds and re-run embeddington-setup --check.",
            )
        return import_step.proof_of_life(points, entities)

    def run_uninstall_dep(console, assume_yes, really, input_fn):
        from installer import uninstall  # lazy: installer/uninstall.py lands in a later task

        return uninstall.run_uninstall(
            console,
            runner.run,
            repo_root,
            assume_yes=assume_yes,
            really_delete_data=really,
            input_fn=input_fn,
        )

    return {
        "detect_state": detect,
        "run_preflight": lambda _c: preflight.run_preflight(
            runner.run, runner.http_get, disk_path=str(repo_root)
        ),
        "git_pull": lambda _c: runner.run(["git", "-C", str(repo_root), "pull", "--ff-only"]),
        "ensure_docker": lambda console, assume_yes, input_fn: docker_ladder.ensure_docker(
            console,
            runner.run,
            platform=docker_ladder.detect_platform(),
            assume_yes=assume_yes,
            input_fn=input_fn,
        ),
        "ensure_env": lambda _c: stack.ensure_env_file(repo_root / "consumer"),
        "read_password": lambda _c: stack.read_password(repo_root / "consumer" / ".env"),
        "compose_up": lambda _c: stack.compose_up(runner.run, repo_root / "consumer"),
        "wait_for_services": lambda console: stack.wait_for_services(console, runner.http_get),
        "run_import": import_step.run_import,
        "proof_of_life": proof,
        "claude_wiring": lambda console, assume_yes, input_fn: claude_step.offer_claude_wiring(
            console, runner.run, repo_root, assume_yes=assume_yes, input_fn=input_fn
        ),
        "install_cron": lambda console, assume_yes, input_fn: install_cron(
            console, runner.run, repo_root, assume_yes=assume_yes, input_fn=input_fn
        ),
        "run_uninstall": run_uninstall_dep,
        "git_head": lambda: runner.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"]
        ).out.strip(),
        "git_changed_files": lambda pre_sha: (lambda r: r.out.split() if r.rc == 0 else [])(
            runner.run(["git", "-C", str(repo_root), "diff", "--name-only", pre_sha, "HEAD"])
        ),
        "resync_venv": lambda _c: runner.run(
            [str(repo_root / ".venv" / "bin" / "pip"), "install", "-e", ".[setup]"],
            cwd=repo_root,
            stream=True,
        ),
        "merge_env": lambda _c: stack.merge_env_keys(
            repo_root / "consumer" / ".env",
            {
                "ARANGO_MEMORY_CAP": stack.adaptive_memory_cap(
                    stack.detect_total_ram_bytes(runner.run)
                )
            },
        ),
        "index_absent": lambda: (
            lexical_index.chunk_text_status(import_step.QDRANT_URL, import_step.COLLECTION)
            == "absent"
        ),
        "cron_present": lambda: cron.cron_line_present(runner.run),
    }


def _render_rows(console, results):
    ui.check_rows(console, [(r.name, r.ok, r.detail) for r in results])


def _cron_receipt(outcome, repo_root):
    """Render the receipt's auto-updates line for an install_cron outcome.

    Args:
        outcome: the string install_cron returned.
        repo_root: the clone root (for the manual line when not enabled).

    Returns:
        A ready-to-print receipt fragment.
    """
    if outcome in ("installed", "installed-cron-down"):
        line = "  Auto-updates: enabled (daily 06:00). Remove with embeddington-setup --uninstall."
        if outcome == "installed-cron-down":
            # Generic-correct: do NOT hardcode a start command — macOS has no `service`,
            # and WSL2 needs more than starting cron. Point at the per-platform README note.
            line += (
                "\n    [yellow]note: no cron daemon detected — the job won't run until cron "
                "is running. See the README's auto-updates note for your platform "
                "(Linux/macOS/WSL2).[/yellow]"
            )
        return line
    return (
        "  Auto-updates: not set up. To enable later, add this crontab line:\n"
        f"    {cron_line(repo_root)}"
    )


def _update_receipt(did, points, entities, mcp_changed, cron_outcome, repo_root):
    """Render a two-shape Update receipt: light when nothing structural changed,
    heavy (enumerated) when one-time upgrades landed.

    Args:
        did: dict of what happened — keys "data_mode", "applied", "deps", "env".
        points, entities: proof-of-life counts.
        mcp_changed: True if mcp/ changed in this pull (drives the restart hint).
        cron_outcome: install_cron's return, or None if not offered.
        repo_root: the clone root (unused today; kept for symmetry with _cron_receipt).

    Returns:
        A ready-to-print receipt string.
    """
    mode, applied = did.get("data_mode"), did.get("applied", 0)
    if mode == "diffs" and applied:
        data = (
            f"  Data:     applied {applied} update(s) — {points:,} vectors · {entities:,} entities"
        )
    elif mode == "baseline":
        data = f"  Data:     restored baseline — {points:,} vectors · {entities:,} entities"
    else:
        data = f"  Data:     already current — {points:,} vectors · {entities:,} entities"

    heavy = []
    if did.get("deps"):
        heavy.append("  Deps:     re-synced (dependencies changed in this update)")
    if did.get("env"):
        heavy.append("  Config:   added new settings to consumer/.env")
    if mcp_changed:
        heavy.append(
            "  Code:     Claude search tools updated — your data works now; reopen "
            "Claude Desktop (Claude Code auto-loads) to use the new code"
        )
    if cron_outcome in ("installed", "installed-cron-down"):
        heavy.append("  Auto-updates: enabled (daily 06:00)")

    lines = [data]
    if heavy:
        lines.append("  One-time upgrades applied:")
        lines.extend(heavy)
    else:
        lines.append("  Stack, index, and settings already current.")
    return "\n".join(lines)


def _import_with_readiness_retry(console, deps, args, password, *, sleep=None):
    """Run the import + proof-of-life; retry ONCE if the stores are momentarily not ready.

    A `docker compose up -d --build` that recreates arango (e.g. to apply the memory cap)
    leaves arangod replaying its WAL for a few seconds; the first read fails as EMB-44
    (proof-of-life) and the first diff-apply write fails as EMB-45 (run_import maps any
    unexpected store error to EMB-45 — see import_step Step 8). Both are transient here, so
    wait briefly and retry once before surfacing the error. A genuinely broken store
    re-raises the same code on the second try. Auth-free; reuses the verified EMB-44/EMB-45
    signals (no availability probe — see the Task 3 WAL-readiness note for why that was
    rejected).

    Args:
        console: rich Console.
        deps: the step-function bundle (needs "run_import" and "proof_of_life").
        args: parsed CLI args (needs .repo and .force_baseline).
        password: the ArangoDB root password.
        sleep: time.sleep override (injected for testing).

    Returns:
        (result, points, entities) on success.

    Raises:
        errors.SetupError: any non-readiness error, or a second consecutive failure.
    """
    sleep = time.sleep if sleep is None else sleep
    for attempt in range(2):
        try:
            result = deps["run_import"](
                console, _repo_root(), password, repo=args.repo, force_baseline=args.force_baseline
            )
            points, entities = deps["proof_of_life"](console)
            return result, points, entities
        except errors.SetupError as exc:
            if exc.code in ("EMB-44", "EMB-45") and attempt == 0:
                console.print(
                    "[yellow]Stores still settling (database recovery?) — retrying "
                    "in a moment...[/yellow]"
                )
                sleep(5)
                continue
            raise


def _update_flow(console, deps, args, input_fn):
    """Fast, idempotent Update: apply every cheap delta (data, container config, .env,
    conditional venv), then a two-shape receipt. Each step no-ops when already current.

    Args:
        console: rich Console.
        deps: the step-function bundle (see `_production_deps`).
        args: parsed CLI args.
        input_fn: callable() -> str used for interactive prompts.

    Returns:
        0 on success (this flow only raises via the steps it calls; a SetupError
        propagates to `main`'s handler).
    """
    pre = deps["git_head"]()
    pull = deps["git_pull"](console)
    if pull.rc != 0:
        console.print("[yellow]git pull failed (local changes?) — updating what I can.[/yellow]")
    changed = deps["git_changed_files"](pre)  # [] when the pull failed or moved nothing

    did = {}
    # Venv re-sync ONLY when packaging changed — avoids a multi-second re-resolve (and
    # silently pulling breaking dep majors) on unrelated data updates.
    if any(
        f == "pyproject.toml" or f.endswith("requirements.txt") or f.startswith("requirements")
        for f in changed
    ):
        deps["resync_venv"](console)
        did["deps"] = True

    did["env"] = bool(deps["merge_env"](console))

    ui.rule(console, "Local stack")
    deps["compose_up"](console)  # up -d --build: cache-cheap no-op when unchanged,
    deps["wait_for_services"](console)  # and the only thing that lands embed code changes

    ui.rule(console, "Knowledge graph")
    # ETA banner ONLY when a real materialize will run (chunk_text absent = never built
    # here yet). A no-op run stays quiet; honors "lead with an ETA only when work happens".
    if deps["index_absent"]():
        console.print(
            "[cyan]First run will build the keyword search index (~3–4 min on a full "
            "graph, one time only — safe to leave running).[/cyan]"
        )
    password = deps["read_password"](console)
    result, points, entities = _import_with_readiness_retry(console, deps, args, password)
    did["data_mode"], did["applied"] = result.get("mode"), result.get("applied", 0)

    mcp_changed = any(f.startswith("mcp/") for f in changed)
    cron_outcome = None
    if not deps["cron_present"]():
        ui.rule(console, "Auto-updates")
        cron_outcome = deps["install_cron"](console, args.yes, input_fn)

    ui.rule(console, "Receipt")
    console.print(_update_receipt(did, points, entities, mcp_changed, cron_outcome, _repo_root()))
    return 0


def _doctor(console, deps):
    """--check: render state + preflight, mutate nothing, exit 0 iff healthy.

    [CRITIC] embed is part of health: it powers vector_search, and a doctor that exits
    0 with the embed container down blesses an install whose query path is dead. Only
    mcp deps stay advisory (the graph is usable without Claude).
    """
    ui.rule(console, "Doctor")
    st = deps["detect_state"](console)
    results = deps["run_preflight"](console)
    _render_rows(console, results)
    rows = [
        ("consumer/.env", st.env_present, "present" if st.env_present else "missing (EMB-33)"),
        (
            "containers",
            st.containers_running,
            "qdrant+arango running" if st.containers_running else "down (EMB-31)",
        ),
        ("embed", st.embed_running, "running" if st.embed_running else "down (EMB-32)"),
        ("stores", st.stores_populated, "populated" if st.stores_populated else "empty"),
        ("cursor", st.cursor_present, "present" if st.cursor_present else "missing (EMB-43)"),
        ("mcp deps", st.mcp_deps, "installed" if st.mcp_deps else "not installed (optional)"),
    ]
    ui.check_rows(console, rows)
    hard_checks_ok = all(r.ok for r in results)
    install_ok = (
        st.env_present
        and st.containers_running
        and st.embed_running
        and st.stores_populated
        and st.cursor_present
    )
    return 0 if (hard_checks_ok and install_ok) else 1


def _gate_preflight(console, results):
    """Turn fatal CheckResults into SetupErrors; warnings just render."""
    _render_rows(console, results)
    for r in results:
        if r.ok:
            continue
        if r.name == "disk":
            raise errors.SetupError(
                "EMB-15",
                f"Not enough free disk: {r.detail}.",
                "Free up at least 3 GB (12+ recommended) and re-run.",
            )
        if r.name.startswith("port"):
            raise errors.SetupError(
                "EMB-24",
                f"{r.name} is {r.detail}.",
                "Stop whatever holds that port (or move it), then re-run the installer.",
            )
        # python/docker not-ok are handled by bash / the ladder respectively.


def _install_flow(console, deps, st, args, input_fn):
    """Fresh install or resume: every step self-skips when state says it's done."""
    results = deps["run_preflight"](console)
    _gate_preflight(console, results)
    deps["ensure_docker"](console, args.yes, input_fn)

    ui.rule(console, "Local stack")
    deps["ensure_env"](console)
    password = deps["read_password"](console)
    if not (st.containers_running and st.embed_running):
        deps["compose_up"](console)
    deps["wait_for_services"](console)

    ui.rule(console, "Knowledge graph")
    result = deps["run_import"](
        console, _repo_root(), password, repo=args.repo, force_baseline=args.force_baseline
    )
    points, entities = deps["proof_of_life"](console)
    console.print(
        f"[green]✓[/green] {points:,} vectors · {entities:,} entities — she's a real graph, man."
    )

    ui.rule(console, "Claude")
    deps["claude_wiring"](console, args.yes, input_fn)

    ui.rule(console, "Auto-updates")
    cron_outcome = deps["install_cron"](console, args.yes, input_fn)

    ui.rule(console, "Receipt")
    console.print(
        f"  Install:   {_repo_root()}\n"
        f"  State:     ~/.local/share/embeddington (or $EMBEDDINGTON_HOME)\n"
        f"  Version:   {result['cursor']}\n"
        f"{_cron_receipt(cron_outcome, _repo_root())}\n"
        f"  Health:    embeddington-setup --check\n"
        f"  Leaving?   embeddington-setup --uninstall\n"
    )
    return 0


def _menu(console, deps, st, args, input_fn):
    """Existing install: Update / Repair / Uninstall / Quit."""
    choice = ui.choose(
        console,
        "This box already has embeddington. What'll it be?",
        [
            ("u", "Update — get the latest (data, config, small fixes). Start here."),
            ("r", "Repair — search broken or a container won't start? Full rebuild + re-verify."),
            ("x", "Uninstall — remove embeddington (interactive, per-item)"),
            ("q", "Quit"),
        ],
        default_key="u",
        assume_yes=args.yes,
        input_fn=input_fn,
    )
    if choice == "q":
        return 0
    if choice == "x":
        return deps["run_uninstall"](console, args.yes, args.really_delete_data, input_fn)
    if choice == "r":
        return _install_flow(console, deps, st, args, input_fn)
    # Update: the fast idempotent pass (data + container config + .env + conditional venv).
    return _update_flow(console, deps, args, input_fn)


def _build_parser():
    parser = argparse.ArgumentParser(prog="embeddington-setup")
    parser.add_argument(
        "--yes", action="store_true", help="unattended: defaults everywhere, no prompts"
    )
    parser.add_argument(
        "--check", action="store_true", help="doctor mode: report health, change nothing, exit 0/1"
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="interactively remove embeddington (per-item consent)",
    )
    parser.add_argument(
        "--really-delete-data",
        action="store_true",
        help="with --yes: allow unattended deletion of data volumes/clone",
    )
    parser.add_argument(
        "--force-baseline",
        action="store_true",
        help="forwarded to the updater: re-restore the full baseline",
    )
    parser.add_argument(
        "--repo",
        default="whiffernet/embeddington",
        help="owner/name of the releases repo (default: %(default)s)",
    )
    return parser


def main(argv=None, *, console=None, deps=None, input_fn=input):
    """Entry point: parse flags, build/accept deps, and dispatch to the right flow.

    Args:
        argv: command-line arguments (excluding the program name), or None to read
            from sys.argv via argparse's default behavior.
        console: a rich Console to render to, or None to build the production one.
        deps: the step-function bundle (see `_production_deps`), or None to build the
            real production wiring. This is the injection seam tests use to swap any
            step for a stub without touching the flow logic.
        input_fn: callable() -> str used for interactive prompts, defaults to `input`.

    Returns:
        Process exit code. 0 on success; 1 if a `SetupError` was raised anywhere in
        the flow. `--check` (doctor mode) repurposes this as a health probe: 0 means
        healthy, 1 means unhealthy, and neither exit mutates anything.
    """
    args = _build_parser().parse_args(argv)
    console = ui.make_console() if console is None else console
    repo_root = _repo_root()
    deps = _production_deps(repo_root, args) if deps is None else deps

    ui.show_banner(console)
    try:
        if args.check:
            return _doctor(console, deps)
        st = deps["detect_state"](console)
        if args.uninstall:
            return deps["run_uninstall"](console, args.yes, args.really_delete_data, input_fn)
        installed = st.containers_running and st.stores_populated and st.cursor_present
        if installed:
            return _menu(console, deps, st, args, input_fn)
        return _install_flow(console, deps, st, args, input_fn)
    except errors.SetupError as err:
        ui.show_error(console, err)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
