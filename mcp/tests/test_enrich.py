"""Tests for the bundled enrich() tool (budgeted concept pipeline)."""

import math
from unittest.mock import AsyncMock, MagicMock

import budget
import enrich as enrich_mod
import pytest
from arango_client import ArangoError
from embedding_client import EmbeddingError
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
    # These fakes predate embed_batch (PR 3 task 4). They exercise pools whose
    # edges carry source_quote, so relevance scoring WILL be attempted. Route
    # them through the degradation path so their legacy confidence-order
    # expectations keep holding.
    embedding.embed_batch = AsyncMock(side_effect=EmbeddingError("no batch mock"))
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
    assert set(result) == {
        "vector_chunks",
        "kg_matches",
        "errors",
        "budget",
        "warnings",
        "grounding",
    }
    m = result["kg_matches"][0]
    assert set(m) == {"concept", "variants", "nodes", "edges", "truncation", "suggest", "error"}
    assert m["variants"][0]["name"] == "ITSM"
    assert m["suggest"] is None and m["error"] is None
    assert set(m["truncation"]) == {"truncated", "available", "returned"}


@pytest.mark.asyncio
async def test_enrich_default_edge_budget_is_60():
    """enrich()'s own edge_budget default must match the PR 6 (#44) re-tune.

    Guards against `enrich()`'s module-level default silently drifting from
    the value wired through `server.py`'s tool signature (see
    test_tools.test_enrich_tool_defaults_match_tuned_values for the server
    side of this pin, and mcp/tests/gold/PR6-EVIDENCE.md for the evidence).
    Calling without edge_budget exercises the real default end-to-end: the
    envelope's budget.edge_budget echoes whatever was actually used.
    """
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
    assert result["budget"]["edge_budget"] == 60


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


# --- Relevance scoring wired into enrich() (PR 3 task 4) -------------------


class BatchEmbed:
    """Fake embedder: query -> [1,0,...]; quotes score by prefab cosine."""

    def __init__(self, quote_vecs=None, fail_batch=False, fail_single=False):
        self.quote_vecs = quote_vecs or {}
        self.fail_batch = fail_batch
        self.fail_single = fail_single
        self.batch_calls = 0

    async def embed(self, text):
        if self.fail_single:
            raise EmbeddingError("down")
        v = [0.0] * 1024
        v[0] = 1.0
        return v

    async def embed_batch(self, texts):
        self.batch_calls += 1
        if self.fail_batch:
            raise EmbeddingError("batch down")
        out = []
        for t in texts:
            # Real cosine similarity, not just a scaled copy of the query axis:
            # a vector collinear with the query (only dim 0 nonzero) always
            # scores cosine == sign(value) regardless of magnitude, which
            # can't differentiate quotes. Build a genuine unit vector whose
            # angle to the query encodes `value` as its exact cosine:
            # dim 0 = value (the "on-axis" component), dim 1 fills out the
            # unit circle so |v| == 1 and cos(query, v) == value exactly.
            value = self.quote_vecs.get(t, 0.0)
            v = [0.0] * 1024
            v[0] = value
            v[1] = math.sqrt(max(0.0, 1.0 - value * value))
            out.append(v)
        return out


class OkQdrant:
    async def search(self, vector, limit, match_text=None):
        return [{"id": "c1", "text": "chunk"}]


class RelArango:
    """Two edges, same predicate: e_rel has a relevant quote, e_conf high confidence."""

    def find_entities(self, text, limit=3):
        return [{"id": "entities_v2/a", "name": text, "type": "t", "degree": 2}]

    def neighbors_stratified(self, eid, per_predicate, overall, predicates):
        return {
            "nodes": [{"id": "entities_v2/a"}, {"id": "entities_v2/b"}],
            "edges": [
                {
                    "id": "e_conf",
                    "source": "entities_v2/a",
                    "target": "entities_v2/b",
                    "predicate": "P1",
                    "confidence": 0.99,
                    "source_quote": "boring quote",
                },
                {
                    "id": "e_rel",
                    "source": "entities_v2/a",
                    "target": "entities_v2/b",
                    "predicate": "P1",
                    "confidence": 0.10,
                    "source_quote": "on-point quote",
                },
            ],
            "fetched": 2,
        }

    def count_edges(self, eid, predicates=None):
        return 2


