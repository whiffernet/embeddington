"""embeddington MCP configuration — env-loaded constants.

All values come from environment variables. server.py loads mcp/.env via
python-dotenv before importing this module (process env wins), so both config
styles work: env vars injected by Claude's JSON config, or a local mcp/.env.
"""

import os

# --- Connectivity ---------------------------------------------------------
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
# Optional API key, for a Qdrant that requires authentication (Qdrant Cloud, or a
# self-hosted instance with `service.api_key` set). Sent as the `api-key` header.
#
# None means send no credential at all, which is the default and what the bundled
# compose file's local Qdrant expects. Blank is normalised to None: an empty
# `api-key` header is rejected by an authenticated Qdrant, and a whitespace-only
# string is truthy in Python, so a stray space in a config file would otherwise
# become a puzzling 401 instead of the keyless behaviour the user intended.
QDRANT_API_KEY = (os.environ.get("QDRANT_API_KEY") or "").strip() or None

ARANGO_URL = os.environ.get("ARANGO_URL", "http://localhost:8529")
ARANGO_DATABASE = os.environ.get("ARANGO_DATABASE", "technology_kg")
ARANGO_USER = os.environ.get("ARANGO_USER", "root")
ARANGO_PASSWORD = os.environ.get("ARANGO_PASSWORD", "")

EMBED_URL = os.environ.get("EMBED_URL", "http://localhost:8100/embed")

HTTP_TIMEOUT = float(os.environ.get("EMBEDDINGTON_TIMEOUT", "30"))

# --- Startup Qdrant reachability probe (_isolation_sanity_check) ----------
# Retries only cover transport failures — the host is down, still starting, or
# unreachable. A real rejection (401/404) or a permanent misconfiguration (a
# typo'd URL scheme) still fails on the first attempt; see
# RETRYABLE_TRANSPORT_ERRORS in qdrant_client.py.
#
# Scope, honestly stated: this rides out a restart measured in seconds (a
# `docker compose restart`, a container that lost its dependency briefly). It
# does NOT cover a host that is down for minutes — no bounded startup retry
# can, and attempting it would hang startup past the MCP client's own
# initialization timeout. A long outage is the reconnect path's problem.
QDRANT_STARTUP_RETRIES = int(os.environ.get("EMBEDDINGTON_QDRANT_STARTUP_RETRIES", "3"))
QDRANT_STARTUP_RETRY_BACKOFF = float(
    os.environ.get("EMBEDDINGTON_QDRANT_STARTUP_RETRY_BACKOFF", "2")
)
# Per-attempt timeout for the probe. Deliberately much shorter than
# HTTP_TIMEOUT (sized for real queries): an unreachable host can burn the FULL
# timeout per attempt before raising, so inheriting 30s here would make the
# worst case 4x30s + backoff. A liveness probe that has not answered in a few
# seconds is not going to.
QDRANT_STARTUP_PROBE_TIMEOUT = float(
    os.environ.get("EMBEDDINGTON_QDRANT_STARTUP_PROBE_TIMEOUT", "5")
)
# Hard ceiling on total time spent retrying, across all attempts. This is the
# number that actually matters: startup must finish well inside the MCP
# client's initialization timeout, or a clear "Refusing to start" message is
# replaced by an opaque client-side disconnect with no diagnostic at all.
# Worst case is roughly this value plus one probe timeout.
QDRANT_STARTUP_DEADLINE = float(os.environ.get("EMBEDDINGTON_QDRANT_STARTUP_DEADLINE", "15"))

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

# --- Response budgeting -----------------------------------------------------
# Response ceiling for enrich, in ESTIMATED tokens (chars/3, pessimistic).
# Server-side config on purpose: the token cap is the CLIENT's property and
# LLM callers must not be able to opt out of the guard (spec §4.1).
MAX_RESPONSE_TOKENS = int(os.environ.get("EMBEDDINGTON_MAX_RESPONSE_TOKENS", "12000"))

# Fraction of each concept's edge slots reserved for the predicate-diversity
# quota during relevance-injected selection (spec §5 PR 3). enrich.py stays
# config-free (like max_response_tokens) — this is wired at the server.py
# call site, not imported by enrich.py itself.
DIVERSITY_QUOTA_FRACTION = float(os.environ.get("EMBEDDINGTON_DIVERSITY_QUOTA", "0.40"))

# Minimum dense-lane similarity score a vector chunk must clear to survive
# (spec §5 PR 4, issue #38). 0.0 disables the filter. 0.50 is the measured
# shipped default (Task 6 battery sweep, live Qdrant): nonsense probes top
# out ~0.45, the weakest legitimate battery query bottoms at ~0.56 — 0.50
# splits with margin on both sides (live-verified: nonsense returns 0 chunks
# at 0.50 vs 5 padded-in at 0.0; all legitimate queries unaffected). Like the
# knobs above, this is wired at the server.py call site; enrich.py stays
# config-free.
SCORE_THRESHOLD = float(os.environ.get("EMBEDDINGTON_SCORE_THRESHOLD", "0.50"))
