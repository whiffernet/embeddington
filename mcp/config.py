"""embeddington MCP configuration — env-loaded constants.

All values come from environment variables. server.py loads mcp/.env via
python-dotenv before importing this module (process env wins), so both config
styles work: env vars injected by Claude's JSON config, or a local mcp/.env.
"""

import os

# --- Connectivity ---------------------------------------------------------
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
# v1: no QDRANT_JWT — JWT auth deferred (see spec §5). Future variable: QDRANT_JWT.

ARANGO_URL = os.environ.get("ARANGO_URL", "http://localhost:8529")
ARANGO_DATABASE = os.environ.get("ARANGO_DATABASE", "technology_kg")
ARANGO_USER = os.environ.get("ARANGO_USER", "root")
ARANGO_PASSWORD = os.environ.get("ARANGO_PASSWORD", "")

EMBED_URL = os.environ.get("EMBED_URL", "http://localhost:8100/embed")

HTTP_TIMEOUT = float(os.environ.get("EMBEDDINGTON_TIMEOUT", "30"))

# --- Hardcoded scope (defense-in-depth) -----------------------------------
# The default configuration uses the consumer's own container root user for
# both Qdrant and Arango. A scoped read-only user is optional hardening an
# operator can configure. The Qdrant collection allowlist below is a static
# layer of protection — a collection absent from it is never reachable.
#
# Map: collection name -> /embed index (encoder routing). The llamaindex
# /embed endpoint routes by index name; passing the index ensures the query
# is embedded by the same encoder used to build the collection. Querying with
# the wrong encoder returns orthogonal garbage.
# The index names happen to equal the collection names today, but they are
# semantically independent encoder-routing tokens — a future maintainer must
# not assume they have to match (they can diverge if /embed routing changes).
ALLOWED_QDRANT_COLLECTIONS = {
    "technology": "technology",  # bge-m3 — ServiceNow MD corpus
}
DEFAULT_QDRANT_COLLECTION = os.environ.get("DEFAULT_QDRANT_COLLECTION", "technology")
if DEFAULT_QDRANT_COLLECTION not in ALLOWED_QDRANT_COLLECTIONS:
    raise ValueError(
        f"DEFAULT_QDRANT_COLLECTION={DEFAULT_QDRANT_COLLECTION!r} is not in "
        f"ALLOWED_QDRANT_COLLECTIONS {sorted(ALLOWED_QDRANT_COLLECTIONS)}"
    )
DEFAULT_EMBED_INDEX = ALLOWED_QDRANT_COLLECTIONS[DEFAULT_QDRANT_COLLECTION]

ALLOWED_ARANGO_COLLECTIONS = {
    "entities": "entities_v2",
    "relationships": "relationships_v2",
    "graph": "servicenow_graph_v2",
}
# Note: no FORBIDDEN_QDRANT_COLLECTIONS in v1 — Qdrant has no credential isolation
# yet (see spec §5). Code-level scoping in tool implementations is the only guard.
