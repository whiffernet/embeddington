"""Resolve a manifest baseline's ``kg_schema`` field into physical KG store names.

The KG cutover (v2 -> v3) changes which ArangoDB collections/graph/search-view a
baseline's data lives in, but a manifest baseline entry only ever carries the short
generation tag (``kg_schema: "v2"|"v3"``, absent meaning "v2" -- see ``validate_manifest``,
which does not require the field at all so old and new manifests both parse). Every
consumer-side write surface (``consumer.writers.ArangoConsumerWriter``,
``consumer.restore_ops.ensure_named_graph``, the installer's uninstall inventory) needs
the same mapping from that short tag to the concrete names, so it lives in exactly one
place: here. Callers take the RESOLVED names as constructor/parameter arguments -- none
of them read ``kg_schema`` (or any KG-schema environment variable) themselves.
"""

DEFAULT_KG_SCHEMA = "v2"

_SCHEMA_NAMES = {
    "v2": {
        "entities": "entities_v2",
        "relationships": "relationships_v2",
        "graph": "servicenow_graph_v2",
        "search_view": "entities_v2_search",
    },
    "v3": {
        "entities": "entities_v3",
        "relationships": "relationships_v3",
        "graph": "servicenow_graph_v3",
        "search_view": "entities_v3_search",
    },
}

KNOWN_KG_SCHEMAS = tuple(_SCHEMA_NAMES)


def resolve_schema_names(kg_schema=None):
    """Resolve a ``kg_schema`` tag into its physical ArangoDB store names.

    Args:
        kg_schema: A manifest baseline entry's ``kg_schema`` value (``"v2"`` or
            ``"v3"``), or ``None``/absent -- which resolves identically to ``"v2"``,
            preserving today's behavior byte-for-byte for every manifest published
            before this field existed.

    Returns:
        A fresh dict with keys ``"entities"``, ``"relationships"``, ``"graph"``, and
        ``"search_view"`` naming the collections/graph/view for that schema generation.

    Raises:
        ValueError: ``kg_schema`` is set but not one of ``KNOWN_KG_SCHEMAS``.
    """
    key = kg_schema or DEFAULT_KG_SCHEMA
    try:
        names = _SCHEMA_NAMES[key]
    except KeyError:
        raise ValueError(
            f"unknown kg_schema {kg_schema!r}; expected one of {KNOWN_KG_SCHEMAS}"
        ) from None
    return dict(names)


def all_collection_names():
    """Every ArangoDB vertex/edge collection name across every known schema generation.

    Used where "ours, across every schema generation we have ever shipped" is the
    question -- e.g. telling a genuinely foreign collection apart from one of ours
    that merely belongs to a different (older or newer) KG schema.

    Returns:
        A frozenset of collection names (entities + relationships only -- graphs and
        ArangoSearch views are not collections and are not returned by
        ``db.collections()``, the enumeration this set is meant to be compared against).
    """
    return frozenset(
        names[key] for names in _SCHEMA_NAMES.values() for key in ("entities", "relationships")
    )
