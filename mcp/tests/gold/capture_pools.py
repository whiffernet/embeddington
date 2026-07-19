#!/usr/bin/env python3
"""Capture + freeze the gold candidate pools (spec §3.2).

Builds each battery query's budget-independent candidate pool from the
restored battery stack and freezes it (with a stack binding + per-pool
fingerprint) to ``pools.json``. Labels (Task 8) attach to exactly these
edge ids. Run with the battery env (see battery_sweep.py docstring).
"""

from __future__ import annotations

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
from battery_queries import QUERIES  # noqa: E402

OUT = Path(__file__).resolve().parent / "pools.json"

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


def main() -> None:
    arango = server._get_arango()
    binding = gold_pools.stack_binding(config.QDRANT_URL, arango)
    gold_pools.assert_binding(binding)
    queries: dict[str, dict] = {}
    for q in QUERIES:
        pool = gold_pools.build_pool(arango, q)
        queries[q["name"]] = {
            "fingerprint": gold_pools.pool_fingerprint(pool),
            "query": q["query"],
            "edges": {eid: {f: ed.get(f) for f in EDGE_FIELDS} for eid, ed in sorted(pool.items())},
        }
        print(f"{q['name']:32s} pool={len(pool):4d}", file=sys.stderr)
    OUT.write_text(
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
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
