"""Local bge-m3 embedding service for Embeddington vector search.

Serves the same ``/embed`` contract the shared `technology` collection was built with
(``{"texts": [...], "index": ...}`` -> ``{"embeddings": [[...]]}``), so the bundled MCP's
embedding client talks to it unchanged. Produces L2-normalized 1024-dim bge-m3 vectors —
the exact space the collection lives in (validated: cosine 1.0 vs the builder's output).

The model is loaded at import, so the container is not ``ready`` until the weights are
downloaded and loaded; ``/health`` reflects that.
"""

import os

from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

MODEL_NAME = os.environ.get("EMBED_MODEL", "BAAI/bge-m3")

# Eager load: a single bge-m3 model shared across requests. normalize_embeddings=True is
# applied per call (the collection uses cosine over normalized vectors).
_model = SentenceTransformer(MODEL_NAME)

app = FastAPI(title="embeddington-embed")


class EmbedRequest(BaseModel):
    """Embed request. ``index`` is accepted for API parity but ignored (single model)."""

    texts: list[str]
    index: str | None = None


class EmbedResponse(BaseModel):
    """Embed response: one 1024-dim vector per input text."""

    embeddings: list[list[float]]


@app.get("/health")
def health() -> dict:
    """Report readiness and the served model."""
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "dim": _model.get_sentence_embedding_dimension(),
    }


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest) -> dict:
    """Embed ``texts`` into L2-normalized bge-m3 vectors."""
    vectors = _model.encode(req.texts, normalize_embeddings=True).tolist()
    return {"embeddings": vectors}
