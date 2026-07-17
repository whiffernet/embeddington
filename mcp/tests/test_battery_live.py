"""Tier-2 live gates against the battery stack (spark-only; spec §7).

Skips entirely unless EMBEDDINGTON_BATTERY=1. Preflight asserts the restore
matches the baseline manifest — an empty graph must FAIL, not pass-by-vacuity.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("EMBEDDINGTON_BATTERY") != "1",
    reason="live battery: set EMBEDDINGTON_BATTERY=1 with the stack restored",
)

EXPECTED_POINTS = int(os.environ.get("BATTERY_EXPECTED_POINTS", "152194"))
EXPECTED_EDGES = int(os.environ.get("BATTERY_EXPECTED_EDGES", "683651"))
CEILING_TOKENS = 12000


@pytest.fixture(autouse=True)
def _fresh_async_clients():
    """Rebuild the server's cached embed/qdrant clients for each test.

    The server caches its async httpx clients for process-lifetime reuse — correct
    for the production MCP server, which runs on one persistent event loop. But
    pytest-asyncio gives each test its OWN event loop, and a cached httpx client
    from a prior (now-closed) loop raises "Event loop is closed" on reuse (the
    client's own ``is_closed`` stays False, so the lazy getter never rebuilds it).
    Clearing the caches makes each test construct its clients inside its own loop.
    The Arango client is synchronous (python-arango) and not loop-bound, so it is
    left cached.
    """
    import server

    server._embed_clients.clear()
    server._qdrant_clients.clear()
    yield


@pytest.fixture(scope="session", autouse=True)
def preflight():
    import config
    import httpx
    from arango_client import ArangoKGClient

    r = httpx.get(f"{config.QDRANT_URL}/collections/technology", timeout=10)
    points = r.json()["result"]["points_count"]
    assert points == EXPECTED_POINTS, f"restore mismatch: {points} points"
    client = ArangoKGClient(
        url=config.ARANGO_URL,
        database=config.ARANGO_DATABASE,
        username=config.ARANGO_USER,
        password=config.ARANGO_PASSWORD,
    )
    edges = client._db.collection("relationships_v2").count()
    assert edges == EXPECTED_EDGES, f"restore mismatch: {edges} edges"


async def _run(q: dict) -> dict:
    import server

    # Call through the enrich tool for full server wiring (predicate
    # normalization + live client construction). FastMCP 3.x's @mcp.tool
    # returns the original function, so `server.enrich` is directly callable;
    # older/other versions wrap it in a FunctionTool exposing `.fn`. Handle
    # both, matching tests/test_tools.py.
    fn = server.enrich.fn if hasattr(server.enrich, "fn") else server.enrich
    return await fn(
        query=q["query"],
        entity_hints=q["entity_hints"],
        top_k=q["top_k"],
        edge_budget=q["edge_budget"],
        predicates=q["predicates"],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("q", __import__("battery_queries").QUERIES, ids=lambda q: q["name"])
async def test_every_battery_query_fits_ceiling(q):
    from budget import estimate_tokens

    result = await _run(q)
    assert estimate_tokens(result) <= CEILING_TOKENS
    assert result["errors"] == {}


@pytest.mark.asyncio
async def test_case1_and_case2_grounding_intact():
    from battery_queries import CASE_1, CASE_2

    for q in (CASE_1, CASE_2):
        result = await _run(q)
        edges = [e for m in result["kg_matches"] for e in m["edges"]]
        assert edges, f"{q['name']}: no edges returned"
        for e in edges:
            assert e["source_quote"] and e["source_document"] is not None
            assert "extraction_type" in e and "confidence" in e and "releases" in e


@pytest.mark.asyncio
async def test_hub_truncation_is_explicit():
    from battery_queries import HUBS

    result = await _run(HUBS[3])  # CMDB
    m = result["kg_matches"][0]
    assert m["truncation"]["truncated"] is True
    assert m["truncation"]["available"] > m["truncation"]["returned"]
    assert m["suggest"] is not None


@pytest.mark.asyncio
async def test_multifacet_concept_merges_and_keeps_facets():
    from battery_queries import CONTROLS

    result = await _run(CONTROLS[2])  # Process Mining license question
    pm = [m for m in result["kg_matches"] if "process mining" in m["concept"]]
    assert len(pm) == 1, "feature/product/module variants must be ONE concept"
    assert len(pm[0]["variants"]) >= 2
    edge_endpoints = {e["source"] for e in pm[0]["edges"]} | {e["target"] for e in pm[0]["edges"]}
    variant_ids = {v["id"] for v in pm[0]["variants"]}
    assert len(edge_endpoints & variant_ids) >= 2, "edges must come from >=2 facets"
