"""Intrinsic ontology metrics computed by AQL (spec §4/M1).

Each function takes a python-arango database handle and returns a plain dict.
No file I/O and no config reads — that lives in ontology_snapshot.py — so the
arithmetic is testable without a stack.

NON-INDEPENDENCE WARNING (spec §4/M1): ``noise`` shares its ruleset with the U5
fix, and ``fragmentation`` shares its normalization with the U6 fix. Neither is
independent evidence that those fixes improved quality, and the closing report
must not cite them as such.
"""

from typing import Any

from ontology_frozen import HUB_DEGREE_THRESHOLD, KNOWN_RELEASES, normalize_release
from ontology_rules import classify_noise

# AQL equivalent of budget.normalize_name: casefold + collapse runs of
# non-alphanumerics to a single space + strip. `[^a-z0-9]+` uses `+` so runs
# collapse, matching the Python regex.
#
# Documented approximation: AQL LOWER() is not Python str.casefold(). They
# differ only on non-ASCII (e.g. the German sharp s), which this
# English-language ServiceNow corpus does not exercise.
NORMALIZE_AQL = 'TRIM(REGEX_REPLACE(LOWER(e.name), "[^a-z0-9]+", " "))'

# Normalized known-release keys, computed once.
_KNOWN_RELEASE_KEYS = frozenset(normalize_release(r) for r in KNOWN_RELEASES)


def _one(db: Any, query: str, bind_vars: dict | None = None) -> Any:
    """Execute an AQL query expected to yield at most one row."""
    rows = list(db.aql.execute(query, bind_vars=bind_vars))
    return rows[0] if rows else None


def _int(value: Any) -> int:
    """Coerce an AQL aggregate to int.

    AQL ``SUM()`` over zero rows returns ``null``, not ``0`` — verified live.
    Every aggregate read here passes through this so an empty graph yields
    zeros rather than a TypeError on the subsequent division.
    """
    return int(value) if value is not None else 0


def fragmentation(db: Any) -> dict:
    """Count concepts whose normalized name spans two or more entity types.

    Reports the specific (>=3-word) and generic (<=2-word) splits separately:
    compound ServiceNow names do not coincidentally collide, so the specific
    split is near-certainly true duplication, while the generic split contains
    real polysemy ("error", "group", "search"). The spec requires a
    floor/ceiling, never a single headline number.

    Args:
        db: python-arango database handle.

    Returns:
        Counts plus ``fragmentation_rate``.
    """
    grouped = (
        _one(
            db,
            f"""
        FOR e IN entities_v2
          COLLECT nm = {NORMALIZE_AQL} INTO g
          LET types = UNIQUE(g[*].e.type)
          FILTER LENGTH(types) >= 2
          LET is_specific = LENGTH(SPLIT(nm, " ")) >= 3
          COLLECT AGGREGATE
            cross_type_concept_count = COUNT(1),
            entities_in_cross_type_groups = SUM(LENGTH(g)),
            specific_groups = SUM(is_specific ? 1 : 0),
            specific_entities = SUM(is_specific ? LENGTH(g) : 0),
            generic_groups = SUM(is_specific ? 0 : 1),
            generic_entities = SUM(is_specific ? 0 : LENGTH(g))
          RETURN {{
            cross_type_concept_count, entities_in_cross_type_groups,
            specific_groups, specific_entities,
            generic_groups, generic_entities
          }}
        """,
        )
        or {}
    )
    total = _int(_one(db, "RETURN {total: LENGTH(entities_v2)}")["total"])
    in_groups = _int(grouped.get("entities_in_cross_type_groups"))
    return {
        "cross_type_concept_count": _int(grouped.get("cross_type_concept_count")),
        "entities_in_cross_type_groups": in_groups,
        "total_entities": total,
        "fragmentation_rate": (in_groups / total) if total else 0.0,
        "specific_groups": _int(grouped.get("specific_groups")),
        "specific_entities": _int(grouped.get("specific_entities")),
        "generic_groups": _int(grouped.get("generic_groups")),
        "generic_entities": _int(grouped.get("generic_entities")),
    }


