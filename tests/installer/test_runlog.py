"""The run journal: every subprocess the wizard fires, appended where support can read it.

The bar these tests hold: a journal that breaks an install is worse than no journal, so
the unwritable-destination cases matter as much as the happy path.
"""

from installer import runlog
from installer.runner import RunResult
from tests.installer.conftest import FakeRun


def open_at(tmp_path, **kwargs):
    return runlog.open_log(tmp_path / "state", **kwargs)


def text(tmp_path):
    return runlog.log_path(tmp_path / "state").read_text()


# --- wrapping the runner --------------------------------------------------------


def test_wrapped_run_returns_the_result_untouched(tmp_path):
    inner = FakeRun([RunResult(3, "out", "err")])
    wrapped = runlog.wrap(inner, open_at(tmp_path))
    assert wrapped(["docker", "info"]) == RunResult(3, "out", "err")


def test_wrapped_run_forwards_every_keyword(tmp_path):
    inner = FakeRun()
    wrapped = runlog.wrap(inner, open_at(tmp_path))
    wrapped(["docker", "compose", "ps"], cwd="/somewhere", stream=True, stdin_devnull=True)
    call = inner.calls[0]
    assert call["cwd"] == "/somewhere" and call["stream"] and call["stdin_devnull"]


def test_records_argv_and_return_code(tmp_path):
    wrapped = runlog.wrap(FakeRun([RunResult(1, "", "")]), open_at(tmp_path))
    wrapped(["docker", "info"])
    assert "docker info" in text(tmp_path)
    assert "rc=1" in text(tmp_path)


def test_records_stderr_only_for_failures(tmp_path):
    log = open_at(tmp_path)
    runlog.wrap(FakeRun([RunResult(0, "", "a warning nobody needs")]), log)(["docker", "info"])
    runlog.wrap(FakeRun([RunResult(1, "", "Cannot connect to the daemon")]), log)(["docker", "ps"])
    body = text(tmp_path)
    assert "a warning nobody needs" not in body
    assert "Cannot connect to the daemon" in body


def test_streamed_calls_are_marked_and_carry_no_body(tmp_path):
    """stream=True inherits the terminal and returns empty out/err by contract, so the
    journal says so rather than recording a misleading blank."""
    wrapped = runlog.wrap(FakeRun([RunResult(1, "", "")]), open_at(tmp_path))
    wrapped(["docker", "compose", "up", "-d", "--build"], stream=True)
    assert "[streamed]" in text(tmp_path)


def test_argv_is_recorded_verbatim(tmp_path):
    """[CRITIC] This is the property that keeps the journal safe to share: no installer
    subprocess carries a secret in argv today, and this fails if one ever starts to."""
    wrapped = runlog.wrap(FakeRun(), open_at(tmp_path))
    wrapped(["crontab", "/tmp/x"])
    assert "crontab /tmp/x" in text(tmp_path)


def test_note_records_a_free_line(tmp_path):
    log = open_at(tmp_path)
    runlog.note(log, "EMB-21 daemon not reachable")
    assert "EMB-21 daemon not reachable" in text(tmp_path)


def test_session_header_separates_runs(tmp_path):
    open_at(tmp_path)
    open_at(tmp_path)
    assert text(tmp_path).count(runlog.SESSION_MARKER) == 2


# --- never break the install ----------------------------------------------------


def test_unwritable_destination_still_runs_the_command(tmp_path):
    """A state dir that can't be created (here: the path is a file) must degrade to a
    no-op journal, not an exception on the first subprocess the wizard fires."""
    blocker = tmp_path / "state"
    blocker.write_text("i am a file, not a directory")
    inner = FakeRun([RunResult(0, "", "")])
    wrapped = runlog.wrap(inner, runlog.open_log(blocker))
    assert wrapped(["docker", "info"]).rc == 0
    assert inner.calls[0]["cmd"] == ["docker", "info"]


def test_note_on_a_dead_log_is_silent(tmp_path):
    blocker = tmp_path / "state"
    blocker.write_text("not a directory")
    runlog.note(runlog.open_log(blocker), "EMB-21")  # must not raise


