"""The breadcrumb install.sh reads to find an existing clone.

Without it the bootstrap defaults its location prompt to $HOME/embeddington regardless of
where the user actually installed — and pressing Enter builds a second clone whose fresh
random password cannot open the first one's ArangoDB volume.
"""

import os
import stat

from installer import install_record


def test_records_the_absolute_clone_path(tmp_path):
    state = tmp_path / "state"
    clone = tmp_path / "clone"
    clone.mkdir()
    assert install_record.record_install_path(clone, env={"EMBEDDINGTON_HOME": str(state)})
    assert (state / "install_path").read_text().strip() == str(clone.resolve())


def test_creates_the_state_dir_when_absent(tmp_path):
    state = tmp_path / "not-yet"
    assert install_record.record_install_path(tmp_path, env={"EMBEDDINGTON_HOME": str(state)})
    assert (state / "install_path").exists()


def test_rewriting_replaces_rather_than_appends(tmp_path):
    """A moved clone must leave ONE path behind, not a history the bootstrap has to guess
    between."""
    env = {"EMBEDDINGTON_HOME": str(tmp_path / "state")}
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    install_record.record_install_path(first, env=env)
    install_record.record_install_path(second, env=env)
    body = (tmp_path / "state" / "install_path").read_text()
    assert body.strip() == str(second.resolve())
    assert str(first.resolve()) not in body


def test_follows_the_same_ladder_as_every_other_state_file(tmp_path):
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    override = tmp_path / "override"

    assert install_record.pointer_path(env={}, home=home) == (
        home / ".local" / "share" / "embeddington" / "install_path"
    )
    assert install_record.pointer_path(env={"XDG_DATA_HOME": str(xdg)}, home=home) == (
        xdg / "embeddington" / "install_path"
    )
    assert install_record.pointer_path(
        env={"EMBEDDINGTON_HOME": str(override), "XDG_DATA_HOME": str(xdg)}, home=home
    ) == (override / "install_path")


def test_an_unwritable_state_dir_is_reported_not_raised(tmp_path):
    """A convenience for the next run must never fail a run that otherwise worked."""
    state = tmp_path / "readonly"
    state.mkdir()
    os.chmod(state, stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert (
            install_record.record_install_path(tmp_path, env={"EMBEDDINGTON_HOME": str(state)})
            is False
        )
    finally:
        os.chmod(state, stat.S_IRWXU)
