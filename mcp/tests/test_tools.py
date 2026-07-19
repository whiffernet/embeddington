"""Tool-level tests for embeddington.

Tests each MCP tool function returns the documented JSON shape, with
clients mocked. Validates the tool surface contract.

Note on FastMCP 3.x: @mcp.tool returns the original function, not a Tool
object. The underlying FunctionTool (which has .fn) is retrieved via
``await srv.mcp.get_tool(name)``. Tests use this pattern instead of the
plan's ``srv.<tool>.fn(...)`` form, which would require FastMCP to return
a Tool wrapper from the decorator (it does not in 3.x).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
import server as srv


@pytest.fixture(autouse=True)
def _mock_clients(monkeypatch):
    """Replace the lazy-init client getters with mocks for every test."""
    fake_embed = AsyncMock()
    fake_embed.embed = AsyncMock(return_value=[0.1] * 1024)

    fake_qdrant = AsyncMock()
    fake_qdrant.search = AsyncMock(
        return_value=[
            {
                "id": "p1",
                "score": 0.9,
                "text": "hello",
                "source": "x.md",
                "metadata": {},
            },
        ]
    )
    fake_qdrant.ensure_chunk_text = AsyncMock(return_value="absent")

    fake_arango = MagicMock()
    fake_arango.find_entities = MagicMock(
        return_value=[
            {
                "id": "entities_v2/x",
                "name": "X",
                "type": "Module",
                "description": "",
            },
        ]
    )
    fake_arango.get_entity = MagicMock(
        return_value={
            "id": "entities_v2/x",
            "name": "X",
            "type": "Module",
        }
    )
    fake_arango.neighbors = MagicMock(return_value={"nodes": [], "edges": []})
    fake_arango.shortest_path = MagicMock(return_value={"nodes": [], "edges": []})
    fake_arango.schema = MagicMock(
        return_value={"entity_types": ["Module"], "predicates": ["uses"]}
    )

    fake_embed.last_index = None
    fake_qdrant.last_collection = None

    def get_embed(index=None):
        fake_embed.last_index = index
        return fake_embed

    def get_qdrant(collection=None):
        fake_qdrant.last_collection = collection
        return fake_qdrant

    monkeypatch.setattr(srv, "_get_embed", get_embed)
    monkeypatch.setattr(srv, "_get_qdrant", get_qdrant)
    monkeypatch.setattr(srv, "_get_arango", lambda: fake_arango)

    # Hermeticity: the lexical-lane globals are process-wide state mutated by
    # _maybe_reensure()/_isolation_sanity_check() in server.py. Without a
    # per-test reset, whichever test in this file runs first "wins" the 60s
    # reensure guard for the rest of the session, and _lexical_status leaks
    # a Mock object forward into every later test that reads it.
    monkeypatch.setattr(srv, "_lexical_status", "absent")
    # Relative offset, not a literal 0.0: time.monotonic()'s epoch is
    # unspecified (often system boot, not wall-clock zero) — in a
    # short-uptime environment 0.0 isn't guaranteed to be >60s in the past,
    # which would make the reensure guard incorrectly no-op. See the same
    # note in test_server_main.py's test_maybe_reensure_throttles_to_once_per_60s.
    monkeypatch.setattr(
        srv, "_lexical_last_reensure", srv.time.monotonic() - srv._LEXICAL_REENSURE_INTERVAL - 1
    )


async def _fn(tool_name: str):
    """Retrieve the underlying function from a registered FastMCP tool.

    In FastMCP 3.x, @mcp.tool returns the original function; the FunctionTool
    wrapper (with .fn) lives inside the server and is accessible via
    await mcp.get_tool(name).

    Args:
        tool_name: Registered tool name string.

    Returns:
        The raw async callable for the tool.
    """
    tool = await srv.mcp.get_tool(tool_name)
    return tool.fn


@pytest.mark.asyncio
async def test_vector_search_returns_chunks():
    fn = await _fn("vector_search")
    result = await fn(query="hello", limit=5)
    assert "results" in result
    assert len(result["results"]) == 1
    assert result["collection"] == "technology"


@pytest.mark.asyncio
async def test_kg_find_entities_returns_list():
    fn = await _fn("kg_find_entities")
    result = await fn(text="X", limit=10)
    assert "entities" in result
    assert result["entities"][0]["name"] == "X"


@pytest.mark.asyncio
async def test_kg_get_entity_returns_doc():
    fn = await _fn("kg_get_entity")
    result = await fn(entity_id="entities_v2/x")
    assert result["entity"]["name"] == "X"


@pytest.mark.asyncio
async def test_kg_neighbors_returns_graph():
    fn = await _fn("kg_neighbors")
    result = await fn(entity_id="entities_v2/x", depth=1)
    assert "nodes" in result
    assert "edges" in result


@pytest.mark.asyncio
async def test_kg_neighbors_default_limit_is_100(monkeypatch):
    """Default cap keeps responses under Claude Code's tool-result cap."""
    captured: dict = {}
    fake = MagicMock()

    def record_neighbors(entity_id, depth=1, types=None, limit=100):
        captured["limit"] = limit
        return {"nodes": [], "edges": []}

    fake.neighbors = record_neighbors
    monkeypatch.setattr(srv, "_get_arango", lambda: fake)

    fn = await _fn("kg_neighbors")
    await fn(entity_id="entities_v2/x")
    assert captured["limit"] == 100


