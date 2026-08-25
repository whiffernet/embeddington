"""When the install last updated, and whether that is too long ago.

The point of this record is to turn a silent failure visible: every way the nightly trigger
can fail — no cron daemon, a macOS folder background jobs can't read, a laptop asleep at
06:00, a WSL2 distro shut down — ends as a machine that quietly stops updating.
"""

import json
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest

from installer import update_record
from installer.runner import RunResult
from tests.installer.conftest import FakeRun

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def _env(tmp_path):
    return {"EMBEDDINGTON_HOME": str(tmp_path / "state")}


def _describes(version="v0.11.12"):
    return FakeRun([RunResult(0, f"{version}\n", "")])


def test_records_time_version_and_mode(tmp_path):
    assert update_record.record_update(tmp_path, "diffs", _describes(), env=_env(tmp_path), now=NOW)
    written = json.loads((tmp_path / "state" / "last_update").read_text())
    assert written == {"at": "2026-08-25T12:00:00Z", "version": "v0.11.12", "mode": "diffs"}


def test_a_no_op_update_still_counts_as_a_run(tmp_path):
    """An install that checks in nightly and finds nothing new is healthy — it must not
    drift toward looking abandoned."""
    update_record.record_update(tmp_path, None, _describes(), env=_env(tmp_path), now=NOW)
    assert json.loads((tmp_path / "state" / "last_update").read_text())["mode"] == "none"


def test_version_comes_from_git_describe(tmp_path):
    run = _describes()
    update_record.record_update(tmp_path, "diffs", run, env=_env(tmp_path), now=NOW)
    assert run.calls[0]["cmd"][-3:] == ["describe", "--tags", "--always"]


def test_a_clone_without_git_metadata_records_unknown(tmp_path):
    run = FakeRun([RunResult(128, "", "not a git repository")])
    update_record.record_update(tmp_path, "diffs", run, env=_env(tmp_path), now=NOW)
    assert json.loads((tmp_path / "state" / "last_update").read_text())["version"] == "unknown"


def test_an_unwritable_state_dir_is_reported_not_raised(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    os.chmod(state, stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert (
            update_record.record_update(
                tmp_path, "diffs", _describes(), env=_env(tmp_path), now=NOW
            )
            is False
        )
    finally:
        os.chmod(state, stat.S_IRWXU)


def test_reading_back_what_was_written(tmp_path):
    update_record.record_update(tmp_path, "baseline", _describes(), env=_env(tmp_path), now=NOW)
    assert update_record.read_record(env=_env(tmp_path))["mode"] == "baseline"


@pytest.mark.parametrize("body", ["", "not json", '"a string"', "{}", '{"nope": 1}'])
def test_an_unusable_record_reads_as_absent(tmp_path, body):
    """Advisory signals must never be the reason something else fails."""
    state = tmp_path / "state"
    state.mkdir()
    (state / "last_update").write_text(body)
    assert update_record.read_record(env=_env(tmp_path)) is None


# --- classification --------------------------------------------------------


def _aged(days):
    at = (NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    return {"at": at, "version": "v0.1.0", "mode": "diffs"}


@pytest.mark.parametrize(
    "days,expected",
    [
        (0, "fresh"),
        (2, "fresh"),
        (6, "fresh"),
        (7, "stale"),
        (29, "stale"),
        (30, "very-stale"),
        (400, "very-stale"),
    ],
)
def test_tier_boundaries(days, expected):
    tier, since = update_record.staleness(_aged(days), now=NOW)
    assert tier == expected
    assert since == days


def test_no_record_is_unknown_not_an_alarm():
    """Every install predating this feature is here until its next run. Treating that as a
    fault would warn exactly the people who did nothing wrong."""
    assert update_record.staleness(None, now=NOW) == ("unknown", None)


def test_an_unparseable_timestamp_is_unknown():
    assert update_record.staleness({"at": "sometime last tuesday"}, now=NOW) == ("unknown", None)


def test_a_record_from_the_future_is_not_stale():
    """Clock skew and restored backups are not evidence that updates stopped."""
    tier, since = update_record.staleness(_aged(-5), now=NOW)
    assert tier == "fresh"
    assert since == 0


def test_a_naive_timestamp_is_read_as_utc():
    """Hand-edited or older records may lack an offset; that must not crash the doctor."""
    tier, _ = update_record.staleness({"at": "2026-08-25T11:00:00"}, now=NOW)
    assert tier == "fresh"
