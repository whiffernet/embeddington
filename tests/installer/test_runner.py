"""runner.run contract: a missing executable is a result, not an exception."""

from installer import runner


def test_missing_executable_returns_rc_127_not_raise():
    result = runner.run(["definitely-not-a-real-binary-xyz"])
    assert result.rc == 127
    assert "command not found" in result.err


def test_missing_executable_streamed_also_returns_rc_127():
    result = runner.run(["definitely-not-a-real-binary-xyz"], stream=True)
    assert result.rc == 127
