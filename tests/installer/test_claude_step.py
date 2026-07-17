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
