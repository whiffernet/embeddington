"""Apply a sequence of diff records to Qdrant + Arango writers, idempotently.

Every write is keyed (point id / Arango _key) so re-applying a bundle is a no-op,
and deletes never error when the target is already absent. This is what makes the
update path crash-safe and resumable (spec §3.3, §8).
"""

from embeddington.errors import RecordError
from embeddington.format import records


def _require(rec, *keys):
    """Extract the named keys from a record, or raise RecordError if any is absent.

    Scoped to pure record field-access only, so a KeyError raised inside a real
    Qdrant/Arango adapter is never mis-reported as a malformed-record RecordError.

    Args:
        rec: A decoded record dict.
        *keys: Field names required on the record.

    Returns:
        A list of the requested field values, in order.

    Raises:
        RecordError: If any requested key is missing.
    """
    try:
        return [rec[k] for k in keys]
    except KeyError as exc:
        raise RecordError(
            f"record missing field {exc} for {rec.get('op')}/{rec.get('kind')}"
        ) from exc


def apply_diff(record_iter, qdrant, arango):
    """Apply all records from one bundle to the given writers.

    Args:
        record_iter: Iterable of decoded record dicts (header optional, first).
        qdrant: A QdrantWriter.
        arango: An ArangoWriter.

    Returns:
        dict with keys ``header`` (the bundle header _hdr dict or None) and
        ``counts`` (points/entities/edges/deletes applied). ``counts["deletes"]``
        is the aggregate of ALL tombstones (point + entity + edge), not per-kind.

    Note: an edge record carries a top-level ``predicate`` field, but the current
        ``ArangoWriter.upsert_edge`` signature does not receive it (it is dropped here).
        Threading predicate through to the writer is a deferred Plan 2 decision — until
        then, a publisher that needs predicate persisted must include it inside ``doc``.

    Raises:
        RecordError: On an unknown op/kind or a record missing required fields.
    """
    header = None
    counts = {"points": 0, "entities": 0, "edges": 0, "deletes": 0}

    for rec in record_iter:
        if records.is_header(rec):
            header = rec["_hdr"]
            continue
        records.validate(rec)
        op, kind = rec["op"], rec["kind"]

        if op == "upsert":
            if kind == "point":
                point_id, vector, payload = _require(rec, "id", "vector", "payload")
                qdrant.upsert_point(point_id, vector, payload)
                counts["points"] += 1
            elif kind == "entity":
                key, doc = _require(rec, "_key", "doc")
                arango.upsert_entity(key, doc)
                counts["entities"] += 1
            else:  # edge
                key, from_, to, doc = _require(rec, "_key", "_from", "_to", "doc")
                arango.upsert_edge(key, from_, to, doc)
                counts["edges"] += 1
        else:  # delete
            if kind == "point":
                if "filename" in rec:
                    (filename,) = _require(rec, "filename")
                    qdrant.delete_points_by_filename(filename)
                else:
                    (point_id,) = _require(rec, "id")
                    qdrant.delete_point(point_id)
            elif kind == "entity":
                (key,) = _require(rec, "_key")
                arango.delete_entity(key)
            else:  # edge
                (key,) = _require(rec, "_key")
                arango.delete_edge(key)
            counts["deletes"] += 1

    return {"header": header, "counts": counts}
