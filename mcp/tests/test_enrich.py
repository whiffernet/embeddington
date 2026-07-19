"""Tests for the bundled enrich() tool (budgeted concept pipeline)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from arango_client import ArangoError
from enrich import _extract_entity_hints, _kg_fetch, _kg_select, _kg_side, enrich


def _entity(eid, name, etype="Feature", degree=10):
    return {
        "id": f"entities_v2/{eid}",
        "name": name,
        "type": etype,
        "source_documents": [],
        "releases": None,
        "degree": degree,
    }


def _edge(eid, src, tgt, predicate="CONTAINS", confidence=0.9):
    return {
        "id": eid,
        "source": f"entities_v2/{src}",
        "target": f"entities_v2/{tgt}",
        "predicate": predicate,
        "confidence": confidence,
        "extraction_type": "explicit",
        "releases": None,
        "source_document": "d",
        "source_quote": "q",
    }


def _stratified(edges, nodes=None):
    return {"nodes": nodes or [], "edges": edges, "fetched": len(edges)}


def _mock_arango():
    a = MagicMock(spec=["find_entities", "neighbors_stratified", "count_edges"])
    a.count_edges = MagicMock(return_value=0)
    return a


def _mock_vector():
    embedding = AsyncMock()
    embedding.embed = AsyncMock(return_value=[0.1] * 1024)
    qdrant = AsyncMock()
    qdrant.search = AsyncMock(
        return_value=[
            {"id": "1", "score": 0.9, "text": "ITSM is...", "source": "x", "metadata": {}}
        ]
    )
    return embedding, qdrant


@pytest.mark.asyncio
async def test_enrich_envelope_keys_always_present():
    embedding, qdrant = _mock_vector()
    arango = _mock_arango()
    arango.find_entities = MagicMock(return_value=[_entity("itsm", "ITSM", "Module")])
    arango.neighbors_stratified = MagicMock(return_value=_stratified([_edge("1", "itsm", "x")]))
    result = await enrich(
        query="what is ITSM",
        entity_hints=["ITSM"],
        top_k=5,
        embedding_client=embedding,
        qdrant_client=qdrant,
        arango_client=arango,
    )
    assert set(result) == {"vector_chunks", "kg_matches", "errors", "budget", "warnings"}
    m = result["kg_matches"][0]
    assert set(m) == {"concept", "variants", "nodes", "edges", "truncation", "suggest", "error"}
    assert m["variants"][0]["name"] == "ITSM"
    assert m["suggest"] is None and m["error"] is None
    assert set(m["truncation"]) == {"truncated", "available", "returned"}


@pytest.mark.asyncio
async def test_enrich_variant_facets_merge_into_one_concept():
    """feature__X + product__X expand as ONE match, edges from BOTH variants."""
    embedding, qdrant = _mock_vector()
    arango = _mock_arango()
    arango.find_entities = MagicMock(
        return_value=[
            _entity("feature__pm", "Process Mining", "Feature", 100),
            _entity("product__pm", "Process Mining", "Product", 50),
        ]
    )
    arango.neighbors_stratified = MagicMock(
        side_effect=[
            _stratified([_edge("f1", "feature__pm", "kpi")]),
            _stratified([_edge("p1", "product__pm", "license", predicate="LICENSED_UNDER")]),
        ]
    )
    result = await enrich(
        query="Process Mining licensing",
        entity_hints=["Process Mining"],
        top_k=5,
        embedding_client=embedding,
        qdrant_client=qdrant,
        arango_client=arango,
    )
    assert len(result["kg_matches"]) == 1
    m = result["kg_matches"][0]
    assert len(m["variants"]) == 2
    preds = {e["predicate"] for e in m["edges"]}
    assert "LICENSED_UNDER" in preds  # the facet edge survived (recall critic #3)
    assert m["truncation"]["available"] == 150  # sum of variant degrees


@pytest.mark.asyncio
async def test_enrich_truncation_flag_and_suggest_on_clip():
    embedding, qdrant = _mock_vector()
    arango = _mock_arango()
    arango.find_entities = MagicMock(return_value=[_entity("hub", "CMDB", degree=5000)])
    arango.neighbors_stratified = MagicMock(
        return_value=_stratified([_edge(str(i), "hub", f"n{i}") for i in range(80)])
    )
    result = await enrich(
        query="CMDB",
        entity_hints=["CMDB"],
        top_k=3,
        edge_budget=10,
        embedding_client=embedding,
        qdrant_client=qdrant,
        arango_client=arango,
    )
    m = result["kg_matches"][0]
    assert m["truncation"]["truncated"] is True
    assert m["truncation"]["returned"] == len(m["edges"]) <= 10
    assert m["suggest"]["kg_neighbors"]["entity_id"] == "entities_v2/hub"
    assert "kg_path" in m["suggest"]["multi_hop"]


@pytest.mark.asyncio
async def test_enrich_per_concept_error_scoping():
    """One concept's Arango failure must not nuke the other match."""
    embedding, qdrant = _mock_vector()
    arango = _mock_arango()
    arango.find_entities = MagicMock(
        return_value=[_entity("good", "Incident"), _entity("bad", "Change")]
    )
    arango.neighbors_stratified = MagicMock(
        side_effect=[_stratified([_edge("1", "good", "x")]), ArangoError("boom")]
    )
    result = await enrich(
        query="Incident vs Change",
        entity_hints=["Incident", "Change"],
        top_k=3,
        embedding_client=embedding,
        qdrant_client=qdrant,
        arango_client=arango,
    )
    good = next(m for m in result["kg_matches"] if m["concept"] == "incident")
    bad = next(m for m in result["kg_matches"] if m["concept"] == "change")
    assert good["edges"] and good["error"] is None
    assert bad["edges"] == [] and "boom" in bad["error"]
    assert "arango" not in result["errors"]  # reserved for TOTAL failure


