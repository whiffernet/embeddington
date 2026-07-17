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
