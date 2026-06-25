"""Consumer-test fixtures: fake qdrant/arango clients matching the adapter call surface."""

import pytest
from qdrant_client import models as qmodels


class FakeQdrantClient:
    """Mimics the qdrant_client methods QdrantConsumerWriter calls."""

    def __init__(self):
        self.points = {}  # id -> {"vector","payload"}

    def upsert(self, collection_name, points):
        for p in points:
            self.points[p.id] = {"vector": p.vector, "payload": p.payload}

    def delete(self, collection_name, points_selector):
        if isinstance(points_selector, qmodels.PointIdsList):
            for pid in points_selector.points:
                self.points.pop(pid, None)
        else:  # FilterSelector on payload.filename
            cond = points_selector.filter.must[0]
            fn = cond.match.value
            for pid in [
                p for p, v in self.points.items() if v["payload"].get("filename") == fn
            ]:
                del self.points[pid]


class _FakeCollection:
    def __init__(self, store):
        self._store = store

    def insert(self, doc, overwrite=False):
        self._store[doc["_key"]] = doc

    def delete(self, key, ignore_missing=False):
        self._store.pop(key, None)


class FakeArangoDb:
    """Mimics db.collection(name) returning a collection with insert/delete."""

    def __init__(self):
        self.collections = {"entities_v2": {}, "relationships_v2": {}}

    def collection(self, name):
        return _FakeCollection(self.collections[name])


@pytest.fixture
def fake_qdrant_client():
    return FakeQdrantClient()


@pytest.fixture
def fake_arango_db():
    return FakeArangoDb()
