"""cron.py: primitives moved here + best-effort daemon detection."""

from installer import cron
from installer.runner import RunResult
from tests.installer.conftest import FakeRun


def test_cron_line_built_from_repo_root():
    line = cron.cron_line("/opt/emb")
    assert line.startswith("0 6 * * * cd /opt/emb &&")
    assert ".venv/bin/embeddington-consume update" in line
    assert "$HOME/embeddington-update.log" in line


def test_strip_removes_only_embeddington_lines():
    tab = "MAILTO=x\n0 6 * * * cd /a && .venv/bin/embeddington-consume update\n0 7 * * * backup\n"
    out = cron.strip_cron_lines(tab)
    assert "embeddington-consume" not in out
    assert "MAILTO=x" in out and "backup" in out


def test_daemon_running_true_when_pgrep_cron_hits():
    run = FakeRun([RunResult(0, "1234", "")])  # pgrep -x cron -> found
    assert cron.cron_daemon_running(run) is True


def test_daemon_running_true_via_crond():
    # pgrep cron miss, pgrep crond hit
    run = FakeRun([RunResult(1, "", ""), RunResult(0, "999", "")])
    assert cron.cron_daemon_running(run) is True


def test_daemon_running_falls_back_to_systemctl():
    # pgrep absent (127) x2, then systemctl is-active cron -> active (rc 0)
    run = FakeRun([RunResult(127, "", ""), RunResult(127, "", ""), RunResult(0, "active", "")])
    assert cron.cron_daemon_running(run) is True


def test_daemon_running_false_when_nothing_confirms():
    run = FakeRun([RunResult(1, "", "")] * 4)  # pgrep x2 miss, systemctl x2 inactive
    assert cron.cron_daemon_running(run) is False
