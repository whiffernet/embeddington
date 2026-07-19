"""Unit tests for capture_pools.py's pure cohort-resolution helper.

capture_pools.main() needs the live battery stack (server._get_arango(),
gold_pools.stack_binding()) so it isn't unit-tested here — only the argv ->
(query list, output path) resolution, which is pure and import-safe.
"""

import sys
from pathlib import Path

import pytest
from battery_queries import IDENTIFIER_QUERIES
from battery_queries import QUERIES as FIXED_QUERIES

sys.path.insert(0, str(Path(__file__).resolve().parent / "gold"))
import capture_pools  # noqa: E402


def test_resolve_cohort_fixed_default():
    queries, out = capture_pools.resolve_cohort("fixed")
    assert queries is FIXED_QUERIES
    assert out.name == "pools.json"


def test_resolve_cohort_identifier():
    queries, out = capture_pools.resolve_cohort("identifier")
    assert queries is IDENTIFIER_QUERIES
    assert out.name == "pools-identifier.json"


def test_resolve_cohort_unknown_raises():
    with pytest.raises(ValueError, match="unknown cohort"):
        capture_pools.resolve_cohort("bogus")


def test_argv_cohort_flag_defaults_to_fixed():
    args = capture_pools._build_parser().parse_args([])
    assert args.cohort == "fixed"


def test_argv_cohort_flag_parses_identifier():
    args = capture_pools._build_parser().parse_args(["--cohort", "identifier"])
    assert args.cohort == "identifier"


def test_argv_cohort_flag_rejects_unknown():
    with pytest.raises(SystemExit):
        capture_pools._build_parser().parse_args(["--cohort", "bogus"])
