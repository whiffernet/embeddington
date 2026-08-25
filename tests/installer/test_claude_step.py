"""Claude wiring: optional, never fatal."""

import io
import subprocess

from rich.console import Console

from installer import claude_step
from installer.runner import RunResult
from tests.installer.conftest import FakeRun


def console():
    return Console(file=io.StringIO(), force_terminal=False, width=100)


def test_no_claude_on_path_skips_quietly(tmp_path):
    got = claude_step.offer_claude_wiring(
        console(), FakeRun(), tmp_path, assume_yes=False, which=lambda n: None
    )
    assert got.deps == "no-claude"


def test_declined_offer_is_skipped(tmp_path):
    got = claude_step.offer_claude_wiring(
        console(),
        FakeRun(),
        tmp_path,
        assume_yes=False,
        which=lambda n: "/usr/local/bin/claude",
        input_fn=lambda: "n",
    )
    assert got.deps == "skipped"


def test_consented_offer_pip_installs_mcp_requirements(tmp_path):
    run = FakeRun([RunResult(0, "", "")])
    got = claude_step.offer_claude_wiring(
        console(),
        run,
        tmp_path,
        assume_yes=False,
        which=lambda n: "/usr/local/bin/claude",
        input_fn=lambda: "y",
    )
    assert got.deps == "installed"
    cmd = run.calls[0]["cmd"]
    assert cmd[-2:] == ["-r", str(tmp_path / "mcp" / "requirements.txt")]
    assert "pip" in " ".join(cmd)


def test_pip_failure_is_failed_not_fatal(tmp_path):
    run = FakeRun([RunResult(1, "", "resolver exploded")])
    got = claude_step.offer_claude_wiring(
        console(),
        run,
        tmp_path,
        assume_yes=False,
        which=lambda n: "/usr/local/bin/claude",
        input_fn=lambda: "y",
    )
    assert got.deps == "failed"  # EMB-51 is shown, not raised


def test_assume_yes_installs_by_default(tmp_path):
    run = FakeRun([RunResult(0, "", "")])
    got = claude_step.offer_claude_wiring(
        console(), run, tmp_path, assume_yes=True, which=lambda n: "/usr/local/bin/claude"
    )
    assert got.deps == "installed"


# --- startup verification (issue #85, Part D) -------------------------------
# Measured exit semantics of the real server, with stdin closed:
#   healthy                 -> rc 0 (transport reads EOF and shuts down cleanly)
#   interpreter has no deps -> rc 1, "ModuleNotFoundError: No module named ..."
#   no password anywhere    -> rc 1, "Missing required env var: ARANGO_PASSWORD ..."
# The client reports every one of these identically as "Connection closed", which is
# why the wizard has to run the spawn itself and quote what came back.


def test_verified_when_the_server_exits_cleanly(tmp_path):
    run = FakeRun([RunResult(0, "", "")])
    status, _ = claude_step.verify_mcp_server(run, tmp_path)
    assert status == "verified"


def test_verify_uses_the_clone_venv_and_closes_stdin(tmp_path):
    """Bare `python3` is the bug under test; and an inherited TTY stdin would make the
    server sit there waiting for a request that never comes, hanging the installer."""
    run = FakeRun([RunResult(0, "", "")])
    claude_step.verify_mcp_server(run, tmp_path)
    call = run.calls[0]
    assert call["cmd"][0] == str(tmp_path / ".venv" / "bin" / "python")
    assert call["cmd"][1:] == ["mcp/server.py"]
    assert call["cwd"] == tmp_path
    assert call["stdin_devnull"] is True


def test_missing_deps_are_named(tmp_path):
    run = FakeRun([RunResult(1, "", "ModuleNotFoundError: No module named 'dotenv'")])
    status, detail = claude_step.verify_mcp_server(run, tmp_path)
    assert status == "deps"
    assert "dotenv" in detail


def test_missing_password_is_named(tmp_path):
    err = "Missing required env var: ARANGO_PASSWORD must be set (via .env)."
    run = FakeRun([RunResult(1, "", err)])
    status, _ = claude_step.verify_mcp_server(run, tmp_path)
    assert status == "password"


def test_unreachable_stack_is_not_reported_as_a_wiring_fault(tmp_path):
    """The server refuses to start when Qdrant is unreachable. That is the stack being
    down, not the wiring being wrong, and saying otherwise sends the user to fix a file
    that is already correct."""
    run = FakeRun([RunResult(1, "", "Refusing to start:\n  Qdrant collection ...")])
    status, _ = claude_step.verify_mcp_server(run, tmp_path)
    assert status == "stack"


def test_absent_interpreter_is_named(tmp_path):
    run = FakeRun([RunResult(127, "", "command not found: .venv/bin/python")])
    status, _ = claude_step.verify_mcp_server(run, tmp_path)
    assert status == "no-interpreter"


