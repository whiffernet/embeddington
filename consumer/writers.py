"""Real Qdrant/Arango write adapters implementing Plan-1's writer protocols.

These write to the USER's local stack. An edge's ``predicate`` is carried inside the
record's ``doc`` and persisted as an attribute on the relationships_v2 edge (the
consumer half of Plan-1 I1 — no protocol change needed).
"""

from qdrant_client import models as qmodels


class QdrantConsumerWriter:
    """Implements embeddington.apply.protocols.QdrantWriter against a qdrant client."""

    def __init__(self, client, collection):
        self._client = client
        self._collection = collection

    @classmethod
    def connect(cls, url, collection):
        """Construct from a URL (the user's local Qdrant).

        Args:
            url: Base URL of the Qdrant instance (e.g. "http://localhost:6333").
            collection: Name of the Qdrant collection to write to.

        Returns:
            A QdrantConsumerWriter connected to the given collection.
        """
        from qdrant_client import QdrantClient

        return cls(QdrantClient(url=url), collection)

    def upsert_point(self, point_id: str, vector: list[float], payload: dict) -> None:
        """Upsert a single point into the collection.

        Args:
            point_id: Unique identifier for the Qdrant point.
            vector: Embedding vector as a list of floats.
            payload: Metadata dict to store alongside the vector.
        """
        self._client.upsert(
            self._collection,
            points=[qmodels.PointStruct(id=point_id, vector=vector, payload=payload)],
        )

    def delete_point(self, point_id: str) -> None:
        """Delete a single point by id.

        Args:
            point_id: Unique identifier of the point to delete.
        """
        self._client.delete(
            self._collection,
            points_selector=qmodels.PointIdsList(points=[point_id]),
        )

    def delete_points_by_filename(self, filename: str) -> None:
        """Delete all points whose payload.filename matches the given value.

        Args:
            filename: The filename value to match against payload.filename.
        """
        self._client.delete(
            self._collection,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key="filename", match=qmodels.MatchValue(value=filename)
                        )
                    ]
                )
            ),
        )


class ArangoConsumerWriter:
    """Implements embeddington.apply.protocols.ArangoWriter against a python-arango db."""

    def __init__(self, db):
        self._entities = db.collection("entities_v2")
        self._edges = db.collection("relationships_v2")

    @classmethod
    def connect(cls, url, db_name, username, password):
        """Construct from a URL (the user's local Arango).

        Args:
            url: Base URL of the ArangoDB instance (e.g. "http://localhost:8529").
            db_name: Name of the ArangoDB database.
            username: ArangoDB username.
            password: ArangoDB password.

        Returns:
            An ArangoConsumerWriter connected to the given database.
        """
        from arango import ArangoClient

        return cls(ArangoClient(hosts=url).db(db_name, username=username, password=password))

    def upsert_entity(self, key: str, doc: dict) -> None:
        """Upsert an entity vertex into entities_v2.

        Args:
            key: ArangoDB _key for the entity document.
            doc: Attribute dict for the entity vertex.
        """
        self._entities.insert({**doc, "_key": key}, overwrite=True)

    def upsert_edge(self, key: str, from_: str, to: str, doc: dict) -> None:
        """Upsert a relationship edge into relationships_v2.

        The ``predicate`` attribute is carried inside ``doc`` and written as an
        edge attribute (I1 resolution — no protocol-level predicate arg needed).

        Args:
            key: ArangoDB _key for the edge document.
            from_: Full ArangoDB document handle for the source vertex.
            to: Full ArangoDB document handle for the target vertex.
            doc: Extra attributes to store on the edge (must include ``predicate``).
        """
        self._edges.insert({**doc, "_key": key, "_from": from_, "_to": to}, overwrite=True)

    def delete_entity(self, key: str) -> None:
        """Delete an entity vertex by _key, ignoring if already absent.

        Args:
            key: ArangoDB _key of the entity to delete.
        """
        self._entities.delete(key, ignore_missing=True)

    def delete_edge(self, key: str) -> None:
        """Delete a relationship edge by _key, ignoring if already absent.

        Args:
            key: ArangoDB _key of the edge to delete.
        """
        self._edges.delete(key, ignore_missing=True)