@pytest.mark.asyncio
async def test_kg_neighbors_forwards_explicit_limit(monkeypatch):
    captured: dict = {}
    fake = MagicMock()

    def record_neighbors(entity_id, depth=1, types=None, limit=100):
        captured["limit"] = limit
        return {"nodes": [], "edges": []}

    fake.neighbors = record_neighbors
    monkeypatch.setattr(srv, "_get_arango", lambda: fake)

    fn = await _fn("kg_neighbors")
    await fn(entity_id="entities_v2/x", limit=25)
    assert captured["limit"] == 25


@pytest.mark.asyncio
async def test_kg_path_returns_path():
    fn = await _fn("kg_path")
    result = await fn(from_id="entities_v2/x", to_id="entities_v2/y")
    assert "nodes" in result


@pytest.mark.asyncio
async def test_kg_schema_returns_types():
    fn = await _fn("kg_schema")
    result = await fn()
    assert "entity_types" in result
    assert "predicates" in result


@pytest.mark.asyncio
async def test_enrich_returns_combined(monkeypatch):
    fake_arango = MagicMock(spec=["find_entities", "neighbors_stratified", "count_edges"])
    fake_arango.find_entities = MagicMock(
        return_value=[
            {
                "id": "entities_v2/x",
                "name": "X",
                "type": "Module",
                "degree": 5,
            },
        ]
    )
    fake_arango.neighbors_stratified = MagicMock(
        return_value={
            "nodes": [
                {"id": "entities_v2/x", "name": "X", "type": "Module", "releases": []},
                {"id": "entities_v2/y", "name": "Y", "type": "Module", "releases": []},
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": "entities_v2/x",
                    "target": "entities_v2/y",
                    "predicate": "USES",
                    "confidence": 0.9,
                    "extraction_type": "explicit",
                    "releases": [],
                    "source_document": "doc.md",
                    "source_quote": "X uses Y",
                },
            ],
            "fetched": 1,
        }
    )
    fake_arango.count_edges = MagicMock(return_value=1)
    monkeypatch.setattr(srv, "_get_arango", lambda: fake_arango)

    fn = await _fn("enrich")
    result = await fn(query="X", entity_hints=["X"], top_k=5)
    assert "vector_chunks" in result
    assert "kg_matches" in result
    assert "errors" in result
    assert "budget" in result
    assert "warnings" in result
    assert len(result["kg_matches"]) == 1
    match = result["kg_matches"][0]
    assert match["variants"][0]["id"] == "entities_v2/x"
    assert len(match["edges"]) == 1
    assert match["edges"][0]["predicate"] == "USES"


