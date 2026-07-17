"""Pure budgeting/selection logic for enrich — no I/O, fully unit-testable.

Owns: entity-name normalization, concept grouping (dedup), budget allocation
across concepts, predicate-stratified edge selection, response token
estimation, and the ceiling trim loop. See the design spec
(2026-07-17-enrich-payload-budget-design.md) for rationale.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

TOKEN_DIVISOR = 3  # deliberately pessimistic: KG JSON tokenizes near 3 chars/token
PREFIX_MERGE_SLACK = 4  # "cmdb_rel_ci" vs "cmdb_rel_ciCIS" — merge if extension <= 4 chars

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_name(name: str) -> str:
    """Casefold and collapse punctuation/whitespace to single spaces."""
    return _NON_ALNUM.sub(" ", name.casefold()).strip()


@dataclass
class Concept:
    """A deduplicated concept: one or more entity variants sharing a name."""

    key: str
    variants: list[dict] = field(default_factory=list)
    hint_index: int = 0


def group_concepts(seeded: list[tuple[int, dict]]) -> list[Concept]:
    """Group (hint_index, entity) pairs into concepts by normalized name.

    Grouping rules (spec §3.1): exact normalized-name match merges; a name
    that extends another by <= PREFIX_MERGE_SLACK chars merges into the
    shorter one's concept; entities with empty names are singleton concepts
    and are never bucketed together; identical entity ids dedup first.

    Args:
        seeded: (hint_index, entity_dict) pairs in per-hint result order.

    Returns:
        Concepts ordered by (hint_index, first-seen order).
    """
    concepts: list[Concept] = []
    seen_ids: set[str] = set()

    for hint_index, entity in seeded:
        eid = entity.get("id", "")
        if eid in seen_ids:
            continue
        seen_ids.add(eid)
        key = normalize_name(entity.get("name") or "")
        target: Concept | None = None
        if key:
            for c in concepts:
                if not c.key:
                    continue
                shorter, longer = sorted((c.key, key), key=len)
                if c.key == key or (
                    longer.startswith(shorter) and len(longer) - len(shorter) <= PREFIX_MERGE_SLACK
                ):
                    target = c
                    break
        if target is None:
            concepts.append(Concept(key=key, variants=[entity], hint_index=hint_index))
        else:
            target.variants.append(entity)
            target.hint_index = min(target.hint_index, hint_index)
            shorter, longer = sorted((target.key, key), key=len)
            target.key = shorter

    concepts.sort(key=lambda c: c.hint_index)
    return concepts


MIN_SLOTS = 3  # a budgeted concept below this is useless — reduce count instead


def allocate_budget(concepts: list[Concept], edge_budget: int, max_concepts: int = 5) -> list[int]:
    """Split edge_budget across the first max_concepts concepts (spec §3.4).

    Weights: concepts seeded by the FIRST hint (the query's subject) weigh
    2.0, all others 1.0. Every budgeted concept gets >= MIN_SLOTS; if the
    budget cannot give MIN_SLOTS to all, the concept count shrinks (relevance
    order) rather than the floor. Leftover from integer division distributes
    +1 at a time in concept (relevance) order — never by degree.

    Args:
        concepts: List of Concept objects to budget for, in relevance order.
        edge_budget: Total slots available; invariant sum(result) <= edge_budget.
        max_concepts: Maximum number of concepts to budget (default 5).

    Returns:
        Slot counts aligned with concepts[:max_concepts]; zeros for concepts
        the budget cannot cover. Invariant: sum(result) <= edge_budget and
        len(result) == min(len(concepts), max_concepts).
    """
    n = min(len(concepts), max_concepts)
    if n == 0 or edge_budget <= 0:
        return [0] * n
    # Handle sub-floor budgets: give the single top-relevance concept exactly
    # edge_budget slots without the MIN_SLOTS floor.
    if edge_budget < MIN_SLOTS:
        return [edge_budget] + [0] * (n - 1)
    n_budgeted = max(1, min(n, edge_budget // MIN_SLOTS))
    weights = [2.0 if concepts[i].hint_index == 0 else 1.0 for i in range(n_budgeted)]
    total_w = sum(weights)
    slots = [max(MIN_SLOTS, int(edge_budget * w / total_w)) for w in weights]
    # Correct rounding drift: trim from the least-relevant end, then hand out
    # leftover from the most-relevant end.
    while sum(slots) > edge_budget:
        for i in range(n_budgeted - 1, -1, -1):
            if slots[i] > MIN_SLOTS and sum(slots) > edge_budget:
                slots[i] -= 1
        if all(s <= MIN_SLOTS for s in slots):
            break
    i = 0
    while sum(slots) < edge_budget and n_budgeted > 0:
        slots[i % n_budgeted] += 1
        i += 1
    return slots + [0] * (n - n_budgeted)


def estimate_tokens(obj: Any) -> int:
    """Pessimistic token estimate: ceil(compact-JSON length / TOKEN_DIVISOR).

    Args:
        obj: Any JSON-serializable object.

    Returns:
        Estimated token count (integer).
    """
    return math.ceil(
        len(json.dumps(obj, separators=(",", ":"), ensure_ascii=False)) / TOKEN_DIVISOR
    )


def coalesced_confidence(edge: dict) -> float:
    """Edge confidence with null coalesced to mid-tier 0.5 (spec §3.3).

    Args:
        edge: Edge dictionary with optional 'confidence' key.

    Returns:
        Confidence value (0.0-1.0), or 0.5 if missing/null.
    """
    c = edge.get("confidence")
    return 0.5 if c is None else float(c)


def select_edges(edges: list[dict], slots: int) -> list[dict]:
    """Predicate-diversity floor, then confidence fill (spec §3.3).

    Pass 1 walks predicates ordered by their best edge's coalesced confidence
    (desc) taking the best edge per predicate — so a minority predicate's
    single edge survives 50 high-confidence edges of another class. Pass 2
    fills remaining slots by coalesced confidence desc. Ties break on edge id
    for determinism. Floor picks come first in the output — the ceiling trim
    (Task 5) drops from the tail, so diversity is sacrificed last.

    Args:
        edges: List of edge dictionaries with 'id', 'predicate', and
            optional 'confidence' keys.
        slots: Maximum number of edges to select.

    Returns:
        Selected edges, ordered floor-picks-first.
    """
    if slots <= 0 or not edges:
        return []
    ranked = sorted(edges, key=lambda e: (-coalesced_confidence(e), str(e.get("id"))))
    kept: list[dict] = []
    kept_ids: set[str] = set()
    seen_preds: set[str] = set()
    for e in ranked:  # pass 1: best edge per distinct predicate
        if len(kept) >= slots:
            break
        p = e.get("predicate")
        eid = str(e.get("id"))
        if p not in seen_preds and eid not in kept_ids:
            seen_preds.add(p)
            kept.append(e)
            kept_ids.add(eid)
    for e in ranked:  # pass 2: fill by confidence
        if len(kept) >= slots:
            break
        eid = str(e.get("id"))
        if eid not in kept_ids:
            kept.append(e)
            kept_ids.add(eid)
    return kept


def trim_to_ceiling(result: dict, max_tokens: int, floor: int = 3) -> dict:
    """Enforce the response token ceiling (spec §4.1–4.2). Mutates result.

    Victim rule: while over ceiling, drop the tail edge of the match holding
    the most edges above its floor (all budgeted matches are hint-derived, so
    all carry the floor). Then trim vector chunks down to one. If still over,
    flag loudly — never return silently oversized, never drop below floors.

    Args:
        result: The assembled enrich envelope with kg_matches and vector_chunks.
        max_tokens: The token ceiling to enforce.
        floor: Minimum edges per concept (default 3).

    Returns:
        The mutated result dict.
    """

    def over() -> bool:
        return estimate_tokens(result) > max_tokens

    matches = result.get("kg_matches", [])
    while over():
        candidates = [m for m in matches if len(m["edges"]) > floor]
        if not candidates:
            break
        victim = max(candidates, key=lambda m: len(m["edges"]))
        victim["edges"].pop()  # tail = lowest-value (selection is diversity-first)
        victim["truncation"]["returned"] = len(victim["edges"])
        victim["truncation"]["truncated"] = True
        result["budget"]["truncated"] = True

    chunks = result.get("vector_chunks", [])
    while over() and len(chunks) > 1:
        chunks.pop()  # qdrant returns score-ordered; tail = weakest
        result["budget"]["truncated"] = True
        if "response ceiling: vector chunks trimmed" not in result["warnings"]:
            result["warnings"].append("response ceiling: vector chunks trimmed")

    if over():
        result["budget"]["truncated"] = True
        result["warnings"].append(
            "response exceeds ceiling even at floors — narrow with predicates"
        )
    result["budget"]["returned"] = sum(len(m["edges"]) for m in matches)
    return result