@pytest.mark.asyncio
async def test_enrich_selection_follows_relevance():
    embed = BatchEmbed(quote_vecs={"on-point quote": 0.9, "boring quote": 0.1})
    res = await enrich(
        query="q",
        entity_hints=["A"],
        top_k=1,
        edge_budget=1,
        embedding_client=embed,
        qdrant_client=OkQdrant(),
        arango_client=RelArango(),
    )
    kept = [e["id"] for m in res["kg_matches"] for e in m["edges"]]
    assert kept == ["e_rel"]  # relevance beat confidence
    assert embed.batch_calls == 1  # ONE batch call for all quotes


@pytest.mark.asyncio
async def test_enrich_degrades_to_confidence_when_batch_embed_fails():
    embed = BatchEmbed(fail_batch=True)
    res = await enrich(
        query="q",
        entity_hints=["A"],
        top_k=1,
        edge_budget=1,
        embedding_client=embed,
        qdrant_client=OkQdrant(),
        arango_client=RelArango(),
    )
    kept = [e["id"] for m in res["kg_matches"] for e in m["edges"]]
    assert kept == ["e_conf"]  # legacy confidence order
    assert any("relevance scoring unavailable" in w for w in res["warnings"])
    assert res["errors"] == {}  # degradation, not an error


@pytest.mark.asyncio
async def test_enrich_total_embed_failure_still_returns_kg():
    embed = BatchEmbed(fail_batch=True, fail_single=True)
    res = await enrich(
        query="q",
        entity_hints=["A"],
        top_k=1,
        edge_budget=1,
        embedding_client=embed,
        qdrant_client=OkQdrant(),
        arango_client=RelArango(),
    )
    # Vector side reports its error; KG side still answers via legacy selection.
    assert "qdrant" in res["errors"] or "embedding" in str(res["errors"])
    kept = [e["id"] for m in res["kg_matches"] for e in m["edges"]]
    assert kept == ["e_conf"]


class QuotaPoolArango:
    """One concept, predicate P1 (2 edges) + predicate P2 (1 edge) — quota-vs-fill
    selection differs depending on diversity_quota_fraction."""

    def find_entities(self, text, limit=3):
        return [{"id": "entities_v2/a", "name": text, "type": "t", "degree": 3}]

    def neighbors_stratified(self, eid, per_predicate, overall, predicates):
        return {
            "nodes": [
                {"id": "entities_v2/a"},
                {"id": "entities_v2/b"},
                {"id": "entities_v2/c"},
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": "entities_v2/a",
                    "target": "entities_v2/b",
                    "predicate": "P1",
                    "confidence": 0.5,
                    "source_quote": "quote high",
                },
                {
                    "id": "e2",
                    "source": "entities_v2/a",
                    "target": "entities_v2/b",
                    "predicate": "P1",
                    "confidence": 0.5,
                    "source_quote": "quote med",
                },
                {
                    "id": "e3",
                    "source": "entities_v2/a",
                    "target": "entities_v2/c",
                    "predicate": "P2",
                    "confidence": 0.5,
                    "source_quote": "quote low",
                },
            ],
            "fetched": 3,
        }

    def count_edges(self, eid, predicates=None):
        return 3


@pytest.mark.asyncio
async def test_enrich_diversity_quota_fraction_plumbs_through_to_select_edges():
    """Regression: nothing previously verified diversity_quota_fraction actually
    flows enrich() -> _kg_select -> select_edges (prior reviewer flag, PR 3 task 4).

    edge_budget=2 -> n_slots=2 for the single concept. quote_vecs give edges a
    strict relevance ranking e1 > e2 > e3, with P2 (e3) always the worst quote.
    A wide quota (fraction=1.0 -> quota=2) reserves a slot for P2's diversity
    pick even though it ranks lowest; a narrow quota (fraction=0.1 -> quota=1,
    the max(1, ...) floor) fills purely by relevance rank and drops P2.
    """
    quote_vecs = {"quote high": 0.9, "quote med": 0.5, "quote low": 0.1}

    res_wide = await enrich(
        query="q",
        entity_hints=["A"],
        top_k=1,
        edge_budget=2,
        embedding_client=BatchEmbed(quote_vecs=quote_vecs),
        qdrant_client=OkQdrant(),
        arango_client=QuotaPoolArango(),
        diversity_quota_fraction=1.0,
    )
    kept_wide = {e["id"] for m in res_wide["kg_matches"] for e in m["edges"]}

    res_narrow = await enrich(
        query="q",
        entity_hints=["A"],
        top_k=1,
        edge_budget=2,
        embedding_client=BatchEmbed(quote_vecs=quote_vecs),
        qdrant_client=OkQdrant(),
        arango_client=QuotaPoolArango(),
        diversity_quota_fraction=0.1,
    )
    kept_narrow = {e["id"] for m in res_narrow["kg_matches"] for e in m["edges"]}

    assert kept_wide == {"e1", "e3"}  # full quota keeps the P2 edge despite low relevance
    assert kept_narrow == {"e1", "e2"}  # minimal quota fills by relevance rank, drops P2
    assert kept_wide != kept_narrow


