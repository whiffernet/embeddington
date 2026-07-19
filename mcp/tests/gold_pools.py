"""Shared, budget-independent candidate-pool construction (spec §3.2).

Single source of truth for the gold-set pool: the sweep's retention pool and
the frozen gold pools MUST be built by the same code so they can never drift.
The pool is deliberately independent of ``edge_budget``/``top_k`` — the tuned
parameters must never shape the pool that grades them.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import httpx

_MCP = Path(__file__).resolve().parent.parent
if str(_MCP) not in sys.path:
    sys.path.insert(0, str(_MCP))

from enrich import _extract_entity_hints  # noqa: E402

POOL_PER_PREDICATE = 2
POOL_OVERALL = 100

EXPECTED_BINDING: dict[str, Any] = {
    "baseline": "baseline-2026-07b",
    "points": 152194,
    "entities": 310364,
    "edges": 683651,
}


class BindingError(RuntimeError):
    """Raised when the live stack does not match the frozen gold binding."""


def resolve_hints(q: dict) -> list[str]:
    """Hints enrich would use: explicit, else the same regex fallback."""
    if q["entity_hints"] is not None:
        return list(q["entity_hints"])
    return _extract_entity_hints(q["query"])


def build_pool(arango: Any, q: dict) -> dict[str, dict]:
    """Merged unbudgeted neighbor pool for a query's resolved entities."""
    pool: dict[str, dict] = {}
    for hint in resolve_hints(q):
        for ent in arango.find_entities(hint, limit=3):
            fetched = arango.neighbors_stratified(
                ent["id"],
                per_predicate=POOL_PER_PREDICATE,
                overall=POOL_OVERALL,
                predicates=q["predicates"],
            )
            for ed in fetched["edges"]:
                pool.setdefault(str(ed["id"]), ed)
    return pool


def pool_fingerprint(pool: dict[str, dict]) -> str:
    """Deterministic sha256 over the pool's edge-id membership."""
    return hashlib.sha256(json.dumps(sorted(pool)).encode()).hexdigest()


def stack_binding(qdrant_url: str, arango: Any) -> dict[str, Any]:
    """Read the live stack's identity counts (Qdrant points, KG doc counts)."""
    resp = httpx.get(f"{qdrant_url}/collections/technology", timeout=30)
    resp.raise_for_status()
    points = resp.json()["result"]["points_count"]
    return {
        "baseline": EXPECTED_BINDING["baseline"],
        "points": int(points),
        "entities": int(arango._db.collection("entities_v2").count()),
        "edges": int(arango._db.collection("relationships_v2").count()),
    }


def assert_binding(binding: dict[str, Any]) -> None:
    """Hard-fail unless the binding matches EXPECTED_BINDING exactly (§3.2)."""
    if binding != EXPECTED_BINDING:
        raise BindingError(f"stack drifted from gold binding: {binding} != {EXPECTED_BINDING}")
