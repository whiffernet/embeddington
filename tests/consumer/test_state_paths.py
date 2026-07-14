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


def test_legacy_candidates_finds_the_install_root_from_an_unrelated_cwd(tmp_path):
    """The migration must NOT depend on the working directory (that is the bug)."""
    clone, elsewhere, home = tmp_path / "clone", tmp_path / "elsewhere", tmp_path / "home"
    (clone / "data").mkdir(parents=True)
    (clone / "data" / ".cursor").write_text("abc")
    elsewhere.mkdir()
    home.mkdir()

    found = state_paths.legacy_cursor_candidates(elsewhere, home, install_root_dir=clone)

    assert found == [clone / "data" / ".cursor"]  # found despite cwd being unrelated


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
