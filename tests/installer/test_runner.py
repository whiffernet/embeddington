"""runner.run contract: a missing executable is a result, not an exception."""

from installer import runner


def test_missing_executable_returns_rc_127_not_raise():
    result = runner.run(["definitely-not-a-real-binary-xyz"])
    assert result.rc == 127
    assert "command not found" in result.err


def test_missing_executable_streamed_also_returns_rc_127():
    result = runner.run(["definitely-not-a-real-binary-xyz"], stream=True)
    assert result.rc == 127


def test_stdin_devnull_gives_the_child_eof_instead_of_our_terminal():
    """A stdio server probe must not inherit the wizard's stdin — it would block on a
    request that never comes. With /dev/null the child reads EOF immediately."""
    result = runner.run(
        ["python3", "-c", "import sys; sys.exit(0 if sys.stdin.read() == '' else 1)"],
        stdin_devnull=True,
        timeout=15,
    )
    assert result.rc == 0