@pytest.mark.asyncio
async def test_vector_search_defaults_to_technology_m3():
    """No collection arg -> response echoes the default technology collection."""
    fn = await _fn("vector_search")
    result = await fn(query="hello")
    assert result["collection"] == "technology"


@pytest.mark.asyncio
async def test_vector_search_default_routes_m3_index(monkeypatch):
    captured = {}

    def get_embed(index=None):
        captured["index"] = index
        m = AsyncMock()
        m.embed = AsyncMock(return_value=[0.1] * 1024)
        return m

    def get_qdrant(collection=None):
        captured["collection"] = collection
        m = AsyncMock()
        m.search = AsyncMock(return_value=[])
        return m

    monkeypatch.setattr(srv, "_get_embed", get_embed)
    monkeypatch.setattr(srv, "_get_qdrant", get_qdrant)

    fn = await _fn("vector_search")
    await fn(query="hello")
    assert captured["index"] == "technology"
    assert captured["collection"] == "technology"


@pytest.mark.asyncio
async def test_vector_search_unknown_collection_errors_without_building_client(
    monkeypatch,
):
    """Unknown collection -> structured error; no embed/qdrant client touched."""
    touched = {"embed": False, "qdrant": False}

    def get_embed(index=None):
        touched["embed"] = True
        return AsyncMock()

    def get_qdrant(collection=None):
        touched["qdrant"] = True
        return AsyncMock()

    monkeypatch.setattr(srv, "_get_embed", get_embed)
    monkeypatch.setattr(srv, "_get_qdrant", get_qdrant)

    fn = await _fn("vector_search")
    result = await fn(query="hello", collection="unknown_collection")
    assert result["count"] == 0
    assert result["collection"] == "unknown_collection"
    assert "unknown collection" in result["error"]
    assert touched == {"embed": False, "qdrant": False}


@pytest.mark.asyncio
async def test_vector_search_embed_error_returns_structured_error(monkeypatch):
    from embedding_client import EmbeddingError

    def get_embed(index=None):
        m = AsyncMock()
        m.embed = AsyncMock(side_effect=EmbeddingError("embed boom"))
        return m

    monkeypatch.setattr(srv, "_get_embed", get_embed)

    fn = await _fn("vector_search")
    result = await fn(query="hello", collection="technology")
    assert result["count"] == 0
    assert result["results"] == []
    assert result["collection"] == "technology"
    assert "embed boom" in result["error"]


