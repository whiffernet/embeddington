"""Tests for the bundled enrich() tool."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from enrich import enrich, _extract_entity_hints


@pytest.mark.asyncio
async def test_enrich_combines_vector_and_kg_results():
    embedding = AsyncMock()
    embedding.embed = AsyncMock(return_value=[0.1] * 1024)

    qdrant = AsyncMock()
    qdrant.search = AsyncMock(
        return_value=[
            {
                "id": "1",
                "score": 0.9,
                "text": "ITSM is...",
                "source": "x.md",
                "metadata": {},
            },
        ]
    )

    arango = MagicMock()
    arango.find_entities = MagicMock(
        return_value=[
            {
                "id": "entities_v2/itsm",
                "name": "ITSM",
                "type": "Module",
                "description": "...",
            },
        ]
    )
    arango.neighbors = MagicMock(
        return_value={
            "nodes": [
                {"id": "entities_v2/incident", "name": "Incident", "type": "Process"}
            ],
            "edges": [
                {
                    "source": "entities_v2/itsm",
                    "target": "entities_v2/incident",
                    "predicate": "contains",
                }
            ],
        }
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
    assert result["kg_matches"][0]["entity"]["name"] == "ITSM"
    assert len(result["kg_matches"][0]["neighbors"]["nodes"]) == 1
    assert result["errors"] == {}


@pytest.mark.asyncio
async def test_enrich_partial_failure_qdrant_down():
    """When Qdrant fails, return empty vector_chunks but still attempt KG."""
    from qdrant_client import QdrantError

    embedding = AsyncMock()
    embedding.embed = AsyncMock(return_value=[0.1] * 1024)

    qdrant = AsyncMock()
    qdrant.search = AsyncMock(side_effect=QdrantError("connection refused"))

    arango = MagicMock()
    arango.find_entities = MagicMock(
        return_value=[
            {
                "id": "entities_v2/itsm",
                "name": "ITSM",
                "type": "Module",
                "description": "",
            },
        ]
    )
    arango.neighbors = MagicMock(return_value={"nodes": [], "edges": []})

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
    embedding = AsyncMock()
    embedding.embed = AsyncMock(return_value=[0.1] * 1024)

    qdrant = AsyncMock()
    qdrant.search = AsyncMock(return_value=[])

    arango = MagicMock()
    arango.find_entities = MagicMock(return_value=[])
    arango.neighbors = MagicMock(return_value={"nodes": [], "edges": []})

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
    hints = _extract_entity_hints(
        "what connects IntegrationHub and Discovery in ServiceNow"
    )
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