@pytest.mark.asyncio
async def test_enrich_empty_hints_emits_warning():
    embedding, qdrant = _mock_vector()
    arango = _mock_arango()
    arango.find_entities = MagicMock(return_value=[])
    result = await enrich(
        query="hello there",
        entity_hints=None,
        top_k=3,
        embedding_client=embedding,
        qdrant_client=qdrant,
        arango_client=arango,
    )
    assert result["kg_matches"] == []
    assert any("no entity hints" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_enrich_combines_vector_and_kg_results():
    embedding, qdrant = _mock_vector()
    arango = _mock_arango()
    arango.find_entities = MagicMock(return_value=[_entity("itsm", "ITSM", "Module")])
    arango.neighbors_stratified = MagicMock(
        return_value=_stratified(
            [_edge("1", "itsm", "incident", predicate="contains")],
            nodes=[_entity("incident", "Incident", "Process")],
        )
    )

    result = await enrich(
        query="what is ITSM",
        entity_hints=["ITSM"],
        top_k=5,
        embedding_client=embedding,
        qdrant_client=qdrant,
        arango_client=arango,
    )

    assert len(result["vector_chunks"]) == 1
    assert result["vector_chunks"][0]["text"] == "ITSM is..."
    assert len(result["kg_matches"]) == 1
    assert result["kg_matches"][0]["variants"][0]["name"] == "ITSM"
    assert len(result["kg_matches"][0]["nodes"]) == 1
    assert result["errors"] == {}


@pytest.mark.asyncio
async def test_enrich_vector_preclip_sets_budget_truncated():
    """The 60%-ceiling vector pre-clip (spec §4.1) pops chunks before `budget`
    is built — regression for the bug where `budget.truncated` only looked at
    match truncation and silently missed vector-side clipping."""
    embedding = AsyncMock()
    embedding.embed = AsyncMock(return_value=[0.1] * 1024)
    qdrant = AsyncMock()
    qdrant.search = AsyncMock(
        return_value=[
            {"id": str(i), "score": 0.9, "text": "x" * 30000, "source": "x", "metadata": {}}
            for i in range(5)
        ]
    )
    arango = _mock_arango()
    arango.find_entities = MagicMock(return_value=[])

    result = await enrich(
        query="what is ITSM",
        entity_hints=["ITSM"],
        top_k=5,
        embedding_client=embedding,
        qdrant_client=qdrant,
        arango_client=arango,
    )

    assert len(result["vector_chunks"]) < 5
    assert "response ceiling: vector chunks trimmed" in result["warnings"]
    assert result["budget"]["truncated"] is True


@pytest.mark.asyncio
async def test_enrich_partial_failure_qdrant_down():
    """When Qdrant fails, return empty vector_chunks but still attempt KG."""
    from qdrant_client import QdrantError

    embedding, qdrant = _mock_vector()
    qdrant.search = AsyncMock(side_effect=QdrantError("connection refused"))

    arango = _mock_arango()
    arango.find_entities = MagicMock(return_value=[_entity("itsm", "ITSM", "Module")])
    arango.neighbors_stratified = MagicMock(return_value=_stratified([]))

    result = await enrich(
        query="ITSM",
        entity_hints=["ITSM"],
        top_k=5,
        embedding_client=embedding,
        qdrant_client=qdrant,
        arango_client=arango,
    )

    assert result["vector_chunks"] == []
    assert "qdrant" in result["errors"]
    assert len(result["kg_matches"]) == 1


@pytest.mark.asyncio
async def test_enrich_no_hints_uses_regex_fallback():
    """If entity_hints not provided, regex extracts capitalized terms."""
    embedding, qdrant = _mock_vector()
    arango = _mock_arango()
    arango.find_entities = MagicMock(return_value=[])
    arango.neighbors_stratified = MagicMock(return_value=_stratified([]))

    await enrich(
        query="Tell me about Workflow Studio and PAD",
        entity_hints=None,
        top_k=5,
        embedding_client=embedding,
        qdrant_client=qdrant,
        arango_client=arango,
    )

    # Regex should have extracted "Workflow Studio" (capitalized seq) and "PAD" (acronym)
    called_with = [call.args[0] for call in arango.find_entities.call_args_list]
    assert "Workflow Studio" in called_with
    assert "PAD" in called_with


def test_kg_fetch_plus_select_equals_kg_side():
    """`_kg_side` must remain exactly `_kg_select(_kg_fetch(...))` composed —
    the seam the relevance-injection task (PR 3 task 4) will split apart."""

    class FakeArango:
        def find_entities(self, text, limit=3):
            return [{"id": "entities_v2/a", "name": text, "type": "t", "degree": 4}]

        def neighbors_stratified(self, eid, per_predicate, overall, predicates):
            return {
                "nodes": [{"id": "entities_v2/a"}, {"id": "entities_v2/b"}],
                "edges": [
                    {
                        "id": "e1",
                        "source": "entities_v2/a",
                        "target": "entities_v2/b",
                        "predicate": "P1",
                        "confidence": 0.9,
                        "source_quote": "q1",
                    },
                    {
                        "id": "e2",
                        "source": "entities_v2/a",
                        "target": "entities_v2/b",
                        "predicate": "P2",
                        "confidence": 0.8,
                        "source_quote": "q2",
                    },
                ],
                "fetched": 2,
            }

        def count_edges(self, eid, predicates=None):
            return 2

    fa = FakeArango()
    legacy = _kg_side(["CMDB"], fa, 10, None)
    fetched = _kg_fetch(["CMDB"], fa, 10, None)
    recomposed = _kg_select(fetched, relevance=None, diversity_quota_fraction=0.25)
    assert recomposed == legacy


# --- _extract_entity_hints regex-fallback unit tests ----------------------
# The fallback runs only when a caller passes entity_hints=None. It must catch
# four shapes: multi-word phrases, CamelCase tokens, acronyms, proper nouns.


def test_extract_hints_catches_camelcase_single_token():
    """CamelCase single tokens like IntegrationHub were missed before."""
    hints = _extract_entity_hints("how does IntegrationHub work")
    assert "IntegrationHub" in hints


def test_extract_hints_catches_standalone_proper_noun():
    """Single capitalized proper nouns like Discovery / Server were missed."""
    hints = _extract_entity_hints("describe the Server table in Discovery")
    assert "Server" in hints
    assert "Discovery" in hints


def test_extract_hints_evidence_case_prompt_07():
    """Regression for the reported bug: this query previously extracted []."""
    hints = _extract_entity_hints("what connects IntegrationHub and Discovery in ServiceNow")
    assert "IntegrationHub" in hints
    assert "Discovery" in hints
    assert "ServiceNow" in hints  # CamelCase


def test_extract_hints_still_catches_phrases_and_acronyms():
    """Existing behavior must be preserved."""
    hints = _extract_entity_hints("explain Hardware Asset Management and CMDB")
    assert "Hardware Asset Management" in hints
    assert "CMDB" in hints


def test_extract_hints_does_not_split_phrase_into_proper_nouns():
    """Words inside a captured multi-word phrase must not also appear alone."""
    hints = _extract_entity_hints("explain Hardware Asset Management")
    assert "Hardware Asset Management" in hints
    assert "Hardware" not in hints
    assert "Management" not in hints


def test_extract_hints_excludes_stopwords_and_leading_verbs():
    """Interrogatives and common command verbs must not become hints."""
    hints = _extract_entity_hints("What is Discovery")
    assert hints == ["Discovery"]
    hints2 = _extract_entity_hints("List the Discovery schedules")
    assert "List" not in hints2
    assert "Discovery" in hints2


def test_extract_hints_dedups_and_caps_at_five():
    """Dedup preserves order; result is capped to bound KG fan-out."""
    q = "A B C IntegrationHub Discovery Server Catalog Workspace Flow Designer"
    hints = _extract_entity_hints(q)
    assert len(hints) == len(set(hints))  # no dups
    assert len(hints) <= 5


def test_extract_hints_snake_case_tables():
    hints = _extract_entity_hints("What is the cmdb_rel_ci table used for?")
    assert "cmdb_rel_ci" in hints


def test_extract_hints_snake_case_alongside_capitalized():
    hints = _extract_entity_hints("How does Discovery populate cmdb_rel_ci?")
    assert "Discovery" in hints and "cmdb_rel_ci" in hints