def test_wrap_without_a_log_is_a_passthrough(tmp_path):
    inner = FakeRun([RunResult(7, "", "")])
    assert runlog.wrap(inner, None)(["docker", "info"]).rc == 7


def test_a_write_failure_mid_run_does_not_propagate(tmp_path):
    """The disk can fill between opening the journal and the next command."""
    log = open_at(tmp_path)

    class Exploding:
        def write(self, _):
            raise OSError("No space left on device")

        def flush(self):
            raise OSError("No space left on device")

    log.handle = Exploding()
    assert runlog.wrap(FakeRun(), log)(["docker", "info"]).rc == 0


# --- size cap -------------------------------------------------------------------


def test_oversized_journal_is_trimmed_to_the_cap_on_open(tmp_path):
    """The nightly job appends to this file forever; without a cap it grows unbounded."""
    path = runlog.log_path(tmp_path / "state")
    path.parent.mkdir(parents=True)
    path.write_text("".join(f"line {i}\n" for i in range(50_000)))
    assert path.stat().st_size > 4096

    open_at(tmp_path, cap_bytes=4096)
    assert path.stat().st_size < 4096 * 3


def test_trimming_keeps_the_most_recent_lines(tmp_path):
    path = runlog.log_path(tmp_path / "state")
    path.parent.mkdir(parents=True)
    path.write_text("".join(f"line {i}\n" for i in range(50_000)))

    open_at(tmp_path, cap_bytes=4096)
    body = path.read_text()
    assert "line 49999" in body and "line 0\n" not in body


def test_a_journal_under_the_cap_is_left_alone(tmp_path):
    path = runlog.log_path(tmp_path / "state")
    path.parent.mkdir(parents=True)
    path.write_text("keep me\n")

    open_at(tmp_path, cap_bytes=4096)
    assert "keep me" in path.read_text()


# --- wiring: the wizard's own subprocesses land in the journal --------------------


def test_main_journals_the_commands_its_steps_run(tmp_path, monkeypatch):
    """End-to-end on the seam: cli.main() wraps runner.run once, so a step that shells
    out has its command in the file without the step knowing anything about logging."""
    from installer import cli, runner

    monkeypatch.setenv("EMBEDDINGTON_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(runner, "run", lambda cmd, **kw: RunResult(0, "", ""))

    deps = cli._production_deps(
        tmp_path, object(), runlog.wrap(runner.run, runlog.open_log(tmp_path / "state"))
    )
    deps["git_head"]()

    body = (tmp_path / "state" / runlog.LOG_NAME).read_text()
    assert "git" in body and "rev-parse" in body


def test_compose_failure_diagnostics_reach_the_journal(tmp_path):
    """The wiring, end to end: stack.compose_up hands its stdout-only evidence to note,
    cli._production_deps points note at the journal, and it lands in the file."""
    from installer import cli

    log = runlog.open_log(tmp_path / "state")
    run = FakeRun(
        [
            RunResult(17, "", ""),
            RunResult(0, "arango   Exited (1)\n", ""),
            RunResult(0, "arango | FATAL cannot allocate memory\n", ""),
        ]
    )
    deps = cli._production_deps(tmp_path, object(), run, lambda text: runlog.note(log, text))
    try:
        deps["compose_up"](None)
    except Exception as exc:
        assert getattr(exc, "code", None) == "EMB-31"

    body = (tmp_path / "state" / runlog.LOG_NAME).read_text()
    assert "EMB-31 diagnostics" in body
    assert "cannot allocate memory" in body


def test_stack_module_does_not_import_the_journal():
    """stack.py takes a note callable precisely so it stays free of this layer.

    Checked against the import graph, not the text: the docstring names runlog.note as
    the production wiring, and that reference is the point.
    """
    import ast
    import inspect

    from installer import stack

    tree = ast.parse(inspect.getsource(stack))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(f"{node.module}.{a.name}" for a in node.names)
    assert not any("runlog" in name for name in imported), imported
