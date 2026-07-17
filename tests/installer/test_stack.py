"""Stack step: env generation (password hygiene), compose, readiness waits."""

import pytest

from installer import errors, stack
from installer.runner import RunResult
from tests.installer.conftest import FakeHttp, FakeRun


def test_ensure_env_file_generates_600_and_never_echoes(tmp_path, capsys):
    env = stack.ensure_env_file(tmp_path, token_fn=lambda n: "sekrit-token")
    body = env.read_text()
    assert "ARANGO_ROOT_PASSWORD=sekrit-token" in body
    assert (env.stat().st_mode & 0o777) == 0o600
    assert "sekrit-token" not in capsys.readouterr().out


def test_ensure_env_file_never_overwrites_an_existing_one(tmp_path):
    existing = tmp_path / ".env"
    existing.write_text("ARANGO_ROOT_PASSWORD=old-password\n")
    stack.ensure_env_file(tmp_path, token_fn=lambda n: "new-password")
    assert "old-password" in existing.read_text()


def test_env_file_is_born_0600_via_o_excl(tmp_path, monkeypatch):
    """Pins the creation call itself: a write-then-chmod regression must fail here."""
    import os

    calls = {}
    real_open = os.open

    def spy(path, flags, mode=0o777):
        calls["flags"], calls["mode"] = flags, mode
        return real_open(path, flags, mode)

    monkeypatch.setattr(stack.os, "open", spy)
    stack.ensure_env_file(tmp_path, token_fn=lambda n: "t")
    assert calls["flags"] & os.O_EXCL
    assert calls["mode"] == 0o600


def test_read_password_returns_the_value(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# comment\nARANGO_ROOT_PASSWORD=hunter2\n")
    assert stack.read_password(f) == "hunter2"


@pytest.mark.parametrize(
    "body", ["", "OTHER=1\n", "ARANGO_ROOT_PASSWORD=\n", "ARANGO_ROOT_PASSWORD=change-me\n"]
)
def test_read_password_rejects_unusable_env_as_emb33(tmp_path, body):
    f = tmp_path / ".env"
    f.write_text(body)
    with pytest.raises(errors.SetupError) as exc:
        stack.read_password(f)
    assert exc.value.code == "EMB-33"


def test_read_password_missing_file_is_emb33_with_doesnt_exist_message(tmp_path):
    f = tmp_path / "missing.env"
    with pytest.raises(errors.SetupError) as exc:
        stack.read_password(f)
    assert exc.value.code == "EMB-33"
    assert "doesn't exist" in exc.value.friendly


def test_compose_up_streams_and_raises_emb31_on_failure(tmp_path):
    ok = FakeRun([RunResult(0, "", "")])
    stack.compose_up(ok, tmp_path)
    assert ok.calls[0]["cmd"][:4] == ["docker", "compose", "up", "-d"]
    assert ok.calls[0]["stream"] is True

    bad = FakeRun([RunResult(17, "", "")])
    with pytest.raises(errors.SetupError) as exc:
        stack.compose_up(bad, tmp_path)
    assert exc.value.code == "EMB-31"


def make_clock(step=5):
    t = {"now": 0}

    def clock():
        return t["now"]

    def sleep(s):
        t["now"] += s

    return clock, sleep


def buffer_console():
    import io

    from rich.console import Console

    return Console(file=io.StringIO(), force_terminal=False, width=100)


def test_wait_for_services_happy_path():
    http = FakeHttp(
        {
            ":6333/collections": (200, '{"result": {}}'),
            ":8529/_api/version": (401, ""),
            ":8100/": (200, "ok"),
        }
    )
    clock, sleep = make_clock()
    stack.wait_for_services(buffer_console(), http, sleep=sleep, clock=clock)  # no raise


def test_wait_for_services_store_timeout_is_emb31():
    http = FakeHttp({":8100/": (200, "ok")})  # stores never answer
    clock, sleep = make_clock()
    with pytest.raises(errors.SetupError) as exc:
        stack.wait_for_services(buffer_console(), http, sleep=sleep, clock=clock, store_timeout=30)
    assert exc.value.code == "EMB-31"


def test_wait_for_services_embed_timeout_is_emb32():
    http = FakeHttp(
        {":6333/collections": (200, '{"result": {}}'), ":8529/_api/version": (401, "")}
    )  # embed never answers
    clock, sleep = make_clock()
    with pytest.raises(errors.SetupError) as exc:
        stack.wait_for_services(
            buffer_console(), http, sleep=sleep, clock=clock, store_timeout=30, embed_timeout=60
        )
    assert exc.value.code == "EMB-32"
