"""Shared fixtures: in-memory fakes implementing the writer protocols."""

import pytest


class FakeQdrant:
    """In-memory stand-in for the Qdrant technology collection."""

    def __init__(self):
        self.points = {}  # id -> {"vector": [...], "payload": {...}}

    def upsert_point(self, point_id, vector, payload):
        self.points[point_id] = {"vector": vector, "payload": payload}

    def delete_point(self, point_id):
        self.points.pop(point_id, None)

    def delete_points_by_filename(self, filename):
        for pid in [
            p
            for p, v in self.points.items()
            if v["payload"].get("filename") == filename
        ]:
            del self.points[pid]


class FakeArango:
    """In-memory stand-in for entities_v2 / relationships_v2."""

    def __init__(self):
        self.entities = {}  # key -> doc
        self.edges = {}  # key -> {"_from", "_to", "doc"}

    def upsert_entity(self, key, doc):
        self.entities[key] = doc

    def upsert_edge(self, key, from_, to, doc):
        self.edges[key] = {"_from": from_, "_to": to, "doc": doc}

    def delete_entity(self, key):
        self.entities.pop(key, None)

    def delete_edge(self, key):
        self.edges.pop(key, None)


@pytest.fixture
def fake_qdrant():
    return FakeQdrant()


@pytest.fixture
def fake_arango():
    return FakeArango()