# --- Hybrid lexical lane + score threshold wired into _vector_side/enrich() ---
# (spec §5 PR 4, issue #38)


class GoodEmbed:
    """Fake embedder: fixed 1024-dim vector for both embed() and embed_batch().

    Reused across the hybrid-lane tests below, where only Qdrant lane
    behavior (dense/lexical fan-out, threshold, RRF merge) is under test.
    """

    async def embed(self, text):
        return [0.1] * 1024

    async def embed_batch(self, texts):
        return [[0.1] * 1024 for _ in texts]


@pytest.mark.asyncio
async def test_vector_side_threshold_drops_weak_chunks():
    class Q:
        async def search(self, vector, limit, match_text=None):
            return [
                {"id": "hi", "score": 0.8, "text": "hi"},
                {"id": "lo", "score": 0.1, "text": "lo"},
            ]

    res = await enrich_mod._vector_side(
        "plain prose query", 5, GoodEmbed(), Q(), score_threshold=0.5
    )
    assert [c["id"] for c in res["chunks"]] == ["hi"]  # fewer than top_k, not padded


@pytest.mark.asyncio
async def test_vector_side_lexical_lane_merges_identifier_hits():
    calls = []

    class Q:
        async def search(self, vector, limit, match_text=None):
            calls.append(match_text)
            if match_text == "cmdb_rel_ci":
                return [{"id": "lex", "score": 0.2, "text": "cmdb_rel_ci doc"}]
            return [{"id": "dense", "score": 0.9, "text": "dense"}]

    res = await enrich_mod._vector_side(
        "What does the cmdb_rel_ci table store?", 5, GoodEmbed(), Q(), lexical_ready=True
    )
    ids = [c["id"] for c in res["chunks"]]
    assert "lex" in ids and "dense" in ids
    assert calls == [None, "cmdb_rel_ci"]
    assert res["lexical"] == {"tokens": ["cmdb_rel_ci"], "active": True}


@pytest.mark.asyncio
async def test_vector_side_lexical_lane_postfilters_to_literal_token():
    """Qdrant's word tokenizer splits identifiers on underscores/punctuation,
    so MatchText("cmdb_rel_ci") admits any chunk containing the scattered
    subtokens {cmdb, rel, ci} anywhere — not just chunks with the literal
    identifier (live-validation defect, issue #38: 5/5 real smoke hits
    lacked the literal token). The lexical lane must post-filter each hit
    down to chunks whose text contains the literal token (case-insensitive)
    before fusion, and over-fetch (limit=max(top_k*2, 25), a measured depth
    floor for common-subtoken identifiers) to give that filtering some
    headroom."""
    calls = []

    class Q:
        async def search(self, vector, limit, match_text=None):
            calls.append((match_text, limit))
            if match_text == "cmdb_rel_ci":
                return [
                    # Mixed case pins the postfilter's case-insensitivity —
                    # an all-lowercase fixture would pass even if the `.lower()`
                    # on the text side were dropped.
                    {"id": "literal", "score": 0.5, "text": "the CMDB_Rel_CI table stores rows"},
                    {"id": "scattered", "score": 0.5, "text": "the cmdb and rel and ci tables"},
                ]
            return [{"id": "dense", "score": 0.9, "text": "dense"}]

    res = await enrich_mod._vector_side(
        "What does the cmdb_rel_ci table store?", 5, GoodEmbed(), Q(), lexical_ready=True
    )
    ids = [c["id"] for c in res["chunks"]]
    assert "literal" in ids
    assert "scattered" not in ids  # scattered-subtoken match dropped by the postfilter
    assert ("cmdb_rel_ci", 25) in calls  # lexical lane over-fetches at max(top_k*2, 25) = 25


