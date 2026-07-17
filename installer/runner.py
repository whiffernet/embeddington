"""Thin, injectable wrappers around subprocess and HTTP GET.

Every module takes `run` and/or `http_get` as parameters so tests inject fakes; these
are the only production implementations, and the only places the installer touches
subprocess or the network directly.
"""

import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class RunResult:
    """Result of a subprocess execution."""

    rc: int
    out: str
    err: str


def run(cmd, *, cwd=None, env=None, timeout=None, stream=False):
    """Run a command and return a RunResult.

    Args:
        cmd: argv list (never a shell string).
        cwd: working directory.
        env: environment mapping (default: inherit).
        timeout: seconds before subprocess.TimeoutExpired propagates.
        stream: when True, the child inherits stdout/stderr (live output for long
            builds); out/err come back empty.

    Returns:
        RunResult(rc, out, err).
    """
    # [CRITIC] A missing executable must be a RESULT, not an exception: subprocess.run
    # raises FileNotFoundError when the binary is absent, which would crash the wizard
    # with a traceback on any box without docker/crontab/git — the exact machines the
    # docker ladder and friendly EMB codes exist for.
    try:
        if stream:
            proc = subprocess.run(cmd, cwd=cwd, env=env, timeout=timeout)
            return RunResult(proc.returncode, "", "")
        proc = subprocess.run(
            cmd, cwd=cwd, env=env, timeout=timeout, capture_output=True, text=True
        )
        return RunResult(proc.returncode, proc.stdout, proc.stderr)
    except FileNotFoundError:
        return RunResult(127, "", f"command not found: {cmd[0]}")


def http_get(url, timeout=5):
    """GET a URL; return (status, body). HTTP error statuses return, only network fails raise.

    Returns:
        (status_code, body_text). A 401/404/500 comes back as its status — reachability
        and rejection are different answers here.

    Raises:
        OSError / urllib.error.URLError: connection refused, DNS failure, timeout.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
