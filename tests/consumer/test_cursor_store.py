from unittest import mock

import pytest

from consumer import cursor_store


def test_read_absent_cursor_is_none(tmp_path):
    assert cursor_store.read_cursor(tmp_path / ".cursor") is None


def test_write_then_read_roundtrip(tmp_path):
    p = tmp_path / ".cursor"
    cursor_store.write_cursor(p, "c3d4")
    assert cursor_store.read_cursor(p) == "c3d4"


def test_write_overwrites_and_strips(tmp_path):
    p = tmp_path / ".cursor"
    cursor_store.write_cursor(p, "c3d4")
    cursor_store.write_cursor(p, "e5f6\n")
    assert cursor_store.read_cursor(p) == "e5f6"


def test_blank_cursor_file_reads_as_none(tmp_path):
    """A 0-byte or whitespace cursor is NOT a valid sha.

    It used to read back as "", which is not None -- so it slipped past every
    `if cursor is None` seam and silently triggered a full baseline re-download.
    """
    p = tmp_path / ".cursor"
    p.write_text("", encoding="utf-8")
    assert cursor_store.read_cursor(p) is None

    p.write_text("  \n ", encoding="utf-8")
    assert cursor_store.read_cursor(p) is None


def test_write_cursor_is_atomic_and_leaves_no_temp_file(tmp_path):
    p = tmp_path / "state" / ".cursor"

    with mock.patch("consumer.cursor_store.os.replace", wraps=cursor_store.os.replace) as spy:
        cursor_store.write_cursor(p, "abc123")

    # Pin the MECHANISM: the write must go through os.replace(tmp, dest), not a
    # direct write to the destination -- that's what makes it atomic.
    assert spy.call_count == 1
    (tmp_arg, dest_arg), _ = spy.call_args
    assert tmp_arg != dest_arg
    assert dest_arg == p

    # End state: content correct, no leftover .tmp file.
    assert cursor_store.read_cursor(p) == "abc123"
    assert list(p.parent.iterdir()) == [p]  # no .tmp left behind


def test_write_cursor_leaves_original_untouched_when_replace_fails(tmp_path):
    """The real safety property: a failed swap must never touch the destination.

    If os.replace() blows up partway (disk full, permissions, etc.), the destination
    must still hold its pre-write value rather than being truncated or left half-written
    -- a torn/blank cursor silently triggers a full 828 MB baseline re-download.
    """
    p = tmp_path / ".cursor"
    cursor_store.write_cursor(p, "original-sha")

    with mock.patch("consumer.cursor_store.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            cursor_store.write_cursor(p, "new-sha")

    assert cursor_store.read_cursor(p) == "original-sha"