@pytest.mark.asyncio
async def test_enrich_tool_normalizes_and_validates_predicates(monkeypatch):
    captured_kwargs: dict = {}

    async def fake_enrich_impl(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "vector_chunks": [],
            "kg_matches": [],
            "errors": {},
            "budget": {"edge_budget": kwargs.get("edge_budget"), "returned": 0, "truncated": False},
            "warnings": [],
        }

    monkeypatch.setattr(srv, "_enrich_impl", fake_enrich_impl)
    monkeypatch.setattr(srv, "_get_known_predicates", lambda: {"CONTAINS", "REQUIRES_ROLE"})

    fn = await _fn("enrich")
    result = await fn(query="X", predicates=["contains", "bogus_pred"])

    assert captured_kwargs["predicates"] == ["CONTAINS", "BOGUS_PRED"]
    assert any("bogus_pred" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_enrich_tool_skips_predicate_validation_when_schema_unavailable(monkeypatch):
    """_get_known_predicates() -> None (Arango down) must not crash or warn
    spuriously; predicates still forwarded UPPER-cased to the impl."""
    captured_kwargs: dict = {}

    async def fake_enrich_impl(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "vector_chunks": [],
            "kg_matches": [],
            "errors": {},
            "budget": {"edge_budget": kwargs.get("edge_budget"), "returned": 0, "truncated": False},
            "warnings": [],
        }

    monkeypatch.setattr(srv, "_enrich_impl", fake_enrich_impl)
    monkeypatch.setattr(srv, "_get_known_predicates", lambda: None)

    fn = await _fn("enrich")
    result = await fn(query="X", predicates=["contains"])

    assert captured_kwargs["predicates"] == ["CONTAINS"]
    assert not any("contains" in w.lower() for w in result["warnings"])
    assert not any("unknown predicates" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_enrich_tool_default_top_k_is_5():
    import inspect

    from server import enrich as tool

    fn = tool.fn if hasattr(tool, "fn") else tool  # FastMCP wraps the coroutine
    assert inspect.signature(fn).parameters["top_k"].default == 5


@pytest.mark.asyncio
async def test_enrich_tool_defaults_match_tuned_values():
    """The shipped enrich defaults must match the sweep-chosen knee (edge_budget=40, top_k=5).

    Guards against the wired tool default silently drifting from the value
    the Task 11 tuning sweep picked (see battery_results/2026-07-17-sweep.md).
    """
    import inspect

    from server import enrich as tool

    fn = tool.fn if hasattr(tool, "fn") else tool  # FastMCP wraps the coroutine
    params = inspect.signature(fn).parameters
    assert params["edge_budget"].default == 40
    assert params["top_k"].default == 5


@pytest.mark.asyncio
async def test_kg_neighbors_reports_truncation(monkeypatch):
    fake = MagicMock()
    fake.neighbors = MagicMock(
        return_value={
            "nodes": [],
            "edges": [{"id": f"e{i}"} for i in range(100)],
            "fetched": 100,
        }
    )
    monkeypatch.setattr(srv, "_get_arango", lambda: fake)

    fn = await _fn("kg_neighbors")
    result = await fn(entity_id="entities_v2/x", limit=100)
    assert result["truncation"] == {"truncated": True, "available": None, "returned": 100}


@pytest.mark.asyncio
async def test_kg_neighbors_available_none_when_depth_not_1(monkeypatch):
    """count_edges is a depth-1-only count; it isn't a valid ceiling for a
    multi-hop `returned`, so `available` must stay None at depth > 1 even
    when `types` is given."""
    fake = MagicMock()
    fake.neighbors = MagicMock(return_value={"nodes": [], "edges": [{"id": "e1"}], "fetched": 1})
    fake.count_edges = MagicMock(return_value=1)
    monkeypatch.setattr(srv, "_get_arango", lambda: fake)

    fn = await _fn("kg_neighbors")
    result = await fn(entity_id="entities_v2/x", depth=2, types=["USES"])
    assert result["truncation"]["available"] is None
    fake.count_edges.assert_not_called()


@pytest.mark.asyncio
async def test_kg_neighbors_count_edges_failure_keeps_payload(monkeypatch):
    """A count_edges error is secondary enrichment failing, not a neighbors()
    failure — the already-fetched nodes/edges must survive."""
    from arango_client import ArangoError

    fake = MagicMock()
    fake.neighbors = MagicMock(
        return_value={
            "nodes": [{"id": "entities_v2/y", "name": "Y", "type": "Module"}],
            "edges": [{"id": "e1", "predicate": "USES"}],
            "fetched": 1,
        }
    )
    fake.count_edges = MagicMock(side_effect=ArangoError("count boom"))
    monkeypatch.setattr(srv, "_get_arango", lambda: fake)

    fn = await _fn("kg_neighbors")
    result = await fn(entity_id="entities_v2/x", depth=1, types=["USES"])
    assert result["edges"] == [{"id": "e1", "predicate": "USES"}]
    assert result["nodes"] == [{"id": "entities_v2/y", "name": "Y", "type": "Module"}]
    assert result["truncation"]["available"] is None
    assert "error" not in result
