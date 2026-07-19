#!/usr/bin/env python3
"""One-off updated_at coverage probe (spec §5 PR 1).

Counts how many entities/edges in the restored battery KG carry a non-null
``updated_at``. Run against the battery stack ONLY (never prod)::

    ARANGO_URL=http://localhost:19412 ARANGO_USER=root \
    ARANGO_PASSWORD=... ARANGO_DATABASE=technology_kg \
    ../../.venv/bin/python coverage_probe.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_MCP = Path(__file__).resolve().parents[2]  # mcp/
sys.path.insert(0, str(_MCP))

import config  # noqa: E402
from arango_client import ArangoKGClient  # noqa: E402

OUT = Path(__file__).resolve().parent / "updated_at_coverage.json"

FORBIDDEN_PORTS = (":6333", ":6334", ":8529")


def main() -> None:
    if any(p in config.ARANGO_URL for p in FORBIDDEN_PORTS):
        raise SystemExit(f"refusing to probe a prod-looking URL: {config.ARANGO_URL}")
    client = ArangoKGClient(
        url=config.ARANGO_URL,
        database=config.ARANGO_DATABASE,
        username=config.ARANGO_USER,
        password=config.ARANGO_PASSWORD,
    )
    counts: dict[str, dict[str, int]] = {}
    for coll in ("entities_v2", "relationships_v2"):
        total = client._db.collection(coll).count()
        stamped = next(
            iter(
                client._db.aql.execute(
                    f"RETURN LENGTH(FOR d IN {coll} FILTER d.updated_at != null RETURN 1)"
                )
            )
        )
        counts[coll] = {"total": int(total), "with_updated_at": int(stamped)}
    payload = {
        "binding": "baseline-2026-07b",
        "note": "updated_at coverage in the restored battery KG (spec §5 PR 1 probe)",
        "counts": counts,
    }
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
