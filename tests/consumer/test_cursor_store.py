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
    cursor_store.write_cursor(p, "abc123")
    assert cursor_store.read_cursor(p) == "abc123"
    assert list(p.parent.iterdir()) == [p]  # no .tmp left behind
