#!/usr/bin/env bash
# Re-sync the vendored MCP server under mcp/ from an upstream claudegraph checkout.
#
# Copies an EXPLICIT file list — never the whole tree: mcp/ also carries the measurement
# apparatus (tests/ontology_*, gold_*, battery_*), README.md, RESPONSE_SHAPES.md,
# pytest.ini and requirements-dev.txt that upstream does not have — then applies the
# identifier transform. Idempotent. `--check` copies into a temp dir and diffs instead.
#
# Usage: ./sync_mcp.sh /path/to/claudegraph [--check]
set -euo pipefail

UP="${1:?usage: sync_mcp.sh /path/to/claudegraph [--check]}"
MODE="${2:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HERE/mcp"

SYNCED=(
  arango_client.py budget.py config.py embedding_client.py enrich.py grounding.py hybrid.py
  qdrant_client.py server.py requirements.txt
  tests/conftest.py tests/test_arango_client.py tests/test_budget.py tests/test_client_timeouts.py
  tests/test_config.py tests/test_config_allowlist.py tests/test_embedding_client.py
  tests/test_enrich.py tests/test_grounding.py tests/test_hybrid.py tests/test_integration.py
  tests/test_password_resolution.py tests/test_qdrant_client.py tests/test_registry.py
  tests/test_server_main.py tests/test_tools.py tests/bench_read_path.py
)

transform() {  # upstream identifiers -> embeddington identifiers, in place
  sed -i \
    -e 's/FastMCP("claudeGraph")/FastMCP("embeddington")/' \
    -e 's/getLogger("claudegraph\./getLogger("embeddington./' \
    -e 's/getLogger("mcp\.claudegraph")/getLogger("mcp.embeddington")/' \
    -e 's/\bCLAUDEGRAPH_/EMBEDDINGTON_/g' \
    "$@"
}

if [[ "$MODE" == "--check" ]]; then
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  rc=0
  for f in "${SYNCED[@]}"; do
    install -D -m 0644 "$UP/$f" "$TMP/$f"
    transform "$TMP/$f"
    if ! diff -q "$TMP/$f" "$DEST/$f" >/dev/null; then echo "DRIFT $f"; rc=1; fi
  done
  exit $rc
fi

for f in "${SYNCED[@]}"; do
  install -D -m 0644 "$UP/$f" "$DEST/$f"
  transform "$DEST/$f"
done
echo "synced ${#SYNCED[@]} files from $UP into $DEST"
