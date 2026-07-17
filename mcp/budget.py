"""Pure budgeting/selection logic for enrich — no I/O, fully unit-testable.

Owns: entity-name normalization, concept grouping (dedup), budget allocation
across concepts, predicate-stratified edge selection, response token
estimation, and the ceiling trim loop. See the design spec
(2026-07-17-enrich-payload-budget-design.md) for rationale.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

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

    Returns:
        Slot counts aligned with concepts[:max_concepts]; zeros for concepts
        the budget cannot cover.
    """
    n = min(len(concepts), max_concepts)
    if n == 0 or edge_budget <= 0:
        return [0] * n
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
