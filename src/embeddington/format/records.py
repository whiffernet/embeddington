"""Builders, encoder, and decoder for diff-bundle records (one JSON object per line)."""

import json

from embeddington.errors import RecordError

_VALID_KINDS = {"point", "entity", "edge"}
_VALID_OPS = {"upsert", "delete"}


def header(schema_version, prev_sha, head_sha, points, entities, edges):
    """Build a bundle header record.

    Args:
        schema_version: Semver string governing compatibility (e.g. "1.0").
        prev_sha: The source git SHA this bundle applies on top of.
        head_sha: The source git SHA this bundle advances the cursor to.
        points: Count of point upserts in the bundle.
        entities: Count of entity upserts.
        edges: Count of edge upserts.

    Returns:
        A header record dict shaped ``{"_hdr": {...}}``.
    """
    return {
        "_hdr": {
            "schema_version": schema_version,
            "prev_sha": prev_sha,
            "head_sha": head_sha,
            "points": points,
            "entities": entities,
            "edges": edges,
        }
    }


def point_upsert(point_id, vector, payload):
    """Build a Qdrant point upsert record.

    Args:
        point_id: Unique identifier for the Qdrant point.
        vector: Embedding vector as a list of floats.
        payload: Metadata dict to store alongside the vector.

    Returns:
        A record dict with op="upsert", kind="point".
    """
    return {
        "op": "upsert",
        "kind": "point",
        "id": point_id,
        "vector": vector,
        "payload": payload,
    }


def entity_upsert(key, doc):
    """Build an Arango entity (vertex) upsert record.

    Args:
        key: ArangoDB _key for the entity document.
        doc: Attribute dict for the entity vertex.

    Returns:
        A record dict with op="upsert", kind="entity".
    """
    return {"op": "upsert", "kind": "entity", "_key": key, "doc": doc}


def edge_upsert(key, from_, to, predicate, doc):
    """Build an Arango relationship (edge) upsert record.

    Args:
        key: ArangoDB _key for the edge document.
        from_: Full ArangoDB document handle for the source vertex.
        to: Full ArangoDB document handle for the target vertex.
        predicate: Relationship label (e.g. "DEPENDS_ON").
        doc: Extra attributes to store on the edge.

    Returns:
        A record dict with op="upsert", kind="edge".
    """
    return {
        "op": "upsert",
        "kind": "edge",
        "_key": key,
        "_from": from_,
        "_to": to,
        "predicate": predicate,
        "doc": doc,
    }


def point_delete_by_filename(filename):
    """Build a tombstone deleting all points whose payload.filename matches.

    Args:
        filename: The filename value to match against payload.filename.

    Returns:
        A record dict with op="delete", kind="point".
    """
    return {"op": "delete", "kind": "point", "filename": filename}


def entity_delete(key):
    """Build a tombstone deleting one entity by _key.

    Args:
        key: ArangoDB _key of the entity to delete.

    Returns:
        A record dict with op="delete", kind="entity".
    """
    return {"op": "delete", "kind": "entity", "_key": key}


def edge_delete(key):
    """Build a tombstone deleting one edge by _key.

    Args:
        key: ArangoDB _key of the edge to delete.

    Returns:
        A record dict with op="delete", kind="edge".
    """
    return {"op": "delete", "kind": "edge", "_key": key}


def is_header(record):
    """Return True if the record is a bundle header.

    Args:
        record: A decoded record dict.

    Returns:
        True when the record contains a ``_hdr`` key.
    """
    return isinstance(record, dict) and "_hdr" in record


def validate(record):
    """Raise RecordError if the record is not a well-formed header/upsert/delete.

    Args:
        record: A decoded record dict.

    Raises:
        RecordError: On unknown op, unknown kind, or missing structure.
    """
    if is_header(record):
        return
    op = record.get("op")
    kind = record.get("kind")
    if op not in _VALID_OPS:
        raise RecordError(f"unknown op: {op!r}")
    if kind not in _VALID_KINDS:
        raise RecordError(f"unknown kind: {kind!r}")


def encode(record):
    """Serialize a record dict to a single JSON line (sorted keys for determinism).

    Args:
        record: A record dict produced by one of the builder functions.

    Returns:
        A JSON string with sorted keys and no trailing newline.
    """
    return json.dumps(record, sort_keys=True, ensure_ascii=False)


def decode(line):
    """Parse and validate a JSON line into a record dict.

    Args:
        line: A single JSON line string.

    Returns:
        A validated record dict.

    Raises:
        RecordError: On invalid JSON or a malformed record.
    """
    try:
        record = json.loads(line)
    except json.JSONDecodeError as exc:
        raise RecordError(f"invalid JSON: {exc}") from exc
    validate(record)
    return record
