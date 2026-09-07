"""Import step: exception mapping to EMB codes and proof-of-life."""

import io
import urllib.error

import pytest
from rich.console import Console

from consumer import updater
from embeddington.errors import ChainGapError, ChecksumError
from installer import errors, import_step


def console():
    return Console(file=io.StringIO(), force_terminal=False, width=100)


def run_with(update_fn):
    """Drive run_import with fake wiring — no network, no stores."""

    def wiring(repo_root, password, repo):
        return ("rc", "qdrant", "arango", "importer")

    return import_step.run_import(
        console(),
        "/tmp/nowhere",
        "pw",
        env={"EMBEDDINGTON_HOME": "/tmp/nowhere/state"},
        home="/tmp/nowhere",
        cwd="/tmp/nowhere",
        update_fn=update_fn,
        wiring_fn=wiring,
    )


def test_success_returns_the_updater_result():
    result = {
        "mode": "diffs",
        "applied": 3,
        "cursor": "abc",
        "baseline": None,
        "adopted_from": None,
    }
    assert run_with(lambda *a, **k: result) == result


def test_force_baseline_is_forwarded():
    seen = {}

    def update_fn(*args, **kwargs):
        seen.update(kwargs)
        return {
            "mode": "baseline",
            "applied": 0,
            "cursor": "x",
            "baseline": {},
            "adopted_from": None,
        }

    import_step.run_import(
        console(),
        "/tmp/nowhere",
        "pw",
        force_baseline=True,
        env={"EMBEDDINGTON_HOME": "/tmp/s"},
        home="/tmp",
        cwd="/tmp",
        update_fn=update_fn,
        wiring_fn=lambda *a: (1, 2, 3, 4),
    )
    assert seen["force_baseline"] is True


@pytest.mark.parametrize(
    "raised, expected_code",
    [
        (updater.BaselineRefused("guard says no"), "EMB-43"),
        (updater.BaselineRequired("need importer"), "EMB-45"),
        (ChainGapError("gap"), "EMB-45"),
        (ChecksumError("bad sha"), "EMB-42"),
        (urllib.error.URLError("boom"), "EMB-41"),
    ],
)
def test_updater_exceptions_map_to_emb_codes(raised, expected_code):
    def update_fn(*a, **k):
        raise raised

    with pytest.raises(errors.SetupError) as exc:
        run_with(update_fn)
    assert exc.value.code == expected_code


def test_run_import_schema_error_gets_upgrade_text():
    from embeddington import SchemaVersionError
    from installer.errors import SetupError

    def boom(*a, **k):
        raise SchemaVersionError("manifest schema major 2 exceeds supported 1")

    with pytest.raises(SetupError) as exc_info:
        run_with(boom)
    assert exc_info.value.code == "EMB-45"
    assert "pulls new code" in exc_info.value.fix


def test_run_import_passes_ensure_index_to_updater(tmp_path, monkeypatch):
    from installer import import_step

    captured = {}

    def fake_update(*args, **kwargs):
        captured["ensure_index"] = kwargs.get("ensure_index")
        return {
            "mode": "diffs",
            "applied": 1,
            "cursor": "abcd",
            "baseline": None,
            "adopted_from": None,
        }

    class _Console:
        def status(self, *_a, **_k):
            import contextlib

            return contextlib.nullcontext()

    def wiring(repo_root, password, repo):
        return object(), object(), object(), (lambda b: None)

    import_step.run_import(
        _Console(),
        tmp_path,
        "pw",
        repo="whiffernet/embeddington",
        wiring_fn=wiring,
        update_fn=fake_update,
    )
    assert callable(captured["ensure_index"])

    # Invoke the captured lambda for real (no network): prove it resolves the
    # `lexical_index` name and passes the production constants, rather than just
    # being "some callable" that would NameError on first real use.
    spy_calls = []

    def spy(url, collection):
        spy_calls.append((url, collection))

    monkeypatch.setattr(import_step.lexical_index, "incremental_chunk_text_index", spy)
    captured["ensure_index"]()
    assert spy_calls == [(import_step.QDRANT_URL, import_step.COLLECTION)]


