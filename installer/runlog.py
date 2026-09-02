"""A journal of every subprocess the wizard runs, so a failed install leaves evidence.

Until this existed the wizard wrote nothing anywhere: `install.sh` logs the pip/venv
bootstrap to <clone>/install.log, but the wizard that follows it printed to the terminal
and nowhere else. A user reporting "the installer didn't see my Docker" had no artifact
to send, and we had no way to tell which of several very different failures they had hit.

Design constraints, in priority order:

  1. It must never break an install. Every write is guarded; an unwritable state dir, a
     full disk, or a handle that dies mid-run degrades to a silent no-op journal. The
     subprocess still runs and its result is returned untouched.
  2. It must stay safe to share. argv is recorded verbatim, which is only acceptable
     because no installer subprocess carries a secret in argv — the generated ArangoDB
     root password is passed as a function argument and never reaches a command line.
     tests/installer/test_runlog.py pins that property so it can't quietly regress.
  3. It must not grow forever. The nightly job runs through the same entry point, so the
     file is trimmed to its trailing cap on open.

It rides `runner.run`, which is by design the only place in the installer that touches
subprocess — so wrapping that one callable captures everything, and `cli.main()` is the
single wiring point.
"""

import time
from pathlib import Path

LOG_NAME = "run.log"
SESSION_MARKER = "=== embeddington run"
MAX_BYTES = 1_000_000

# Streamed calls inherit the terminal and return empty out/err (see runner.run), so the
# journal marks them rather than recording a blank that reads as "produced no output".
STREAM_MARKER = "[streamed]"


class RunLog:
    """An open journal handle. `handle` is None when the destination was unusable."""

    def __init__(self, handle):
        self.handle = handle

    @property
    def enabled(self):
        """True while writes are actually landing somewhere."""
        return self.handle is not None

    def write(self, text):
        """Append a line, swallowing any I/O failure (rule 1)."""
        if self.handle is None:
            return
        try:
            self.handle.write(text + "\n")
            self.handle.flush()
        except (OSError, ValueError):
            # The disk can fill, or the handle can be closed, at any point in a long
            # install. Losing the journal is acceptable; losing the install is not.
            self.handle = None


def log_path(state_dir):
    """Where the journal lives for a given state directory."""
    return Path(state_dir) / LOG_NAME


def _trim(path, cap_bytes):
    """Keep the trailing cap_bytes, rounded up to a whole line. Best-effort."""
    try:
        if path.stat().st_size <= cap_bytes:
            return
        with path.open("rb") as handle:
            handle.seek(-cap_bytes, 2)
            tail = handle.read()
        # Drop the partial first line so the file never opens mid-record.
        _, _, whole = tail.partition(b"\n")
        path.write_bytes(whole)
    except OSError:
        pass


def open_log(state_dir, *, cap_bytes=MAX_BYTES, now=None):
    """Open (creating as needed) the journal for this run.

    Args:
        state_dir: the resolved state directory (consumer.state_paths.resolve_state_dir).
        cap_bytes: trim the existing file to this trailing size before appending.
        now: time.strftime-compatible timestamp string; injected in tests.

    Returns:
        RunLog. Always usable — a destination that can't be opened yields a journal whose
        writes are no-ops, never an exception.
    """
    path = log_path(state_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _trim(path, cap_bytes)
        handle = path.open("a", encoding="utf-8")
    except OSError:
        return RunLog(None)
    log = RunLog(handle)
    log.write(f"\n{SESSION_MARKER} {now or _stamp()} ===")
    return log


def _stamp():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def note(log, text):
    """Record a free-form line — an EMB code, a decision, a state transition."""
    if log is not None:
        log.write(f"{_stamp()}  {text}")


def wrap(run, log):
    """Return a runner.run-compatible callable that journals each call.

    Args:
        run: the callable to wrap (production: runner.run).
        log: a RunLog, or None to disable journaling entirely.

    Returns:
        A callable with runner.run's exact signature and return value. Failures record
        the stderr tail; successes record only the command, which keeps a long install
        from filling the file with noise nobody will read.
    """
    if log is None:
        return run

    def logged(cmd, **kwargs):
        result = run(cmd, **kwargs)
        marker = f" {STREAM_MARKER}" if kwargs.get("stream") else ""
        log.write(f"{_stamp()}  {' '.join(str(part) for part in cmd)}  rc={result.rc}{marker}")
        if result.rc != 0 and not kwargs.get("stream"):
            from installer.docker_probe import stderr_tail

            tail = stderr_tail(result.err) or stderr_tail(result.out)
            for line in tail.splitlines():
                log.write(f"    {line}")
        return result

    return logged
