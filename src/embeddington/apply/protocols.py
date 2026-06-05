"""Narrow writer interfaces the apply layer depends on (real adapters in a later plan)."""

from typing import Protocol


class QdrantWriter(Protocol):
    """Minimal write surface for the technology collection."""

    def upsert_point(
        self, point_id: str, vector: list[float], payload: dict
    ) -> None: ...

    def delete_point(self, point_id: str) -> None: ...

    def delete_points_by_filename(self, filename: str) -> None: ...


class ArangoWriter(Protocol):
    """Minimal write surface for entities_v2 / relationships_v2."""

    def upsert_entity(self, key: str, doc: dict) -> None: ...

    def upsert_edge(self, key: str, from_: str, to: str, doc: dict) -> None: ...

    def delete_entity(self, key: str) -> None: ...

    def delete_edge(self, key: str) -> None: ...