def test_run_import_maps_unexpected_store_error_to_emb45(tmp_path):
    from installer import import_step
    from installer.errors import SetupError

    class _Console:
        def status(self, *_a, **_k):
            import contextlib

            return contextlib.nullcontext()

    def wiring(repo_root, password, repo):
        return object(), object(), object(), (lambda b: None)

    def boom_update(*a, **k):
        raise RuntimeError(
            "arango 503: still recovering from WAL"
        )  # not an EmbeddingtonError/OSError

    try:
        import_step.run_import(_Console(), tmp_path, "pw", wiring_fn=wiring, update_fn=boom_update)
    except SetupError as exc:
        assert exc.code == "EMB-45"
        assert "recovering" in str(exc)
    else:
        raise AssertionError("expected a SetupError")


def test_emb43_carries_the_guards_own_message():
    def update_fn(*a, **k):
        raise updater.BaselineRefused("Qdrant already has 152,194 points ...")

    with pytest.raises(errors.SetupError) as exc:
        run_with(update_fn)
    assert "152,194" in exc.value.friendly


def test_installed_kg_schema_defaults_to_none_when_env_absent(tmp_path):
    assert import_step.installed_kg_schema(tmp_path / "consumer") is None


def test_installed_kg_schema_reads_the_persisted_value(tmp_path):
    consumer_dir = tmp_path / "consumer"
    consumer_dir.mkdir()
    (consumer_dir / ".env").write_text("EMBEDDINGTON_KG_SCHEMA=v3\n")
    assert import_step.installed_kg_schema(consumer_dir) == "v3"


def test_production_wiring_resolves_v2_names_when_env_says_nothing(tmp_path, monkeypatch):
    consumer_dir = tmp_path / "consumer"
    consumer_dir.mkdir()
    (consumer_dir / ".env").write_text("ARANGO_ROOT_PASSWORD=x\n")
    captured = {}
    monkeypatch.setattr(
        import_step.writers.ArangoConsumerWriter,
        "connect",
        classmethod(lambda cls, *a, **k: captured.update(k) or object()),
    )
    monkeypatch.setattr(
        import_step.writers.QdrantConsumerWriter, "connect", classmethod(lambda cls, *a: object())
    )
    import_step._production_wiring(tmp_path, "pw", "whiffernet/embeddington")
    assert captured == {"entities": "entities_v2", "relationships": "relationships_v2"}


def test_production_wiring_resolves_v3_names_when_env_says_so(tmp_path, monkeypatch):
    consumer_dir = tmp_path / "consumer"
    consumer_dir.mkdir()
    (consumer_dir / ".env").write_text("EMBEDDINGTON_KG_SCHEMA=v3\n")
    captured = {}
    monkeypatch.setattr(
        import_step.writers.ArangoConsumerWriter,
        "connect",
        classmethod(lambda cls, *a, **k: captured.update(k) or object()),
    )
    monkeypatch.setattr(
        import_step.writers.QdrantConsumerWriter, "connect", classmethod(lambda cls, *a: object())
    )
    import_step._production_wiring(tmp_path, "pw", "whiffernet/embeddington")
    assert captured == {"entities": "entities_v3", "relationships": "relationships_v3"}


def test_persist_kg_schema_writes_both_consumer_and_mcp_env(tmp_path):
    """C1/I1 fix: persistence moved inside make_baseline_importer's importer (see
    tests/consumer/test_updater.py's C1 regression and test_restore_ops.py for the
    wiring-order tests) -- this pins _persist_kg_schema's own two-file contract in
    isolation. mcp/.env doesn't exist yet in a typical install; it must be CREATED."""
    (tmp_path / "consumer").mkdir()
    (tmp_path / "consumer" / ".env").write_text("ARANGO_ROOT_PASSWORD=x\n")
    (tmp_path / "mcp").mkdir()

    import_step._persist_kg_schema(tmp_path, "v3")

    assert (tmp_path / "consumer" / ".env").read_text() == (
        "ARANGO_ROOT_PASSWORD=x\nEMBEDDINGTON_KG_SCHEMA=v3\n"
    )
    assert (tmp_path / "mcp" / ".env").read_text() == "EMBEDDINGTON_KG_SCHEMA=v3\n"


