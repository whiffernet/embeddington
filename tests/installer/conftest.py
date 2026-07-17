"""Shared fakes for installer tests. No real subprocess/network/docker anywhere."""

import pytest

from installer.runner import RunResult


class FakeRun:
    """Records commands; replies from a queue of RunResults (default rc=0)."""

    def __init__(self, results=None):
        self.calls = []
        self.results = list(results or [])

    def __call__(self, cmd, *, cwd=None, env=None, timeout=None, stream=False):
        self.calls.append({"cmd": list(cmd), "cwd": cwd, "stream": stream})
        if self.results:
            return self.results.pop(0)
        return RunResult(0, "", "")


class FakeHttp:
    """Maps url-substring -> (status, body); anything unmapped raises OSError."""

    def __init__(self, responses=None):
        self.responses = dict(responses or {})
        self.urls = []

    def __call__(self, url, timeout=5):
        self.urls.append(url)
        for fragment, reply in self.responses.items():
            if fragment in url:
                if isinstance(reply, Exception):
                    raise reply
                return reply
        raise OSError(f"connection refused: {url}")


@pytest.fixture
def fake_run():
    return FakeRun()


@pytest.fixture
def fake_http():
    return FakeHttp()
