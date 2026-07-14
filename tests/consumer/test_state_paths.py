"""The state-location resolution ladder, and discovery of pre-v0.2 cursors."""

from pathlib import Path

from consumer import state_paths


def test_embeddington_home_wins_over_xdg(tmp_path):
    env = {"EMBEDDINGTON_HOME": "/opt/emb", "XDG_DATA_HOME": "/xdg"}
    assert state_paths.resolve_state_dir(env, tmp_path) == Path("/opt/emb")


def test_xdg_data_home_used_when_set(tmp_path):
    assert state_paths.resolve_state_dir({"XDG_DATA_HOME": "/xdg"}, tmp_path) == Path(
        "/xdg/embeddington"
    )


def test_default_is_local_share_under_home(tmp_path):
    assert state_paths.resolve_state_dir({}, tmp_path) == tmp_path / ".local/share/embeddington"


def test_cursor_and_work_dir_hang_off_state_dir(tmp_path):
    assert state_paths.default_cursor_path({}, tmp_path) == (
        tmp_path / ".local/share/embeddington/.cursor"
    )
    assert state_paths.default_work_dir({}, tmp_path) == (
        tmp_path / ".local/share/embeddington/work"
    )


def test_install_root_is_the_dir_containing_the_consumer_package():
    # consumer/state_paths.py -> consumer/ -> <repo root>
    assert (state_paths.install_root() / "consumer" / "state_paths.py").exists()


def test_install_root_does_not_depend_on_cwd(monkeypatch, tmp_path):
    """The regression this guards: install_root() must not resolve via Path.cwd().

    A broken `def install_root(): return Path.cwd()` would also satisfy the assertion
    above (pytest runs from the repo root), so pin cwd-independence directly: the value
    must be identical before and after chdir-ing somewhere unrelated.
    """
    before = state_paths.install_root()

    monkeypatch.chdir(tmp_path)
    after = state_paths.install_root()

    assert after == before
    assert (after / "consumer" / "state_paths.py").exists()


def test_legacy_candidates_finds_the_install_root_from_an_unrelated_cwd(tmp_path):
    """The migration must NOT depend on the working directory (that is the bug)."""
    clone, elsewhere, home = tmp_path / "clone", tmp_path / "elsewhere", tmp_path / "home"
    (clone / "data").mkdir(parents=True)
    (clone / "data" / ".cursor").write_text("abc")
    elsewhere.mkdir()
    home.mkdir()

    found = state_paths.legacy_cursor_candidates(elsewhere, home, install_root_dir=clone)

    assert found == [clone / "data" / ".cursor"]  # found despite cwd being unrelated


def test_legacy_candidates_finds_install_root_cursor_with_no_override(monkeypatch, tmp_path):
    """Exercises the real production path: no install_root_dir override.

    A pre-v0.2 cursor placed at the real install_root()/data/.cursor must still be found
    when called from an unrelated cwd -- this is the actual cron scenario, not a stand-in
    for it. The fixture file lives inside the real repo (data/ is gitignored), so it is
    created and removed within the test.
    """
    cursor_path = state_paths.install_root() / "data" / ".cursor"
    assert not cursor_path.exists(), "stray data/.cursor already present in repo"

    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    cursor_path.write_text("abc")
    try:
        elsewhere, home = tmp_path / "elsewhere", tmp_path / "home"
        elsewhere.mkdir()
        home.mkdir()
        monkeypatch.chdir(elsewhere)

        found = state_paths.legacy_cursor_candidates(elsewhere, home)

        assert found == [cursor_path]
    finally:
        cursor_path.unlink()
        try:
            cursor_path.parent.rmdir()
        except OSError:
            pass  # data/ pre-existed or holds other files; leave it alone


def test_legacy_candidates_orders_cwd_first_then_install_root_then_home(tmp_path):
    cwd, clone, home = tmp_path / "cwd", tmp_path / "clone", tmp_path / "home"
    for base in (cwd, clone, home):
        (base / "data").mkdir(parents=True)
        (base / "data" / ".cursor").write_text("abc")

    found = state_paths.legacy_cursor_candidates(cwd, home, install_root_dir=clone)

    assert found == [
        cwd / "data" / ".cursor",
        clone / "data" / ".cursor",
        home / "data" / ".cursor",
    ]


def test_legacy_candidates_returns_only_existing_paths(tmp_path):
    cwd, home = tmp_path / "clone", tmp_path / "home"
    (cwd / "data").mkdir(parents=True)
    (cwd / "data" / ".cursor").write_text("abc")
    home.mkdir()

    found = state_paths.legacy_cursor_candidates(cwd, home, install_root_dir=tmp_path / "nope")

    assert found == [cwd / "data" / ".cursor"]


def test_legacy_candidates_dedupes_when_the_paths_coincide(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / ".cursor").write_text("abc")

    found = state_paths.legacy_cursor_candidates(tmp_path, tmp_path, install_root_dir=tmp_path)

    assert found == [tmp_path / "data" / ".cursor"]
