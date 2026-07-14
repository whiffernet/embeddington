"""Consumer-test fixtures: fake qdrant/arango clients matching the adapter call surface."""

import types

import pytest
from arango.exceptions import CollectionListError, DocumentCountError
from arango.request import Request
from arango.response import Response
from qdrant_client import models as qmodels


def _arango_server_error(exc_cls, error_code, message):
    """Build a real python-arango server error, as the driver would raise it on a 404.

    Args:
        exc_cls: The python-arango exception class to construct.
        error_code: ArangoDB ERR code (e.g. 1228 = database not found).
        message: The server's error message.

    Returns:
        An instance of exc_cls carrying an HTTP 404 response.
    """
    resp = Response(
        method="get",
        url="http://fake/_api/collection",
        headers={},
        status_code=404,
        status_text="Not Found",
        raw_body="",
    )
    resp.error_code = error_code
    resp.error_message = message
    return exc_cls(resp, Request(method="get", endpoint="/_api/collection"))


class FakeQdrantClient:
    """Mimics the qdrant_client methods QdrantConsumerWriter calls."""

    def __init__(self):
        self.points = {}  # id -> {"vector","payload"}
        self.exists = True  # flip to False to simulate a missing collection

    def collection_exists(self, collection_name):
        return self.exists

    def count(self, collection_name, exact=True):
        if not self.exists:
            # Mirrors the real qdrant_client, which raises (UnexpectedResponse / 404)
            # rather than returning zero when the collection doesn't exist.
            raise ValueError("collection not found")
        return types.SimpleNamespace(count=len(self.points))

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
            for pid in [p for p, v in self.points.items() if v["payload"].get("filename") == fn]:
                del self.points[pid]


class _FakeCollection:
    """Mimics python-arango's StandardCollection, including its 404 behavior.

    The real handle is LAZY: db.collection(name) never touches the server, so a handle to a
    missing collection (or one in a database that does not exist) constructs happily and only
    blows up on first use. count() raises DocumentCountError; it does NOT return 0.
    """

    def __init__(self, db, name):
        self._db = db
        self._name = name

    @property
    def _store(self):
        return self._db.collections[self._name]

    def count(self):
        if not self._db.db_exists:
            raise _arango_server_error(DocumentCountError, 1228, "database not found")
        if self._name not in self._db.collections:
            raise _arango_server_error(DocumentCountError, 1203, "collection or view not found")
        return len(self._store)

    def insert(self, doc, overwrite=False):
        self._store[doc["_key"]] = doc

    def delete(self, key, ignore_missing=False):
        self._store.pop(key, None)


class FakeArangoDb:
    """Mimics db.collection(name) / db.has_collection(name) on a python-arango database.

    Two knobs reproduce the states a consumer really passes through:
      * ``db_exists = False`` -- the fresh consumer stack, before arangorestore
        ``--create-database`` has made ``technology_kg``. has_collection() and count() both
        raise (HTTP 404), exactly as the driver does.
      * dropping a key from ``collections`` -- the database exists but the collection does
        not; count() on its (lazy) handle raises.
    """

    def __init__(self):
        self.collections = {"entities_v2": {}, "relationships_v2": {}}
        self.db_exists = True  # flip to False to simulate "database not found"

    def has_collection(self, name):
        if not self.db_exists:
            raise _arango_server_error(CollectionListError, 1228, "database not found")
        return name in self.collections

    def collection(self, name):
        return _FakeCollection(self, name)  # lazy: no existence check, like the real client


@pytest.fixture
def fake_qdrant_client():
    return FakeQdrantClient()


@pytest.fixture
def fake_arango_db():
    return FakeArangoDb()
