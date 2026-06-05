import copy

from embeddington.apply import apply_diff
from embeddington.format import records


def _bundle():
    return [
        records.header("1.0", "a1", "b2", points=1, entities=1, edges=1),
        records.point_upsert("p1", [0.1, 0.2], {"filename": "a.md"}),
        records.entity_upsert("E1", {"name": "CMDB"}),
        records.edge_upsert("R1", "entities_v2/E1", "entities_v2/E2", "USES", {}),
        records.point_delete_by_filename("old.md"),
    ]


def test_applying_twice_yields_identical_state(fake_qdrant, fake_arango):
    apply_diff.apply_diff(_bundle(), fake_qdrant, fake_arango)
    after_first = (
        copy.deepcopy(fake_qdrant.points),
        copy.deepcopy(fake_arango.entities),
        copy.deepcopy(fake_arango.edges),
    )

    apply_diff.apply_diff(_bundle(), fake_qdrant, fake_arango)  # re-apply
    after_second = (fake_qdrant.points, fake_arango.entities, fake_arango.edges)

    assert after_first == after_second  # no duplicates, no drift
