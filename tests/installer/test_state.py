"""InstallState detection — pure reads, everything injected."""

from installer import state
from installer.runner import RunResult
from tests.installer.conftest import FakeRun


def detect(tmp_path, *, run=None, points=lambda: 1, entities=lambda: 1, find_spec=lambda n: None):
    env = {"EMBEDDINGTON_HOME": str(tmp_path / "state")}
    return state.detect_state(
        tmp_path,
        run or FakeRun([RunResult(0, "qdrant\narango\nembed\n", "")]),
        points,
        entities,
        env=env,
        home=tmp_path,
        find_spec=find_spec,
    )


def test_fresh_box_is_all_false(tmp_path):
    st = detect(
        tmp_path,
        run=FakeRun([RunResult(1, "", "no compose file")]),
        points=lambda: 0,
        entities=lambda: 0,
    )
    assert not st.env_present and not st.containers_running and not st.embed_running
    assert not st.stores_populated and not st.cursor_present and not st.mcp_deps


def test_missing_docker_binary_reads_as_not_running_not_a_crash(tmp_path):
    st = detect(
        tmp_path,
        run=FakeRun([RunResult(127, "", "command not found: docker")]),
        points=lambda: 0,
        entities=lambda: 0,
    )
    assert not st.containers_running and not st.embed_running


def test_full_install_is_all_true(tmp_path):
    (tmp_path / "consumer").mkdir()
    (tmp_path / "consumer" / ".env").write_text("ARANGO_ROOT_PASSWORD=x\n")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / ".cursor").write_text("abc123\n")
    st = detect(tmp_path, find_spec=lambda n: object())
    assert st.env_present and st.containers_running and st.embed_running
    assert st.stores_populated and st.cursor_present and st.mcp_deps


def test_one_populated_store_is_not_populated(tmp_path):
    # Pins the AND: a mutation to `or` would call a half-restored stack healthy.
    st = detect(tmp_path, points=lambda: 152_194, entities=lambda: 0)
    assert not st.stores_populated


def test_store_errors_read_as_not_populated(tmp_path):
    def boom():
        raise ConnectionError("daemon down")

    st = detect(tmp_path, points=boom, entities=boom)
    assert not st.stores_populated


def test_containers_need_both_qdrant_and_arango(tmp_path):
    st = detect(tmp_path, run=FakeRun([RunResult(0, "qdrant\n", "")]))
    assert not st.containers_running
