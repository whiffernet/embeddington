"""Optional Claude wiring: install the server's deps, verify it starts, offer reach.

Never fatal — the graph is complete and usable without Claude. Failures show EMB-51 as
a warning and the flow continues.

Note what this module does NOT do: write a password anywhere. server.py falls back to the
consumer stack's own .env, which already holds that credential at 0600 from install time,
so nothing here needs to copy it into a second file. One secret, one place on disk. The
configuration the server reads is therefore complete the moment the stack exists —
independent of the shell, the working directory, or whether the client is a GUI.
"""

import subprocess
import sys
from dataclasses import dataclass

from installer import errors, ui


def offer_claude_wiring(console, run, repo_root, *, assume_yes, which=None, input_fn=input):
    """Configure the MCP server, install its deps, prove it starts, and offer reach.

    Order matters. mcp/.env is written FIRST and unconditionally — before the check for
    the Claude Code CLI — because a Claude Desktop user has no CLI on PATH and needs that
    file more than anyone: a GUI app inherits no shell exports at all.

    Never fatal. Every failure here is shown and stepped over; the graph is complete and
    queryable without any of it.

    Args:
        console: rich Console for output.
        run: Callable that runs a subprocess command (takes cmd argv list).
        repo_root: Path to the repo root.
        assume_yes: If True, skip prompts and assume affirmative answers.
        which: Callable to locate a binary on PATH (default: shutil.which).
        input_fn: Callable to read user input (default: builtins.input).

    Returns:
        WiringResult describing each half of the outcome.
    """
    import shutil

    which = shutil.which if which is None else which
    if which("claude") is None:
        console.print(
            "[dim]Claude Code isn't on your PATH. The server reads its password from "
            "consumer/.env on its own, so Claude Desktop works once you point it at "
            "mcp/server.py (see mcp/README.md); the graph works either way.[/dim]"
        )
        return WiringResult("no-claude", "not-run", "not-offered")

    if not ui.confirm(
        console,
        "Claude Code detected. Wire up the embeddington MCP server (installs mcp/ deps)?",
        default=True,
        assume_yes=assume_yes,
        input_fn=input_fn,
    ):
        return WiringResult("skipped", "not-run", "not-offered")

    req = repo_root / "mcp" / "requirements.txt"
    result = run([sys.executable, "-m", "pip", "install", "-r", str(req)])
    if result.rc != 0:
        ui.show_error(
            console,
            errors.SetupError(
                "EMB-51",
                "Installing the MCP server's dependencies failed (the graph itself is fine).",
                f"Run `.venv/bin/pip install -r {req}` manually to see why.",
            ),
        )
        return WiringResult("failed", "not-run", "not-offered")

    verify_status, detail = verify_mcp_server(run, repo_root)
    if verify_status != "verified":
        # Shown, never raised: a server that won't start costs the user nothing they
        # already had, and the data update that preceded this is worth keeping.
        ui.show_error(console, mcp_verification_error(verify_status, detail))
        return WiringResult("installed", verify_status, "not-offered")

    console.print("\n[green]✓[/green] MCP server verified — it starts and answers.")
    registration = offer_user_scope(
        console, run, repo_root, assume_yes=assume_yes, input_fn=input_fn
    )

    if registration in ("registered", "refreshed"):
        console.print(
            f"  Registered as [bold]{USER_SCOPE_NAME}[/bold] — run [bold]claude[/bold] "
            "from anywhere and it's there."
        )
    else:
        console.print(
            "  To query the graph: [bold]cd <your clone> && claude[/bold], then approve "
            "the 'embeddington' server. No venv activation and no exported password — "
            "the server reads consumer/.env itself."
        )
    return WiringResult("installed", verify_status, registration)


