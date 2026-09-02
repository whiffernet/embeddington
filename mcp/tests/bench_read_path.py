"""Before/after timing for the read-path Track 1 fixes (spec §6).

Runs a fixed set of calls against a live knowledge graph and prints a
Markdown table for the PR body. Run it on ``main`` and on the branch; paste
both tables. Credentials come from the environment only and are never
printed. Not collected by pytest (no ``test_`` prefix).

Env (first form preferred; the second is what ``.env`` already holds, so
``set -a; . /path/to/.env; set +a`` is enough):
    ARANGO_TEST_URL / ARANGO_TEST_DATABASE / ARANGO_TEST_USER / ARANGO_TEST_PASSWORD
    ARANGO_URL      / ARANGO_DATABASE      / ARANGO_USER      / ARANGO_PASSWORD

Usage (repo root, so the flat modules import):
    python -m tests.bench_read_path --reps 5 [--only find-entities]
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from typing import Any, Callable

from arango_client import ArangoKGClient

FIND_NEEDLES = [
    "incident",
    "Discovery",
    "sys_user",
    "com.snc.itsm.roles.request_management",
    "Now Assist",
    "cmdb_ci",
]


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(f"ARANGO_TEST_{name}") or os.environ.get(f"ARANGO_{name}") or default


def _client() -> ArangoKGClient:
    pw = _env("PASSWORD")
    if not pw:
        sys.exit("set ARANGO_TEST_PASSWORD (or ARANGO_PASSWORD) in the environment — never on argv")
    return ArangoKGClient(
        url=_env("URL", "http://localhost:8529") or "",
        database=_env("DATABASE", "knowledge_graph") or "",
        username=_env("USER", "kg_servicenow_ro") or "",
        password=pw,
    )


def _timed(fn: Callable[[], Any], reps: int) -> tuple[float, Any]:
    """Median wall-clock ms over ``reps`` calls, plus the last result."""
    times: list[float] = []
    result = None
    for _ in range(reps):
        t0 = time.perf_counter()
        result = fn()
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times), result


def bench_find_entities(client: ArangoKGClient, reps: int) -> None:
    """Scan query vs view query, same needle, same process — both paths exist after Task 2."""
    print("\n### kg_find_entities (median ms, server scan stats)\n")
    print(
        "| needle | scan ms | scan scannedFull | view ms | view scannedFull "
        "| view scannedIndex | same top-1 |"
    )
    print("|---|---|---|---|---|---|---|")
    view_ok = client._view_available()
    for needle in FIND_NEEDLES:
        rows: dict[bool, tuple[float, dict, list]] = {}
        for use_view in (False, True):
            if use_view and not view_ok:
                break
            q = client._find_entities_query(use_view)
            bv = client._find_entities_bind_vars(needle, 10, use_view)

            def run() -> tuple[dict, list]:
                cur = client._db.aql.execute(q, bind_vars=bv)
                out = list(cur)
                return cur.statistics(), out

            ms, (stats, out) = _timed(run, reps)
            rows[use_view] = (ms, stats, out)
        s_ms, s_st, s_rows = rows[False]
        if True in rows:
            v_ms, v_st, v_rows = rows[True]
            same = bool(s_rows and v_rows and s_rows[0]["id"] == v_rows[0]["id"])
            print(
                f"| {needle} | {s_ms:.0f} | {s_st.get('scanned_full')} | {v_ms:.0f} | "
                f"{v_st.get('scanned_full')} | {v_st.get('scanned_index')} | {same} |"
            )
        else:
            print(f"| {needle} | {s_ms:.0f} | {s_st.get('scanned_full')} | (no view) | | | |")


# Name pairs resolved through find_entities at run time (entity keys are deterministic
# type__name, so the same names resolve identically on prod and on the battery stack).
# The last two are fixed to make each suppression path *visible* on real topology rather
# than only in the unit tests (round A: release-mediated bad 5/5, hub-mediated bad ~86%):
#   - ("Process Selection", "Upgrade to next family release") is a real 2-hop path whose
#     only route runs through the Zurich Release node (both names resolve top-1 to the
#     Feature entity, so this is a plain name pair).
#   - The hub-mediated pair is pinned by _id rather than by name: find_entities("HR Service
#     Delivery") ranks the Product entity ahead of the Module (higher view score), but
#     module__hr_service_delivery -> module__discovery is the real role__admin-mediated
#     route on prod. An "entities_v2/..." string is used as-is instead of being resolved.
PATH_PAIRS = [
    ("Incident", "ITIL"),
    ("Discovery", "CMDB"),
    ("Now Assist", "Virtual Agent"),
    ("Change Management", "Change Request"),
    ("Service Catalog", "Request Management"),
    ("Process Selection", "Upgrade to next family release"),
    ("entities_v2/module__hr_service_delivery", "entities_v2/module__discovery"),
]


def _resolve(client: ArangoKGClient, label: str) -> dict[str, Any] | None:
    """Resolve a `PATH_PAIRS` label to `{id, ...}`.

    A raw entity ``_id`` (``entities_v2/...``) passes through unchanged, letting a
    bench pair pin an exact entity when name resolution would rank a different type
    first. Anything else resolves via `find_entities`'s top-1 match.

    Args:
        client: Client to resolve against.
        label: Either an ``entities_v2/...`` id or free-text to search.

    Returns:
        ``{"id": ...}`` (an id label) or a full `find_entities` result dict, or
        `None` if a searched label had no match.
    """
    if label.startswith("entities_v2/"):
        return {"id": label}
    found = client.find_entities(label, limit=1)
    return found[0] if found else None


def bench_path(client: ArangoKGClient, reps: int) -> None:
    """kg_path on fixed name pairs: outcome + median ms. Before = SHORTEST_PATH, after = K_SHORTEST_PATHS."""
    print("\n### kg_path (median ms, outcome)\n")
    print("| from | to | outcome | hops | hubs named | ms |")
    print("|---|---|---|---|---|---|")
    for a, b in PATH_PAIRS:
        fa = _resolve(client, a)
        fb = _resolve(client, b)
        if not fa or not fb:
            print(f"| {a} | {b} | unresolved | | | |")
            continue
        ms, res = _timed(lambda: client.shortest_path(fa["id"], fb["id"], max_hops=4), reps)
        if res is None:
            outcome, hops, hubs = "no_path", "", ""
        elif res.get("abstained"):
            outcome, hops, hubs = (
                "abstained",
                "",
                ",".join(h["id"].split("/")[-1] for h in res["hubs"]),
            )
        else:
            inner = [n["id"].split("/")[-1] for n in res["nodes"][1:-1]]
            outcome, hops, hubs = "path", str(len(res["edges"])), " > ".join(inner)
        print(f"| {fa['id']} | {fb['id']} | {outcome} | {hops} | {hubs} | {ms:.0f} |")


def bench_neighbors(client: ArangoKGClient, reps: int) -> None:
    """kg_neighbors on the biggest hub and on a mid-size entity: ms + predicate spread in the cap."""
    print("\n### kg_neighbors depth=1 limit=100 (median ms, distinct predicates in the top band)\n")
    print(
        "| entity | degree | ms | distinct predicates returned | distinct predicates in top confidence band |"
    )
    print("|---|---|---|---|---|")
    for needle in ("admin", "incident", "Discovery"):
        found = client.find_entities(needle, limit=1)
        if not found:
            continue
        ms, res = _timed(lambda: client.neighbors(found[0]["id"], depth=1, limit=100), reps)
        edges = res["edges"]
        band = [e for e in edges if edges and e["confidence"] == edges[0]["confidence"]]
        print(
            f"| {found[0]['id']} | {found[0]['degree']} | {ms:.0f} | "
            f"{len({e['predicate'] for e in edges})} | {len({e['predicate'] for e in band})} |"
        )


ENRICH_QUERIES = [
    (
        "How does Incident Management use the incident table and which roles does it need?",
        ["Incident Management", "incident", "ITIL"],
    ),
    ("What does Discovery write into the CMDB?", ["Discovery", "CMDB", "cmdb_ci"]),
    (
        "Which plugins does Now Assist for ITSM require?",
        ["Now Assist", "ITSM", "Virtual Agent"],
    ),
    (
        "How is Change Management related to Change Request approvals?",
        ["Change Management", "Change Request"],
    ),
    (
        "What tables does Service Catalog request management use?",
        ["Service Catalog", "Request Management", "sc_request"],
    ),
]


def bench_enrich(client: ArangoKGClient, reps: int) -> None:
    """enrich() end to end: ms, KG edges returned, and whether relevance scoring degraded."""
    import asyncio

    import config
    from embedding_client import EmbeddingClient
    from enrich import enrich
    from qdrant_client import QdrantSearchClient

    if not os.environ.get("QDRANT_URL") or not os.environ.get("EMBED_URL"):
        print("\n### enrich — skipped (QDRANT_URL / EMBED_URL unset)\n")
        return
    embed = EmbeddingClient(
        url=os.environ["EMBED_URL"], index=config.DEFAULT_EMBED_INDEX, cache_size=0
    )
    qdrant = QdrantSearchClient(
        url=os.environ["QDRANT_URL"],
        collection="technology",
        api_key=os.environ.get("QDRANT_API_KEY"),
    )
    print("\n### enrich (median ms, degraded-to-confidence warnings)\n")
    print("| query | ms | kg edges | degraded |")
    print("|---|---|---|---|")
    for q, hints in ENRICH_QUERIES:

        def run() -> dict:
            return asyncio.run(
                enrich(
                    query=q,
                    entity_hints=hints,
                    top_k=5,
                    embedding_client=embed,
                    qdrant_client=qdrant,
                    arango_client=client,
                )
            )

        ms, res = _timed(run, reps)
        degraded = any("degraded to confidence order" in w for w in res["warnings"])
        print(f"| {q[:50]} | {ms:.0f} | {res['budget']['returned']} | {degraded} |")
    # Each rep above ran its own asyncio.run(), so the clients' underlying httpx
    # transports are bound to an already-closed event loop by the time we get here —
    # closing them in yet another fresh loop raises "Event loop is closed". The table
    # is already printed; best-effort close, don't let cleanup crash the script.
    for client_ in (embed, qdrant):
        try:
            asyncio.run(client_.close())
        except RuntimeError:
            pass


SECTIONS: dict[str, Callable[[ArangoKGClient, int], None]] = {
    "find-entities": bench_find_entities,
    "path": bench_path,
    "neighbors": bench_neighbors,
    "enrich": bench_enrich,
}


def main() -> None:
    """Parse args, connect from env, run the requested sections."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--only", choices=sorted(SECTIONS), default=None)
    args = ap.parse_args()
    client = _client()
    print(f"database={client._database}  reps={args.reps}")
    for name, fn in SECTIONS.items():
        if args.only in (None, name):
            fn(client, args.reps)


if __name__ == "__main__":
    main()
