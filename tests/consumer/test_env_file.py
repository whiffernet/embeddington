import os
import stat

import pytest

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


# --- I3: the replace path must be atomic -- consumer/.env holds the only copy of --
# --- the Arango root password.                                                  --


def test_replace_survives_a_failure_mid_write_leaving_the_original_intact(tmp_path, monkeypatch):
    """A crash while writing the temp file's content (before os.replace ever runs)
    must leave the ORIGINAL file completely untouched -- not empty, not partial."""
    p = tmp_path / ".env"
    original = "ARANGO_ROOT_PASSWORD=sekrit\nFOO=old\n"
    p.write_text(original)

    real_fdopen = os.fdopen

    def boom_fdopen(fd, mode="r", *a, **k):
        handle = real_fdopen(fd, mode, *a, **k)

        class _BoomOnWrite:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                handle.close()

            def write(self, _data):
                raise OSError("disk full (simulated)")

        return _BoomOnWrite()

    monkeypatch.setattr(env_file.os, "fdopen", boom_fdopen)

    with pytest.raises(OSError, match="disk full"):
        env_file.set_key(p, "FOO", "new")

    assert p.read_text() == original, "the original file must survive a failed write untouched"
    # the temp file must not be left behind either
    leftovers = [f for f in tmp_path.iterdir() if f.name != ".env"]
    assert leftovers == [], f"a temp file was left behind: {leftovers}"


def test_replace_preserves_the_original_files_permission_bits(tmp_path):
    """consumer/.env is created 0600 (installer/stack.py); a key update must not
    loosen that."""
    p = tmp_path / ".env"
    p.write_text("ARANGO_ROOT_PASSWORD=sekrit\nFOO=old\n")
    os.chmod(p, 0o600)

    env_file.set_key(p, "FOO", "new")

    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_replace_writes_the_temp_file_in_the_same_directory(tmp_path, monkeypatch):
    """The rename must stay on one filesystem to actually be atomic -- a temp file in
    the system temp dir (a different filesystem on many setups) would make
    os.replace either fail across devices or silently stop being atomic."""
    p = tmp_path / ".env"
    p.write_text("FOO=old\n")
    seen_dirs = []

    real_mkstemp = env_file.tempfile.mkstemp

    def spy_mkstemp(*a, **k):
        seen_dirs.append(k.get("dir"))
        return real_mkstemp(*a, **k)

    monkeypatch.setattr(env_file.tempfile, "mkstemp", spy_mkstemp)

    env_file.set_key(p, "FOO", "new")

    assert seen_dirs == [str(tmp_path)]