@pytest.mark.asyncio
async def test_vector_side_lexical_skipped_when_not_ready():
    calls = []

    class Q:
        async def search(self, vector, limit, match_text=None):
            calls.append(match_text)
            return [{"id": "dense", "score": 0.9, "text": "dense"}]

    res = await enrich_mod._vector_side(
        "What does the cmdb_rel_ci table store?", 5, GoodEmbed(), Q(), lexical_ready=False
    )
    assert calls == [None]
    assert res["lexical"] == {"tokens": ["cmdb_rel_ci"], "active": False}


@pytest.mark.asyncio
async def test_vector_side_lexical_lane_failure_degrades_active_false():
    """A lexical lane search raising must not fail the call — it logs, drops
    that lane's results, and reports active: False (spec §5 PR 4)."""

    from qdrant_client import QdrantError

    class Q:
        async def search(self, vector, limit, match_text=None):
            if match_text is not None:
                raise QdrantError("lexical lane boom")
            return [{"id": "dense", "score": 0.9, "text": "dense"}]

    res = await enrich_mod._vector_side(
        "What does the cmdb_rel_ci table store?", 5, GoodEmbed(), Q(), lexical_ready=True
    )
    assert [c["id"] for c in res["chunks"]] == ["dense"]  # dense-only, lexical lane dropped
    assert res["lexical"] == {"tokens": ["cmdb_rel_ci"], "active": False}
    assert res["error"] is None  # a lexical-only failure is not a top-level error


@pytest.mark.asyncio
async def test_vector_side_threshold_applies_only_to_dense_lane_pre_merge():
    """The score threshold must filter the DENSE lane BEFORE fusion — never
    the lexical lane, and never the already-fused result. Kills mutants that
    swap the apply-threshold/merge order or apply the threshold to both
    lanes (a lexical hit scoring below threshold must still survive)."""

    class Q:
        async def search(self, vector, limit, match_text=None):
            if match_text == "cmdb_rel_ci":
                # Postfilter to literal token (defect 1 fix) — text must
                # contain "cmdb_rel_ci" verbatim to survive into the lane.
                return [{"id": "lex", "score": 0.1, "text": "cmdb_rel_ci lex doc"}]
            return [
                {"id": "hi", "score": 0.8, "text": "hi"},
                {"id": "lo", "score": 0.1, "text": "lo"},
            ]

    res = await enrich_mod._vector_side(
        "What does the cmdb_rel_ci table store?",
        5,
        GoodEmbed(),
        Q(),
        score_threshold=0.5,
        lexical_ready=True,
    )
    ids = [c["id"] for c in res["chunks"]]
    assert "hi" in ids  # dense chunk above threshold survives
    assert "lo" not in ids  # dense chunk below threshold dropped
    assert "lex" in ids  # lexical hit survives despite scoring below the dense threshold


@pytest.mark.asyncio
async def test_vector_side_fused_result_capped_at_top_k():
    """Dense + lexical lanes together can surface more than top_k distinct
    ids; the fused result must still be capped to top_k. Kills a mutant that
    drops (or defaults away, e.g. limit=None) the rrf_merge cap."""

    class Q:
        async def search(self, vector, limit, match_text=None):
            if match_text == "cmdb_rel_ci":
                # Postfilter to literal token (defect 1 fix) — text must
                # contain "cmdb_rel_ci" verbatim to survive into the lane.
                return [
                    {"id": f"lex{i}", "score": 0.9, "text": f"cmdb_rel_ci lex{i}"} for i in range(3)
                ]
            return [{"id": f"dense{i}", "score": 0.9, "text": f"dense{i}"} for i in range(3)]

    res = await enrich_mod._vector_side(
        "What does the cmdb_rel_ci table store?", 2, GoodEmbed(), Q(), lexical_ready=True
    )
    assert len(res["chunks"]) == 2


@pytest.mark.asyncio
async def test_enrich_warns_when_lexical_degraded():
    # identifier query + lexical_ready=False -> explicit envelope warning
    res = await enrich_mod.enrich(
        query="What does the cmdb_rel_ci table store?",
        entity_hints=["cmdb_rel_ci"],
        top_k=2,
        edge_budget=4,
        embedding_client=BatchEmbed(),
        qdrant_client=OkQdrant(),
        arango_client=RelArango(),
        lexical_ready=False,
    )
    assert "lexical lane degraded — chunk_text index not ready" in res["warnings"]


# --- Grounding tier attached to the enrich envelope (spec §5 PR 5, issue #47) ---


