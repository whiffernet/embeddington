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