def test_persist_kg_schema_replaces_an_existing_value_in_both_files(tmp_path):
    (tmp_path / "consumer").mkdir()
    (tmp_path / "consumer" / ".env").write_text("EMBEDDINGTON_KG_SCHEMA=v2\n")
    (tmp_path / "mcp").mkdir()
    (tmp_path / "mcp" / ".env").write_text("QDRANT_URL=http://x\nEMBEDDINGTON_KG_SCHEMA=v2\n")

    import_step._persist_kg_schema(tmp_path, "v3")

    assert (tmp_path / "consumer" / ".env").read_text() == "EMBEDDINGTON_KG_SCHEMA=v3\n"
    assert (tmp_path / "mcp" / ".env").read_text() == (
        "QDRANT_URL=http://x\nEMBEDDINGTON_KG_SCHEMA=v3\n"
    )


def test_persist_kg_schema_one_file_failing_does_not_stop_the_other(tmp_path, capsys):
    """Neither file's directory exists -- both writes fail, both are warned about
    independently, and neither raises."""
    import_step._persist_kg_schema(tmp_path / "no-such-clone", "v3")
    err = capsys.readouterr().err
    assert err.count("warning") == 2
    assert "consumer" in err and "mcp" in err


def test_production_wiring_threads_the_arango_writer_and_persist_into_the_importer(
    tmp_path, monkeypatch
):
    """C1 fix: make_baseline_importer must receive the SAME arango writer run_import
    threads into updater.update (so a v3 restore's retarget takes effect on the
    writer the trailing diff-apply loop actually uses), plus a persist_kg_schema
    callable wired to _persist_kg_schema."""
    (tmp_path / "consumer").mkdir()
    (tmp_path / "consumer" / ".env").write_text("ARANGO_ROOT_PASSWORD=x\n")
    (tmp_path / "mcp").mkdir()

    sentinel_arango = object()
    monkeypatch.setattr(
        import_step.writers.ArangoConsumerWriter,
        "connect",
        classmethod(lambda cls, *a, **k: sentinel_arango),
    )
    monkeypatch.setattr(
        import_step.writers.QdrantConsumerWriter, "connect", classmethod(lambda cls, *a: object())
    )
    captured = {}

    def fake_make_baseline_importer(*a, **k):
        captured.update(k)
        return "importer"

    monkeypatch.setattr(
        import_step.restore_ops, "make_baseline_importer", fake_make_baseline_importer
    )

    def fake_update(*a, **k):
        return {
            "mode": "up_to_date",
            "applied": 0,
            "cursor": "x",
            "baseline": None,
            "adopted_from": None,
        }

    import_step.run_import(
        console(),
        tmp_path,
        "pw",
        env={"EMBEDDINGTON_HOME": str(tmp_path / "state")},
        home=tmp_path,
        cwd=tmp_path,
        update_fn=fake_update,
    )

    assert captured["arango_writer"] is sentinel_arango
    assert callable(captured["persist_kg_schema"])

    # Exercise the captured closure for real (no network): prove it resolves the
    # module-level _persist_kg_schema and repo_root, rather than being some callable
    # that would NameError on first real use.
    captured["persist_kg_schema"]("v3")
    assert "EMBEDDINGTON_KG_SCHEMA=v3" in (tmp_path / "consumer" / ".env").read_text()
    assert "EMBEDDINGTON_KG_SCHEMA=v3" in (tmp_path / "mcp" / ".env").read_text()


def test_proof_of_life_returns_counts():
    assert import_step.proof_of_life(lambda: 152_194, lambda: 41_000) == (152_194, 41_000)


def test_proof_of_life_zero_is_emb44():
    with pytest.raises(errors.SetupError) as exc:
        import_step.proof_of_life(lambda: 0, lambda: 41_000)
    assert exc.value.code == "EMB-44"


def test_proof_of_life_raising_counter_is_emb44_not_a_traceback():
    # The real entity_count() RAISES on 401/500/503 (e.g. Arango still in WAL recovery
    # right after compose up) — that must surface as EMB-44, not crash the wizard.
    def boom():
        raise ConnectionError("WAL recovery in progress")

    with pytest.raises(errors.SetupError) as exc:
        import_step.proof_of_life(boom, lambda: 41_000)
    assert exc.value.code == "EMB-44"