@pytest.mark.asyncio
async def test_enrich_grounding_none_on_empty_retrieval():
    class EmptyQdrant:
        async def search(self, vector, limit, match_text=None):
            return []

    class EmptyArango:
        def find_entities(self, text, limit=3):
            return []

    res = await enrich_mod.enrich(
        query="purple elephant quantum recipes",
        entity_hints=None,
        top_k=5,
        edge_budget=40,
        embedding_client=BatchEmbed(),
        qdrant_client=EmptyQdrant(),
        arango_client=EmptyArango(),
    )
    assert res["grounding"]["tier"] == "none"
    assert res["vector_chunks"] == [] and res["kg_matches"] == []
    # Issue #47 acceptance: no invented identifiers on empty retrieval — the
    # envelope carries NO entity/table-like content at all.
    assert "sn_" not in str(res["vector_chunks"]) + str(res["kg_matches"])


@pytest.mark.asyncio
async def test_enrich_grounding_weak_when_asked_identifier_absent():
    # THE incident-class regression (spec §5 PR 5): on-topic content, asked-for
    # identifier missing from every returned chunk and edge quote.
    class OnTopicQdrant:
        async def search(self, vector, limit, match_text=None):
            if match_text:  # lexical lane finds nothing for the fake id
                return []
            return [{"id": "c1", "score": 0.8, "text": "hardware assets are tracked in tables"}]

    res = await enrich_mod.enrich(
        query="What is the sn_zz_fake_table used for?",
        entity_hints=["hardware"],
        top_k=5,
        edge_budget=40,
        embedding_client=BatchEmbed(),
        qdrant_client=OnTopicQdrant(),
        arango_client=RelArango(),
        lexical_ready=True,
    )
    assert res["grounding"]["tier"] == "weak"
    assert any("sn_zz_fake_table" in r for r in res["grounding"]["reasons"])
    # The fake identifier appears nowhere in returned content:
    payload = str(res["vector_chunks"]) + str(res["kg_matches"])
    assert "sn_zz_fake_table" not in payload


@pytest.mark.asyncio
async def test_enrich_grounding_ok_on_normal_retrieval():
    embed = BatchEmbed(quote_vecs={"on-point quote": 0.9, "boring quote": 0.1})
    res = await enrich_mod.enrich(
        query="q",
        entity_hints=["A"],
        top_k=1,
        edge_budget=1,
        embedding_client=embed,
        qdrant_client=OkQdrant(),
        arango_client=RelArango(),
    )
    assert res["grounding"] == {"tier": "ok", "reasons": []}


@pytest.mark.asyncio
async def test_grounding_classified_after_ceiling_trim(monkeypatch):
    # If the trim empties a half, grounding must reflect the FINAL envelope.
    #
    # Branch this fixture actually exercises (verified by direct run): with
    # top_k=1/edge_budget=1, trim_to_ceiling's kg-edge loop only pops edges
    # from matches holding MORE than `floor` (3) edges — this match has just
    # 1 — and the vector-chunk loop never drops below 1 chunk. So even at
    # max_response_tokens=1 the envelope keeps its single non-empty chunk and
    # single edge (just flags "exceeds ceiling even at floors" in warnings)
    # rather than emptying either half. Grounding is computed from that
    # still-non-empty final content, so tier is "ok".
    embed = BatchEmbed()
    res = await enrich_mod.enrich(
        query="q",
        entity_hints=["A"],
        top_k=1,
        edge_budget=1,
        embedding_client=embed,
        qdrant_client=OkQdrant(),
        arango_client=RelArango(),
        max_response_tokens=1,  # brutal ceiling: trim floors everything it can
    )
    g = res["grounding"]
    n_edges = sum(len(m["edges"]) for m in res["kg_matches"])
    if not res["vector_chunks"] and n_edges == 0:
        assert g["tier"] == "none"
    else:
        assert (g["tier"] == "ok") == (bool(res["vector_chunks"]) and n_edges > 0)


