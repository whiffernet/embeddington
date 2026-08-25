"""No credential may reach an error message, and therefore a log.

`subprocess.run(check=True)` raises CalledProcessError, whose string carries the ENTIRE
argv. The installer interpolates an unexpected exception into EMB-45, and the nightly job
redirects that into ~/embeddington-update.log — so one ordinary restore failure wrote the
ArangoDB root password, in plaintext, into a file that then sits there.
"""

import subprocess

import pytest

from consumer import restore_ops

PASSWORD = "correct-horse-battery-staple"


def _cmd():
    return [
        "docker",
        "run",
        "arangorestore",
        "--server.username",
        "root",
        "--server.password",
        PASSWORD,
        "--server.database",
        "technology_kg",
    ]


def test_redaction_replaces_only_the_secret():
    safe = restore_ops.redact_argv(_cmd(), ("--server.password",))
    assert PASSWORD not in safe
    assert safe[safe.index("--server.password") + 1] == restore_ops.REDACTED
    assert "root" in safe and "technology_kg" in safe, "only the secret is removed"


def test_a_flag_at_the_end_does_not_crash_the_redactor():
    assert restore_ops.redact_argv(["x", "--server.password"], ("--server.password",)) == [
        "x",
        "--server.password",
    ]


def test_a_failing_restore_never_names_the_password(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, b"", b"arangorestore: connection refused")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError) as exc:
        restore_ops._run_without_leaking_secrets(_cmd(), secret_flags=("--server.password",))

    rendered = f"{exc.value}{exc.value!r}"
    assert PASSWORD not in rendered
    assert restore_ops.REDACTED in rendered
    assert "connection refused" in rendered, "the useful half of the message survives"
    assert "exited 1" in rendered


def test_a_tool_echoing_the_password_back_is_scrubbed(monkeypatch):
    """arangorestore printing its own arguments must not become a leak by another route."""

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, b"", f"bad password: {PASSWORD}".encode())

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError) as exc:
        restore_ops._run_without_leaking_secrets(_cmd(), secret_flags=("--server.password",))
    assert PASSWORD not in str(exc.value)


def test_a_successful_run_returns_normally(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, b"ok", b"")
    )
    result = restore_ops._run_without_leaking_secrets(_cmd(), secret_flags=("--server.password",))
    assert result.returncode == 0


def test_the_real_restore_path_declares_the_password_flag(monkeypatch):
    """End to end through restore_arango_dump: a failure there must be redacted too."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, b"", b"boom"),
    )
    with pytest.raises(RuntimeError) as exc:
        restore_ops.restore_arango_dump(
            "http://localhost:8529", "technology_kg", "root", PASSWORD, "/tmp/dump"
        )
    assert PASSWORD not in str(exc.value)
    assert restore_ops.REDACTED in str(exc.value)