def ensure_claude_wiring(console, run, repo_root):
    """Prompt-free wiring refresh for the update path. Never raises, never asks.

    Mirrors the cron step's split: refresh what the user already has, never introduce
    something that needs consent. That is what makes it safe to run from an unattended
    nightly update — and what lets an install broken by the old configuration repair
    itself without anybody doing anything.

    The server is only probed when its dependencies are actually installed. A user who
    declined Claude wiring should not be nagged about a component they never wanted.

    Nothing here writes configuration: an install left broken by an older installer is
    repaired by the code update alone (.mcp.json's interpreter and server.py's password
    fallback both arrive with the pull), so this step only has to re-verify and repoint.

    Args:
        console: rich Console for output.
        run: runner.run-compatible callable.
        repo_root: the clone root.

    Returns:
        WiringResult (deps here reports what was found, not what was installed).
    """
    if not mcp_deps_installed(run, repo_root):
        return WiringResult("absent", "not-run", "not-offered")

    verify_status, detail = verify_mcp_server(run, repo_root)
    if verify_status not in ("verified", "stack"):
        # "stack" is the containers being down mid-update, not a wiring problem, and it
        # resolves itself; anything else is worth a word.
        ui.show_error(console, mcp_verification_error(verify_status, detail))

    return WiringResult("present", verify_status, refresh_user_scope(run, repo_root))


# Generous: the server's startup sanity check makes real calls to the local stores, and a
# cold container answers slowly. This only bounds a hang, it is not a latency budget.
MCP_VERIFY_TIMEOUT = 60


def _stderr_tail(text, lines=3):
    """The last few non-blank lines — the part that names the actual failure."""
    kept = [line for line in (text or "").splitlines() if line.strip()]
    return "\n".join(kept[-lines:]).strip()


def _diagnose(res):
    """Classify a probe result into (status, detail).

    Every one of these reaches the user's client as the same "Connection closed", so the
    wizard's whole value here is telling them apart. Signatures are taken from the
    server's own startup paths, not guessed.

    Args:
        res: RunResult from the probe spawn.

    Returns:
        (status, detail) where status is verified | no-interpreter | deps | password |
        stack | unknown.
    """
    text = f"{res.err or ''}\n{res.out or ''}"
    if res.rc == 0:
        return "verified", ""
    if res.rc == 127 or "command not found" in text:
        return "no-interpreter", _stderr_tail(text)
    if "No module named" in text:
        return "deps", _stderr_tail(text)
    if "Missing required env var" in text:
        return "password", _stderr_tail(text)
    if "Refusing to start" in text:
        return "stack", _stderr_tail(text)
    return "unknown", _stderr_tail(text)


def verify_mcp_server(run, repo_root, *, timeout=MCP_VERIFY_TIMEOUT):
    """Actually start the server and see what happens. Never raises.

    Spawns exactly what the client will spawn — the clone's venv interpreter, the
    repo-relative script, the same working directory — with stdin closed. Measured: a
    healthy server takes the EOF, shuts down cleanly, and exits 0.

    Args:
        run: runner.run-compatible callable.
        repo_root: the clone root.
        timeout: seconds before the probe is treated as hung.

    Returns:
        (status, detail) — see _diagnose. "timeout" when the server never exited.
    """
    interpreter = repo_root / ".venv" / "bin" / "python"
    try:
        res = run(
            [str(interpreter), "mcp/server.py"],
            cwd=repo_root,
            timeout=timeout,
            stdin_devnull=True,
        )
    except subprocess.TimeoutExpired:
        return "timeout", f"still running after {timeout}s with stdin closed"
    return _diagnose(res)


_VERIFY_FIXES = {
    "no-interpreter": (
        "The clone's .venv is missing or incomplete. Re-run the install one-liner; it "
        "rebuilds the environment."
    ),
    "deps": (
        "Re-run embeddington-setup and accept the Claude step, or install them yourself "
        "with the clone's own interpreter: .venv/bin/pip install -r mcp/requirements.txt"
    ),
    "password": (
        "The server reads ARANGO_ROOT_PASSWORD from consumer/.env, so this usually means "
        "that file is missing or empty — re-run embeddington-setup to regenerate it. To "
        "point the server somewhere else instead, set ARANGO_PASSWORD in mcp/.env."
    ),
    "stack": (
        "This is the local stack being down, not the Claude wiring — that part is fine. "
        "Run embeddington-setup --check to see which container isn't answering."
    ),
    "timeout": (
        "The server started but never exited when its input closed. Run it by hand to "
        "watch it: .venv/bin/python mcp/server.py < /dev/null"
    ),
    "unknown": (
        "Run the probe yourself to see the whole error: .venv/bin/python mcp/server.py < /dev/null"
    ),
}


