from consumer import env_file


def test_read_key_missing_file_returns_default(tmp_path):
    assert env_file.read_key(tmp_path / "nope.env", "FOO", default="fallback") == "fallback"


def test_read_key_absent_key_returns_default(tmp_path):
    p = tmp_path / ".env"
    p.write_text("OTHER=1\n")
    assert env_file.read_key(p, "FOO", default="fallback") == "fallback"


def test_read_key_empty_value_returns_default(tmp_path):
    """`cp .env.example .env` shape: a present-but-empty assignment counts as unset."""
    p = tmp_path / ".env"
    p.write_text("FOO=\n")
    assert env_file.read_key(p, "FOO", default="fallback") == "fallback"


def test_read_key_returns_stripped_value(tmp_path):
    p = tmp_path / ".env"
    p.write_text("FOO=  bar  \n")
    assert env_file.read_key(p, "FOO") == "bar"


def test_set_key_creates_missing_file(tmp_path):
    p = tmp_path / ".env"
    env_file.set_key(p, "FOO", "bar")
    assert p.read_text() == "FOO=bar\n"


def test_set_key_appends_when_absent(tmp_path):
    p = tmp_path / ".env"
    p.write_text("OTHER=1\n")
    env_file.set_key(p, "FOO", "bar")
    assert p.read_text() == "OTHER=1\nFOO=bar\n"


def test_set_key_appends_with_newline_when_file_lacks_trailing_newline(tmp_path):
    p = tmp_path / ".env"
    p.write_text("OTHER=1")
    env_file.set_key(p, "FOO", "bar")
    assert p.read_text() == "OTHER=1\nFOO=bar\n"


def test_set_key_replaces_existing_value_in_place(tmp_path):
    p = tmp_path / ".env"
    p.write_text("BEFORE=1\nFOO=old\nAFTER=2\n")
    env_file.set_key(p, "FOO", "new")
    assert p.read_text() == "BEFORE=1\nFOO=new\nAFTER=2\n"


def test_set_key_then_read_key_round_trips(tmp_path):
    p = tmp_path / ".env"
    env_file.set_key(p, "EMBEDDINGTON_KG_SCHEMA", "v3")
    assert env_file.read_key(p, "EMBEDDINGTON_KG_SCHEMA") == "v3"