def noise(db: Any) -> dict:
    """Classify every entity name against the vendored noise ruleset.

    Streams names rather than aggregating in AQL because the ruleset is Python
    regex, not expressible as an AQL predicate without divergence risk.

    Args:
        db: python-arango database handle.

    Returns:
        Totals, ``noise_rate``, and a per-category breakdown.
    """
    by_category: dict[str, int] = {}
    total = 0
    noisy = 0
    for row in db.aql.execute("FOR e IN entities_v2 RETURN {name: e.name}"):
        total += 1
        category = classify_noise(row["name"] or "")
        if category is not None:
            noisy += 1
            by_category[category] = by_category.get(category, 0) + 1
    return {
        "total_entities": total,
        "noise_entities": noisy,
        "noise_rate": (noisy / total) if total else 0.0,
        "by_category": by_category,
    }


def topology(db: Any) -> dict:
    """Measure leaf fraction and hub edge concentration.

    ``hub_concentration`` is the share of ALL edges incident to at least one
    hub. Its node set derives from HUB_DEGREE_THRESHOLD rather than a separate
    top-K, so there is no second knob with which to tune the metric.

    Args:
        db: python-arango database handle.

    Returns:
        Leaf and hub counts plus their derived fractions.
    """
    leaves = (
        _one(
            db,
            """
        FOR e IN entities_v2
          LET d = LENGTH(FOR x IN 1..1 ANY e relationships_v2 RETURN 1)
          COLLECT AGGREGATE
            total_entities = COUNT(1),
            leaf_entities = SUM(d == 1 ? 1 : 0)
          RETURN {total_entities, leaf_entities}
        """,
        )
        or {}
    )
    hubs = (
        _one(
            db,
            """
        FOR e IN entities_v2
          LET d = LENGTH(FOR x IN 1..1 ANY e relationships_v2 RETURN 1)
          FILTER d > @threshold
          COLLECT WITH COUNT INTO hub_count
          RETURN {hub_count}
        """,
            {"threshold": HUB_DEGREE_THRESHOLD},
        )
        or {}
    )
    edges = (
        _one(
            db,
            """
        LET hub_ids = (
          FOR e IN entities_v2
            LET d = LENGTH(FOR x IN 1..1 ANY e relationships_v2 RETURN 1)
            FILTER d > @threshold
            RETURN e._id
        )
        FOR r IN relationships_v2
          COLLECT AGGREGATE
            total_edges = COUNT(1),
            hub_incident_edges = SUM(
              (r._from IN hub_ids OR r._to IN hub_ids) ? 1 : 0
            )
          RETURN {total_edges, hub_incident_edges}
        """,
            {"threshold": HUB_DEGREE_THRESHOLD},
        )
        or {}
    )

    total_entities = _int(leaves.get("total_entities"))
    leaf_entities = _int(leaves.get("leaf_entities"))
    total_edges = _int(edges.get("total_edges"))
    hub_incident = _int(edges.get("hub_incident_edges"))
    return {
        "total_entities": total_entities,
        "leaf_entities": leaf_entities,
        "leaf_fraction": (leaf_entities / total_entities) if total_entities else 0.0,
        "hub_count": _int(hubs.get("hub_count")),
        "total_edges": total_edges,
        "hub_incident_edges": hub_incident,
        "hub_concentration": (hub_incident / total_edges) if total_edges else 0.0,
    }


def release_purity(db: Any) -> dict:
    """Fraction of Release-typed entities that are genuine platform releases.

    Matching rule, frozen in ontology_frozen (spec §4/M1): normalize BOTH
    sides via ``normalize_release`` (casefold, strip all non-alphanumerics),
    then accept exact-or-prefix. Exact matching alone recognises 27 of 1,315
    (2.05%) and drops "WashingtonDC Patch 9a", an unambiguous release, purely
    on spacing.

    Args:
        db: python-arango database handle.

    Returns:
        Counts, ``release_purity``, and up to 20 unrecognised names.
    """
    total = 0
    known = 0
    unknown: list[str] = []
    for row in db.aql.execute(
        'FOR e IN entities_v2 FILTER e.type == "Release" RETURN {name: e.name}'
    ):
        total += 1
        raw = (row["name"] or "").strip()
        key = normalize_release(raw)
        if key and any(key == k or key.startswith(k) for k in _KNOWN_RELEASE_KEYS):
            known += 1
        elif len(unknown) < 20:
            unknown.append(raw)
    return {
        "release_entities": total,
        "known_release_entities": known,
        "release_purity": (known / total) if total else 0.0,
        "unknown_samples": unknown,
    }
