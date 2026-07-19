#!/usr/bin/env python3
"""Capture + freeze the gold candidate pools (spec §3.2).

Builds each battery query's budget-independent candidate pool from the
restored battery stack and freezes it (with a stack binding + per-pool
fingerprint) to ``pools.json``. Labels (Task 8) attach to exactly these
edge ids. Run with the battery env (see battery_sweep.py docstring).

``--cohort identifier`` captures the spec §3.4 identifier cohort
(``battery_queries.IDENTIFIER_QUERIES``) into ``pools-identifier.json``
instead; the default ``--cohort fixed`` is unchanged and still writes
``pools.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parents[1]
_MCP = _TESTS.parent
for p in (str(_MCP), str(_TESTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

import config  # noqa: E402
import gold_pools  # noqa: E402
import server  # noqa: E402
import sweep_io  # noqa: E402

_GOLD_DIR = Path(__file__).resolve().parent
_OUTPUT_PATHS = {
    "fixed": _GOLD_DIR / "pools.json",
    "identifier": _GOLD_DIR / "pools-identifier.json",
}


def resolve_cohort(name: str) -> tuple[list[dict], Path]:
    """Resolve a ``--cohort`` flag to its query list and output path.

    Args:
        name: ``"fixed"`` (default, frozen 11-query battery -> pools.json)
            or ``"identifier"`` (the id_* cohort -> pools-identifier.json).

    Returns:
        A ``(queries, output_path)`` pair.

    Raises:
        ValueError: if ``name`` is not a recognized cohort.
    """
    queries = sweep_io.select_cohort(name)  # raises ValueError on unknown cohort
    return queries, _OUTPUT_PATHS[name]


EDGE_FIELDS = (
    "predicate",
    "source_quote",
    "source_document",
    "confidence",
    "extraction_type",
    "releases",
    "source",
    "target",
)


def _build_parser() -> argparse.ArgumentParser:
    """Build the argv parser (pure — no stack access, safe to unit-test)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", choices=sorted(_OUTPUT_PATHS), default="fixed")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    cohort_queries, out = resolve_cohort(args.cohort)

    arango = server._get_arango()
    binding = gold_pools.stack_binding(config.QDRANT_URL, arango)
    gold_pools.assert_binding(binding)
    queries: dict[str, dict] = {}
    for q in cohort_queries:
        pool = gold_pools.build_pool(arango, q)
        queries[q["name"]] = {
            "fingerprint": gold_pools.pool_fingerprint(pool),
            "query": q["query"],
            "edges": {eid: {f: ed.get(f) for f in EDGE_FIELDS} for eid, ed in sorted(pool.items())},
        }
        print(f"{q['name']:32s} pool={len(pool):4d}", file=sys.stderr)
    out.write_text(
        json.dumps(
            {
                "binding": binding,
                "per_predicate": gold_pools.POOL_PER_PREDICATE,
                "overall": gold_pools.POOL_OVERALL,
                "queries": queries,
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
