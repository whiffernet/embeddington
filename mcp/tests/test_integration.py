"""End-to-end integration tests against a live local stack.

Skipped unless ARANGO_TEST_PASSWORD is set. Hits real Qdrant + Arango + /embed
and exercises the full enrich() flow.
"""

import os

import pytest
from arango_client import ArangoKGClient
from embedding_client import EmbeddingClient
from enrich import enrich
from qdrant_client import QdrantSearchClient

# Capture real env values at module import (before _safe_env autouse override)
_QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
_ARANGO_URL = os.environ.get("ARANGO_TEST_URL", "http://localhost:8529")
_ARANGO_USER = os.environ.get("ARANGO_TEST_USER", "root")
_ARANGO_PW = os.environ.get("ARANGO_TEST_PASSWORD", "")
_EMBED_URL = os.environ.get("EMBED_URL", "http://localhost:8100/embed")


pytestmark = pytest.mark.skipif(
    not _ARANGO_PW,
    reason="set ARANGO_TEST_PASSWORD to run",
)


@pytest.mark.asyncio
async def test_enrich_against_live_servicenow_data():
    """Full enrich() round-trip: vector search + KG lookup against live services.

    Skips cleanly when:
    - ARANGO_TEST_PASSWORD is not set (module-level skipif above)
    - /embed is down (embedding service unhealthy — expected during CUBLAS errors)
    - KG has no matching entity for the test query
    """
    embed = EmbeddingClient(url=_EMBED_URL)
    qdrant = QdrantSearchClient(url=_QDRANT_URL, collection="technology")
    arango = ArangoKGClient(
        url=_ARANGO_URL,
        database="technology_kg",
        username=_ARANGO_USER,
        password=_ARANGO_PW,
    )

    result = await enrich(
        query="What is incident management?",
        entity_hints=["Incident Management"],
        top_k=5,
        embedding_client=embed,
        qdrant_client=qdrant,
        arango_client=arango,
    )

    # If embedding service is down, skip cleanly
    if "qdrant" in result["errors"] and "embedding" in result["errors"]["qdrant"].lower():
        await embed.close()
        await qdrant.close()
        pytest.skip(
            f"embedding service unhealthy — re-run when /embed is fixed. "
            f"Error was: {result['errors']['qdrant']}"
        )

    assert result["errors"] == {}, f"unexpected errors: {result['errors']}"
    assert len(result["vector_chunks"]) > 0, "vector side returned nothing"

    # KG side may be empty if 'Incident Management' isn't in the KG — log + soft-assert
    if not result["kg_matches"]:
        await embed.close()
        await qdrant.close()
        pytest.skip("KG has no 'Incident Management' entity — enrich worked but no KG data")
    assert "entity" in result["kg_matches"][0]
    assert "neighbors" in result["kg_matches"][0]

    await embed.close()
    await qdrant.close()
