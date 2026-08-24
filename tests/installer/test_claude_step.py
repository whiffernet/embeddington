"""Claude wiring: optional, never fatal."""

import io

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


# --- mcp/.env generation (issue #85, Part B) --------------------------------
# The server loads mcp/.env from its OWN directory, so this file is the only config
# that survives a launch from another directory, a GUI launch, or a shell that never
# sourced consumer/.env.

import os
import stat


def _consumer_env(tmp_path, password="s3cret-token-value"):
    (tmp_path / "consumer").mkdir(parents=True, exist_ok=True)
    (tmp_path / "consumer" / ".env").write_text(f"ARANGO_ROOT_PASSWORD={password}\n")
    (tmp_path / "mcp").mkdir(parents=True, exist_ok=True)
    return tmp_path / "mcp" / ".env"


def test_creates_mcp_env_from_consumer_env(tmp_path):
    target = _consumer_env(tmp_path)
    assert claude_step.ensure_mcp_env(tmp_path) == "created"
    body = target.read_text()
    assert "ARANGO_PASSWORD=s3cret-token-value" in body
    assert "ARANGO_DATABASE=technology_kg" in body
    assert "QDRANT_URL=http://localhost:6333" in body


def test_created_file_is_0600_from_birth(tmp_path):
    target = _consumer_env(tmp_path)
    claude_step.ensure_mcp_env(tmp_path)
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600


def test_never_overwrites_a_value_the_user_edited(tmp_path):
    target = _consumer_env(tmp_path)
    target.write_text("ARANGO_URL=http://elsewhere:8529\nARANGO_PASSWORD=mine\n")
    claude_step.ensure_mcp_env(tmp_path)
    body = target.read_text()
    assert "http://elsewhere:8529" in body
    assert "ARANGO_PASSWORD=mine" in body
    assert "s3cret-token-value" not in body


def test_fills_an_empty_password_left_by_copying_the_example(tmp_path):
    """`cp .env.example .env` leaves ARANGO_PASSWORD= present but empty — a key that
    merge-if-absent would skip, leaving the server unable to start."""
    target = _consumer_env(tmp_path)
    target.write_text("ARANGO_USER=root\nARANGO_PASSWORD=\n")
    assert claude_step.ensure_mcp_env(tmp_path) == "filled"
    assert "ARANGO_PASSWORD=s3cret-token-value" in target.read_text()


def test_complete_file_is_left_alone(tmp_path):
    target = _consumer_env(tmp_path)
    claude_step.ensure_mcp_env(tmp_path)
    before = target.read_text()
    assert claude_step.ensure_mcp_env(tmp_path) == "unchanged"
    assert target.read_text() == before


def test_missing_consumer_env_is_reported_not_raised(tmp_path):
    (tmp_path / "mcp").mkdir(parents=True)
    assert claude_step.ensure_mcp_env(tmp_path) == "no-password"
    assert not (tmp_path / "mcp" / ".env").exists()


# --- startup verification (issue #85, Part D) -------------------------------
# Measured exit semantics of the real server, with stdin closed:
#   healthy                 -> rc 0 (transport reads EOF and shuts down cleanly)
#   interpreter has no deps -> rc 1, "ModuleNotFoundError: No module named ..."
#   no password anywhere    -> rc 1, "Missing required env var: ARANGO_PASSWORD ..."
# The client reports every one of these identically as "Connection closed", which is
# why the wizard has to run the spawn itself and quote what came back.

import subprocess


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


def test_mcp_env_is_written_even_with_no_claude_cli(tmp_path):
    """A Claude Desktop user has no CLI on PATH and needs mcp/.env MORE than anyone —
    a GUI app inherits no shell exports at all."""
    _consumer_env(tmp_path)
    got = claude_step.offer_claude_wiring(
        console(), FakeRun(), tmp_path, assume_yes=False, which=lambda n: None
    )
    assert got.deps == "no-claude"
    assert got.env == "created"
    assert "ARANGO_PASSWORD=s3cret-token-value" in (tmp_path / "mcp" / ".env").read_text()


def test_registration_is_not_offered_when_the_server_does_not_start(tmp_path):
    """Registering a server that cannot start just spreads the failure to every
    directory instead of one."""
    _consumer_env(tmp_path)
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
        console(), FakeRun(), tmp_path, assume_yes=False,
        input_fn=lambda: asked.append(1) or "n",
    )
    assert got == "refreshed"
    assert not asked


def test_unattended_never_registers(tmp_path):
    run = FakeRun()
    assert claude_step.offer_user_scope(
        console(), run, tmp_path, assume_yes=True, input_fn=lambda: "y"
    ) == "skipped-unattended"
    assert not run.calls


def test_update_path_refreshes_but_never_creates_a_registration(tmp_path):
    """Adding one is a consent-bearing act; an unattended nightly update is the wrong
    place to perform it."""
    run = FakeRun([RunResult(1, "", "")])  # get -> absent
    assert claude_step.refresh_user_scope(run, tmp_path) == "absent"
    assert not [c for c in run.calls if c["cmd"][2:3] == ["add"]]


def test_update_path_does_not_probe_when_deps_were_never_installed(tmp_path):
    """A user who declined Claude wiring should not be warned about it every night."""
    _consumer_env(tmp_path)
    run = FakeRun([RunResult(1, "", "No module named 'fastmcp'")])
    got = claude_step.ensure_claude_wiring(console(), run, tmp_path)
    assert got.deps == "absent"
    assert got.verify == "not-run"
    assert not [c for c in run.calls if c["cmd"][-1] == "mcp/server.py"]


def test_update_path_writes_a_missing_mcp_env(tmp_path):
    """This is the line that repairs an install broken by the old configuration, with
    nobody doing anything."""
    _consumer_env(tmp_path)
    got = claude_step.ensure_claude_wiring(console(), FakeRun(), tmp_path)
    assert got.env == "created"
    assert (tmp_path / "mcp" / ".env").exists()


def test_uninstall_removes_the_registration(tmp_path):
    run = FakeRun([RunResult(0, "", ""), RunResult(1, "", "")])  # remove ok, then absent
    assert claude_step.remove_user_scope(run) is True
    assert run.calls[0]["cmd"][:4] == ["claude", "mcp", "remove", claude_step.USER_SCOPE_NAME]