def test_unknown_failure_surfaces_the_servers_own_stderr(tmp_path):
    run = FakeRun([RunResult(1, "", "AttributeError: something we have never seen")])
    status, detail = claude_step.verify_mcp_server(run, tmp_path)
    assert status == "unknown"
    assert "never seen" in detail


def test_a_hung_server_is_a_timeout_not_a_crash(tmp_path):
    def hang(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="python", timeout=1)

    status, _ = claude_step.verify_mcp_server(hang, tmp_path)
    assert status == "timeout"


def test_every_failure_status_maps_to_a_registered_error(tmp_path):
    for status in ("deps", "password", "stack", "no-interpreter", "timeout", "unknown"):
        err = claude_step.mcp_verification_error(status, "detail here")
        assert err.code == "EMB-52"
        assert err.fix  # every one tells the user what to do next
    assert claude_step.mcp_verification_error("verified", "") is None


# --- reach beyond the clone (issue #85, Part C) -----------------------------


def test_no_claude_cli_writes_no_configuration_at_all(tmp_path):
    """A Desktop user is served by server.py reading consumer/.env itself — there is
    nothing for the installer to write, and no second copy of the credential."""
    got = claude_step.offer_claude_wiring(
        console(), FakeRun(), tmp_path, assume_yes=False, which=lambda n: None
    )
    assert got.deps == "no-claude"
    assert not (tmp_path / "mcp" / ".env").exists()


def test_registration_is_not_offered_when_the_server_does_not_start(tmp_path):
    """Registering a server that cannot start just spreads the failure to every
    directory instead of one."""
    run = FakeRun([RunResult(0, "", ""), RunResult(1, "", "No module named 'fastmcp'")])
    got = claude_step.offer_claude_wiring(
        console(), run, tmp_path, assume_yes=True, which=lambda n: "/usr/local/bin/claude"
    )
    assert got.deps == "installed"
    assert got.verify == "deps"
    assert got.registration == "not-offered"


def test_registration_uses_absolute_paths(tmp_path):
    run = FakeRun([RunResult(1, "", "")])  # get -> absent, then remove/add/get default 0
    got = claude_step.offer_user_scope(
        console(), run, tmp_path, assume_yes=False, input_fn=lambda: "y"
    )
    assert got == "registered"
    add = [c for c in run.calls if c["cmd"][2:3] == ["add"]][0]["cmd"]
    assert add[-2] == str(tmp_path / ".venv" / "bin" / "python")
    assert add[-1] == str(tmp_path / "mcp" / "server.py")
    assert "--scope" in add and "user" in add
    assert all(not part.startswith(".venv") for part in add), "relative never spawns"


def test_existing_registration_is_refreshed_not_re_offered(tmp_path):
    """Never re-nag: same contract the cron step follows."""
    asked = []
    got = claude_step.offer_user_scope(
        console(),
        FakeRun(),
        tmp_path,
        assume_yes=False,
        input_fn=lambda: asked.append(1) or "n",
    )
    assert got == "refreshed"
    assert not asked


def test_unattended_never_registers(tmp_path):
    run = FakeRun()
    assert (
        claude_step.offer_user_scope(
            console(), run, tmp_path, assume_yes=True, input_fn=lambda: "y"
        )
        == "skipped-unattended"
    )
    assert not run.calls


def test_update_path_refreshes_but_never_creates_a_registration(tmp_path):
    """Adding one is a consent-bearing act; an unattended nightly update is the wrong
    place to perform it."""
    run = FakeRun([RunResult(1, "", "")])  # get -> absent
    assert claude_step.refresh_user_scope(run, tmp_path) == "absent"
    assert not [c for c in run.calls if c["cmd"][2:3] == ["add"]]


def test_update_path_does_not_probe_when_deps_were_never_installed(tmp_path):
    """A user who declined Claude wiring should not be warned about it every night."""
    run = FakeRun([RunResult(1, "", "No module named 'fastmcp'")])
    got = claude_step.ensure_claude_wiring(console(), run, tmp_path)
    assert got.deps == "absent"
    assert got.verify == "not-run"
    assert not [c for c in run.calls if c["cmd"][-1] == "mcp/server.py"]


def test_update_path_writes_no_configuration(tmp_path):
    """The repair now arrives with the code (.mcp.json's interpreter, server.py's
    password fallback), so this step never has to author a file — let alone one holding
    a credential."""
    claude_step.ensure_claude_wiring(console(), FakeRun(), tmp_path)
    assert not (tmp_path / "mcp" / ".env").exists()


def test_uninstall_removes_the_registration(tmp_path):
    run = FakeRun([RunResult(0, "", ""), RunResult(1, "", "")])  # remove ok, then absent
    assert claude_step.remove_user_scope(run) is True
    assert run.calls[0]["cmd"][:4] == ["claude", "mcp", "remove", claude_step.USER_SCOPE_NAME]