class SixEdgeArango:
    """One concept, 6 edges on 6 distinct predicates. The asked-for
    identifier lives ONLY in the lowest-relevance edge's quote ("e_tail").
    With distinct predicates, select_edges' quota-then-fill selection
    collapses to plain relevance order (pass 1 takes the top `quota` by rank
    since each is trivially its predicate's "best" edge; pass 2 continues
    the same rank-ordered walk for the rest) — so e_tail is last in the
    selected `edges` list, and trim_to_ceiling's victim rule pops from the
    tail first.
    """

    def find_entities(self, text, limit=3):
        return [{"id": "entities_v2/a", "name": text, "type": "t", "degree": 6}]

    def neighbors_stratified(self, eid, per_predicate, overall, predicates):
        edges = [
            {
                "id": f"e{i}",
                "source": "entities_v2/a",
                "target": "entities_v2/b",
                "predicate": f"P{i}",
                "confidence": 0.5,
                "source_quote": f"quote {i}",
            }
            for i in range(1, 6)
        ]
        edges.append(
            {
                "id": "e_tail",
                "source": "entities_v2/a",
                "target": "entities_v2/b",
                "predicate": "P6",
                "confidence": 0.5,
                "source_quote": "sn_ci_relationship governs this link",
            }
        )
        return {
            "nodes": [{"id": "entities_v2/a"}, {"id": "entities_v2/b"}],
            "edges": edges,
            "fetched": 6,
        }

    def count_edges(self, eid, predicates=None):
        return 6


class OneChunkQdrant:
    async def search(self, vector, limit, match_text=None):
        return [{"id": "c1", "score": 0.8, "text": "unrelated background prose"}]


@pytest.mark.asyncio
async def test_grounding_reflects_post_trim_not_pre_trim_content():
    """Order-discriminating regression (review fix, issue #47): a
    classify-before-trim bug is invisible to the brief's other fixtures
    because none of them make pre- and post-trim content diverge. Here the
    queried identifier lives ONLY in the tail (lowest-relevance) KG edge's
    quote — SixEdgeArango's distinct-predicate pool puts it last in the
    selected edges (see class docstring), so a ceiling tuned to force
    exactly one edge eviction removes precisely that edge. A
    classify-before-trim bug would still see the quote pre-trim and report
    "ok"; the required post-trim order must report "weak" with the
    identifier named as missing.

    The ceiling is derived programmatically, not a magic number: an
    uncapped run establishes the full pre-trim envelope's token estimate
    (`full_size`); trim_to_ceiling only acts while strictly `over()`, so
    `max_response_tokens = full_size - 1` forces exactly one over-budget
    iteration. One evicted edge is far more than 1 token's worth of JSON, so
    that single pop already clears the ceiling and the loop stops — see the
    assertion below that only `e_tail` (and nothing else) is gone.
    """
    quote_vecs = {f"quote {i}": 1.0 - i * 0.1 for i in range(1, 6)}  # 0.9 .. 0.5
    quote_vecs["sn_ci_relationship governs this link"] = -0.9  # worst relevance -> tail
    query = "What is sn_ci_relationship used for?"

    full = await enrich_mod.enrich(
        query=query,
        entity_hints=["A"],
        top_k=1,
        edge_budget=6,
        embedding_client=BatchEmbed(quote_vecs=quote_vecs),
        qdrant_client=OneChunkQdrant(),
        arango_client=SixEdgeArango(),
        max_response_tokens=10**6,
    )
    # Sanity: uncapped, the identifier IS present pre-trim (fetched, selected,
    # not yet evicted) — confirms the fixture actually exercises the tail
    # edge the ordering claim depends on.
    assert full["grounding"]["tier"] == "ok"
    full_edge_ids = [e["id"] for e in full["kg_matches"][0]["edges"]]
    assert full_edge_ids[-1] == "e_tail"
    # trim_to_ceiling's `over()` check runs BEFORE `grounding` is attached
    # (enrich() adds it after trim returns) — size against the same envelope
    # shape trim actually sees, or the derived ceiling undershoots.
    full_pre_grounding = {k: v for k, v in full.items() if k != "grounding"}
    full_size = budget.estimate_tokens(full_pre_grounding)

    res = await enrich_mod.enrich(
        query=query,
        entity_hints=["A"],
        top_k=1,
        edge_budget=6,
        embedding_client=BatchEmbed(quote_vecs=quote_vecs),
        qdrant_client=OneChunkQdrant(),
        arango_client=SixEdgeArango(),
        max_response_tokens=full_size - 1,
    )
    edges = res["kg_matches"][0]["edges"]
    assert [e["id"] for e in edges] == [f"e{i}" for i in range(1, 6)]  # only e_tail evicted
    assert len(edges) >= 3  # never trimmed below the per-concept floor
    assert res["grounding"]["tier"] == "weak"
    assert any("sn_ci_relationship" in r for r in res["grounding"]["reasons"])
    payload = str(res["vector_chunks"]) + str(res["kg_matches"])
    assert "sn_ci_relationship" not in payload
