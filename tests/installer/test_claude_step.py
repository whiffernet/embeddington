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
    assert got == "no-claude"


def test_declined_offer_is_skipped(tmp_path):
    got = claude_step.offer_claude_wiring(
        console(),
        FakeRun(),
        tmp_path,
        assume_yes=False,
        which=lambda n: "/usr/local/bin/claude",
        input_fn=lambda: "n",
    )
    assert got == "skipped"


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
    assert got == "installed"
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
    assert got == "failed"  # EMB-51 is shown, not raised


def test_assume_yes_installs_by_default(tmp_path):
    run = FakeRun([RunResult(0, "", "")])
    got = claude_step.offer_claude_wiring(
        console(), run, tmp_path, assume_yes=True, which=lambda n: "/usr/local/bin/claude"
    )
    assert got == "installed"


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
