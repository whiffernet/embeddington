"""When this install last successfully updated, and whether that was too long ago.

The self-update machinery is not what fails — the trigger is, in ways nobody sees: a cron
daemon that isn't running, a macOS folder background jobs can't read, a laptop asleep at
06:00 (cron skips, it does not catch up), a WSL2 distro shut down when idle. Each ends the
same way: a machine that quietly stops receiving updates and says nothing about it.

Chasing every cause means a scheduler per platform and still misses the ones nobody has
thought of. Recording when the machinery last ran catches all of them, including the
unknown ones, for a fraction of the code — it converts a silent failure into a visible one.

Nothing here reports anywhere. The record is local, written locally, read locally.
"""

import json
from datetime import datetime, timezone

from consumer import state_paths

RECORD_NAME = "last_update"

# A working nightly job leaves the record under 2 days old, so a 7-day gap is roughly five
# missed runs — a real signal, with slack for a machine that was simply switched off.
STALE_AFTER_DAYS = 7
VERY_STALE_AFTER_DAYS = 30


def record_path(env=None, home=None):
    """Where the record lives, following the same ladder as every other state file."""
    import os
    from pathlib import Path

    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    return state_paths.resolve_state_dir(env, home) / RECORD_NAME


def clone_version(repo_root, run):
    """The release this clone is on, e.g. "v0.11.12", or "unknown".

    `git describe --tags --always`, deliberately NOT importlib.metadata: pyproject declares
    0.3.0 while the shipped release is v0.11.x (issue #70), so package metadata would report
    a wrong version into exactly the support conversations this field exists to serve.
    Verified against the `--depth 1` clone install.sh creates — the tag pointing at HEAD is
    fetched, so describe returns the real release. `--always` degrades to a short SHA rather
    than failing on a clone with no tags.

    Args:
        repo_root: the clone root.
        run: runner.run-compatible callable.

    Returns:
        The description, or "unknown" when there is no usable git metadata.
    """
    res = run(["git", "-C", str(repo_root), "describe", "--tags", "--always"])
    described = res.out.strip() if res.rc == 0 else ""
    return described or "unknown"


def record_update(repo_root, mode, run, *, pull_ok=None, env=None, home=None, now=None):
    """Record a successful update run; report whether it stuck. Never raises.

    A no-op update still refreshes the timestamp: the signal is "the machinery ran", not
    "data changed". An install that checks in nightly and finds nothing new is healthy, and
    must not drift toward looking abandoned.

    Args:
        repo_root: the clone root.
        mode: the updater's own result ("baseline" / "diffs" / anything falsy for a no-op).
        run: runner.run-compatible callable, for the version lookup.
        pull_ok: whether the code half of the update landed. False records that this run
            updated data while remaining stuck on old code — a state that otherwise looks
            identical to a healthy update, because the data really did move. None means the
            flow does not pull at all (the install path; install.sh pulled before it ran).
        env / home: forwarded to `record_path` (injected in tests).
        now: datetime override (injected in tests).

    Returns:
        True iff the record was written.
    """
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    payload = {
        "at": stamp.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "version": clone_version(repo_root, run),
        "mode": mode or "none",
    }
    if pull_ok is not None:
        payload["pull"] = "ok" if pull_ok else "failed"
    target = record_path(env=env, home=home)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload) + "\n")
        return True
    except OSError:
        return False


def read_record(env=None, home=None):
    """The last recorded update, or None when there isn't one or it can't be read.

    A corrupt or truncated file reads as absent rather than raising: this is an advisory
    signal, and it must never be the reason something else fails.
    """
    try:
        data = json.loads(record_path(env=env, home=home).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and "at" in data else None


def staleness(record, now=None):
    """Classify a record into (tier, days_since).

    Tiers: "unknown" (nothing recorded), "fresh", "stale", "very-stale".

    "unknown" is deliberately NOT an alarm. Every install predating this feature is in that
    state until its next run, and treating it as a fault would warn precisely the people who
    did nothing wrong.

    Args:
        record: a dict from `read_record`, or None.
        now: datetime override (injected in tests).

    Returns:
        (tier, days_since) — days_since is None for "unknown".
    """
    if not record:
        return "unknown", None
    try:
        at = datetime.fromisoformat(str(record["at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return "unknown", None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    # A record from the future (clock skew, a restored backup) is not evidence of staleness.
    days = max(0, int((current - at).total_seconds() // 86400))
    if days >= VERY_STALE_AFTER_DAYS:
        return "very-stale", days
    if days >= STALE_AFTER_DAYS:
        return "stale", days
    return "fresh", days


def code_is_stuck(record):
    """True when the last run updated data but could not pull new code.

    Time-based staleness cannot see this: the job runs nightly, the data moves, the
    timestamp stays fresh — and the clone sits on old code indefinitely because
    `git pull --ff-only` fails every time (a local edit, a detached HEAD, a diverged
    branch). The only trace is this flag.
    """
    return bool(record) and record.get("pull") == "failed"