def mcp_verification_error(status, detail):
    """Turn a non-verified probe status into a SetupError; None when it verified.

    Args:
        status: a status from verify_mcp_server.
        detail: the accompanying detail (the server's own words, when it had any).

    Returns:
        errors.SetupError (EMB-52), or None when status is "verified".
    """
    if status == "verified":
        return None
    said = f" It said: {detail}" if detail else ""
    return errors.SetupError(
        "EMB-52",
        f"The MCP server didn't start when I probed it ({status}).{said}",
        _VERIFY_FIXES.get(status, _VERIFY_FIXES["unknown"]),
    )


# Distinct from the project-scoped server in .mcp.json on purpose. Measured: the same
# name in two scopes makes the client print a standing "defined in multiple scopes with
# different endpoints" warning on every listing, and the project entry wins in-project
# anyway. Two names, no warning, and the scopes stay tellable apart.
USER_SCOPE_NAME = "embeddington-local"


def _mcp_add_argv(repo_root):
    """The registration command. Absolute paths are mandatory, not tidy: measured, a
    relative command under user scope is never spawned at all from another directory."""
    return [
        "claude",
        "mcp",
        "add",
        USER_SCOPE_NAME,
        "--scope",
        "user",
        "--",
        str(repo_root / ".venv" / "bin" / "python"),
        str(repo_root / "mcp" / "server.py"),
    ]


def user_scope_present(run):
    """True iff a user-scope registration under our name exists. Never raises."""
    return run(["claude", "mcp", "get", USER_SCOPE_NAME]).rc == 0


def register_user_scope(run, repo_root):
    """Point the user-scope registration at THIS clone; return whether it stuck.

    Remove-then-add, because `claude mcp add` refuses an existing name — and does so
    while exiting 0, so its return code proves nothing. Success is confirmed by reading
    the registration back, which is the only trustworthy signal available.

    Args:
        run: runner.run-compatible callable.
        repo_root: the clone root the registration should point at.

    Returns:
        True iff a registration exists afterwards.
    """
    run(["claude", "mcp", "remove", USER_SCOPE_NAME, "-s", "user"])  # absent is fine
    run(_mcp_add_argv(repo_root))
    return user_scope_present(run)


def remove_user_scope(run):
    """Drop the user-scope registration (uninstall). Never raises."""
    run(["claude", "mcp", "remove", USER_SCOPE_NAME, "-s", "user"])
    return not user_scope_present(run)


def offer_user_scope(console, run, repo_root, *, assume_yes, input_fn=input):
    """Offer to make the server reachable from every directory.

    An existing registration is refreshed silently rather than re-offered — the same
    never-re-nag contract the cron step follows. Unattended runs cannot consent, so they
    neither prompt nor register.

    Returns:
        "registered" | "refreshed" | "declined" | "skipped-unattended" | "failed".
    """
    if assume_yes:
        return "skipped-unattended"
    if user_scope_present(run):
        return "refreshed" if register_user_scope(run, repo_root) else "failed"
    if not ui.confirm(
        console,
        "Make the graph queryable from every directory, not just this clone?",
        default=True,
        input_fn=input_fn,
    ):
        return "declined"
    return "registered" if register_user_scope(run, repo_root) else "failed"


def refresh_user_scope(run, repo_root):
    """Update path: repoint an existing registration; never create one.

    Adding a registration is a consent-bearing act, and an unattended nightly update is
    the wrong place to perform one.

    Returns:
        "refreshed" | "absent" | "failed".
    """
    if not user_scope_present(run):
        return "absent"
    return "refreshed" if register_user_scope(run, repo_root) else "failed"


def mcp_deps_installed(run, repo_root):
    """True iff the server's dependencies import under the interpreter that runs it.

    Deliberately not `find_spec` from the wizard: that tests the WRONG interpreter, and
    the repo's own `mcp/` directory is importable as a namespace package, so it can
    report success with no SDK installed at all.
    """
    interpreter = repo_root / ".venv" / "bin" / "python"
    return run([str(interpreter), "-c", "import fastmcp"]).rc == 0


@dataclass(frozen=True)
class WiringResult:
    """What the Claude step actually accomplished, for the receipt to report honestly."""

    deps: str  # installed | skipped | failed | no-claude
    verify: str  # verified | deps | password | stack | ... | not-run
    # registered | refreshed | declined | skipped-unattended | failed | not-offered
    registration: str
