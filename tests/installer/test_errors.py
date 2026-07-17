"""Registry and SetupError contract tests."""

import re

import pytest
from installer import errors


def test_every_code_matches_the_emb_pattern():
    assert errors.CODES  # non-empty
    for code in errors.CODES:
        assert re.fullmatch(r"EMB-\d\d", code), code


def test_anchor_is_lowercase_readme_url():
    assert errors.anchor("EMB-21") == "https://github.com/whiffernet/embeddington#emb-21"


def test_setup_error_carries_code_friendly_fix():
    err = errors.SetupError("EMB-21", "Couldn't reach the Docker daemon.", "colima start")
    assert err.code == "EMB-21"
    assert err.friendly == "Couldn't reach the Docker daemon."
    assert err.fix == "colima start"
    assert str(err) == "[EMB-21] Couldn't reach the Docker daemon."


def test_setup_error_rejects_unregistered_codes():
    with pytest.raises(ValueError):
        errors.SetupError("EMB-99", "nope", "nope")


def test_expected_phase_ranges_are_present():
    # The spec's table, pinned: one representative per range.
    for code in ("EMB-10", "EMB-20", "EMB-31", "EMB-41", "EMB-51", "EMB-61"):
        assert code in errors.CODES
