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
