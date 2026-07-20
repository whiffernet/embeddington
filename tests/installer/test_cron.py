"""cron.py: primitives moved here + best-effort daemon detection."""

import io

from rich.console import Console

from installer import cron
from installer.errors import SetupError  # noqa: F401  (ensures EMB-62 stays registered)
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


def console():
    return Console(file=io.StringIO(), force_terminal=False, width=100)


class CronRun:
    """FakeRun that ALSO captures the crontab body at call time.

    install_cron writes a tempfile, runs `crontab <path>`, then unlinks it in a finally —
    so a read-after-return helper would find the file gone. This fake reads the file the
    instant `crontab <path>` is invoked (before the unlink), into ``self.written``.
    Results are a queue, returned in order like FakeRun.
    """

    def __init__(self, results=None):
        self.calls = []
        self.results = list(results or [])
        self.written = None

    def __call__(self, cmd, *, cwd=None, env=None, timeout=None, stream=False):
        self.calls.append({"cmd": list(cmd), "cwd": cwd, "stream": stream})
        if cmd[:1] == ["crontab"] and len(cmd) == 2 and cmd[1] != "-l":
            with open(cmd[1]) as fh:
                self.written = fh.read()  # capture the written body before unlink
        return self.results.pop(0) if self.results else RunResult(0, "", "")


def test_assume_yes_skips_without_touching_crontab():
    run = CronRun()

    def explode():
        raise AssertionError("assume_yes must not read input")

    out = cron.install_cron(console(), run, "/opt/emb", assume_yes=True, input_fn=explode)
    assert out == "skipped-unattended"
    assert run.calls == []  # no crontab -l, no write


def test_declined_writes_nothing_and_no_emb62():
    c = console()
    run = CronRun()
    out = cron.install_cron(c, run, "/opt/emb", assume_yes=False, input_fn=lambda: "n")
    assert out == "declined"
    assert not any(call["cmd"][:1] == ["crontab"] for call in run.calls)
    assert "EMB-62" not in c.file.getvalue()  # a declined user must not see a write error


def test_accepted_installs_exactly_one_line_daemon_up():
    # crontab -l (empty), crontab <file> ok, pgrep cron -> up
    run = CronRun(
        [RunResult(1, "", "no crontab for user"), RunResult(0, "", ""), RunResult(0, "1", "")]
    )
    out = cron.install_cron(console(), run, "/opt/emb", assume_yes=False, input_fn=lambda: "y")
    assert out == "installed"
    assert run.written.count("embeddington-consume") == 1
    assert run.written == cron.cron_line("/opt/emb") + "\n"  # exact body, no leading blank


def test_idempotent_refreshes_existing_line_and_keeps_foreign():
    existing = (
        "MAILTO=x\n0 6 * * * cd /OLD && .venv/bin/embeddington-consume update\n0 7 * * * backup\n"
    )
    run = CronRun([RunResult(0, existing, ""), RunResult(0, "", ""), RunResult(0, "1", "")])
    out = cron.install_cron(console(), run, "/opt/emb", assume_yes=False, input_fn=lambda: "y")
    assert out == "installed"
    # Exact body: foreign lines preserved in order, single refreshed embeddington line last,
    # no duplicate and no accumulated blank line (guards a dropped .rstrip("\n")).
    assert run.written == ("MAILTO=x\n0 7 * * * backup\n" + cron.cron_line("/opt/emb") + "\n")


def test_daemon_down_reports_installed_cron_down():
    # crontab -l ok(empty), write ok, pgrep cron/crond miss, systemctl inactive x2
    run = CronRun(
        [
            RunResult(0, "", ""),
            RunResult(0, "", ""),
            RunResult(1, "", ""),
            RunResult(1, "", ""),
            RunResult(3, "", ""),
            RunResult(3, "", ""),
        ]
    )
    out = cron.install_cron(console(), run, "/opt/emb", assume_yes=False, input_fn=lambda: "y")
    assert out == "installed-cron-down"


def test_no_crontab_binary_returns_no_crontab():
    run = CronRun([RunResult(127, "", "command not found: crontab")])
    out = cron.install_cron(console(), run, "/opt/emb", assume_yes=False, input_fn=lambda: "y")
    assert out == "no-crontab"
    # exactly the one `crontab -l` probe was made, and NO write (len-2 crontab call that isn't -l)
    assert run.calls == [{"cmd": ["crontab", "-l"], "cwd": None, "stream": False}]
    assert not any(
        c["cmd"][:1] == ["crontab"] and len(c["cmd"]) == 2 and c["cmd"][1] != "-l"
        for c in run.calls
    )


def test_write_failure_is_not_installed_and_warns_emb62():
    c = console()
    run = CronRun([RunResult(0, "", ""), RunResult(1, "", "crontab: cannot write")])
    out = cron.install_cron(c, run, "/opt/emb", assume_yes=False, input_fn=lambda: "y")
    assert out == "declined"
    assert "EMB-62" in c.file.getvalue()


def test_tempfile_is_cleaned_up_after_install():
    import os

    run = CronRun([RunResult(0, "", ""), RunResult(0, "", ""), RunResult(0, "1", "")])
    cron.install_cron(console(), run, "/opt/emb", assume_yes=False, input_fn=lambda: "y")
    write_calls = [
        c["cmd"][1]
        for c in run.calls
        if c["cmd"][:1] == ["crontab"] and len(c["cmd"]) == 2 and c["cmd"][1] != "-l"
    ]
    assert write_calls and not os.path.exists(write_calls[0])


def test_cron_line_present_detects_marker():
    present = cron.cron_line_present(
        lambda cmd: RunResult(0, "0 6 * * * cd /x && embeddington-consume update\n", "")
    )
    assert present is True

    absent = cron.cron_line_present(lambda cmd: RunResult(0, "0 5 * * * some-other-job\n", ""))
    assert absent is False

    no_crontab = cron.cron_line_present(lambda cmd: RunResult(1, "", "no crontab for user"))
    assert no_crontab is False

    no_binary = cron.cron_line_present(lambda cmd: RunResult(127, "", "command not found: crontab"))
    assert no_binary is False
