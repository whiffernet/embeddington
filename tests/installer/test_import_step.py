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
