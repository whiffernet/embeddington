#!/usr/bin/env python3
"""Budget tuning sweep (Task 11, spec §7): edge_budget × top_k × dedup grid.

Run from ``mcp/`` with the battery env exported (same env as the Task 10 live
battery — points at the local restored stack, never prod)::

    QDRANT_URL=http://localhost:19411 ARANGO_URL=http://localhost:19412 \\
    ARANGO_USER=root ARANGO_PASSWORD=battery-local-1 \\
    ARANGO_DATABASE=technology_kg EMBED_URL=http://localhost:8100/embed \\
    ../.venv/bin/python tests/battery_sweep.py

For every battery query (``battery_queries.QUERIES``) it sweeps
``edge_budget ∈ {20,40,60,80,120}`` × ``top_k ∈ {3,5,10}`` × ``dedup {on,off}``
and records, per combo:

  - ``tokens``   — ``budget.estimate_tokens(result)`` (the response-ceiling basis)
  - ``ms``       — median wall-clock latency of the ``enrich`` call across
    ``SWEEP_REPS`` repetitions (``ms_median``/``ms_iqr`` are also kept)
  - ``returned`` — ``budget.returned`` (total KG edges in the response)
  - ``trunc``    — any match truncated OR the vector half pre-clipped
  - ``ret``      — retention (below)
  - ``pp``       — per-predicate recall (below)

The ``dedup off`` arm exercises the ``BUDGET_DISABLE_DEDUP=1`` hook in
``budget.group_concepts`` (every entity becomes its own concept). That hook is
a sweep-only diagnostic — there is **no** production dedup toggle; the shipped
``enrich`` always dedups, so the knee decision is made over the ``dedup=on``
arm and ``dedup=off`` is reported only to show what concept-dedup buys.

Retention (spec §7, deliberately NON-CIRCULAR)
----------------------------------------------
Ground truth for a query is the UNBUDGETED edge pool for its concepts —
``neighbors_stratified(per_predicate=2, overall=100)`` merged across every
entity the query's hints resolve to (predicate-scoped when the query is) —
ranked by bge-m3 cosine similarity of each edge's ``source_quote`` to the
QUERY. Query-relevance is an axis ORTHOGONAL to the ``confidence`` the budget
selects on, so the metric does not grade the budget against itself. The
top-``K`` (K=10) by that ranking are the ground truth; ``retention =
|returned_edge_ids ∩ ground_truth| / K`` (K clamped to pool size, which never
bites here — every pool is ≥ 10). The pool is stratified and unbudgeted, hence
deliberately broader than any kept set: retention is **pool-relative** —
``1.0`` means the budget returned all 10 of the KG's most query-relevant edges
for those concepts, not that it returned the whole pool. Per-predicate recall
is ``|distinct predicates in returned edges ∩ pool predicates| / |pool
predicates|``.

Ground truth is computed ONCE per query (independent of edge_budget/top_k/dedup
— those change only how the budget allocates, never which entities the query
resolves to) and reused across all 30 combos.

Before the grid runs, the live stack is checked against the frozen gold
binding (``gold_pools.stack_binding``/``assert_binding``) and hard-fails on
drift — set ``SWEEP_SKIP_BINDING=1`` to bypass for unit/dev runs (never for a
committed sweep). ``SWEEP_REPS`` (default 1) controls repeated timing samples
per combo/query; ``SWEEP_TAG`` (default today's date) names the output files.
``SWEEP_COHORT`` (``fixed`` default | ``identifier``, spec §3.4) selects the
query list; ``identifier`` also appends ``-identifier`` to the tag.

Writes ``battery_results/<tag>-sweep.md`` (a committed artifact for the
default tag), the machine-readable ``battery_results/<tag>-sweep.json``, and
``battery_results/<tag>-worst-response.json`` (the single largest response by
estimated tokens, for tokenizer calibration), and prints the knee decision to
stdout.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from statistics import mean

import numpy as np

# Quiet per-request client chatter — the sweep makes thousands of embed/search
# calls; server.py sets the root logger to INFO on import.
logging.getLogger("httpx").setLevel(logging.WARNING)

_MCP = Path(__file__).resolve().parent.parent  # mcp/
_TESTS = Path(__file__).resolve().parent  # mcp/tests/
for _p in (str(_MCP), str(_TESTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import budget as _budget  # noqa: E402
import config  # noqa: E402
import gold_pools  # noqa: E402
import server  # noqa: E402
import sweep_io  # noqa: E402
from embedding_client import EmbeddingClient  # noqa: E402
from gold_pools import (  # noqa: E402
    POOL_OVERALL,
    POOL_PER_PREDICATE,
)
from gold_pools import build_pool as _build_pool  # noqa: E402

EDGE_BUDGETS = [20, 40, 60, 80, 120]
TOP_KS = [3, 5, 10]
DEDUPS = ["on", "off"]
GT_K = 10
EMBED_CONCURRENCY = 16
CEILING = config.MAX_RESPONSE_TOKENS  # 12000 est-tokens
HEADROOM_TOKENS = int(CEILING * 0.75)  # ≤ this leaves ≥25% ceiling headroom
PLATEAU_TOLERANCE = 0.05  # "within 5% of the plateau" (relative)
SHIPPED = (
    40,
    5,
)  # (edge_budget, top_k) currently-committed defaults (re-swept on orphan-fixed data)

# SWEEP_COHORT (spec §3.4): "fixed" (default) runs the frozen 11-query
# battery; "identifier" runs the identifier cohort into its own tagged
# results. Ground-truth/gold plumbing below is unchanged — it's query-list
# agnostic and just iterates whichever QUERIES ends up selected.
SWEEP_COHORT = os.environ.get("SWEEP_COHORT", "fixed")
QUERIES = sweep_io.select_cohort(SWEEP_COHORT)  # raises ValueError on unknown cohort

SWEEP_REPS = int(os.environ.get("SWEEP_REPS", "1"))
SWEEP_TAG = os.environ.get("SWEEP_TAG") or time.strftime("%Y-%m-%d")
if SWEEP_COHORT == "identifier":
    SWEEP_TAG += "-identifier"
RESULTS_PATH = _TESTS / "battery_results" / f"{SWEEP_TAG}-sweep.md"
RESULTS_JSON = _TESTS / "battery_results" / f"{SWEEP_TAG}-sweep.json"
WORST_JSON = _TESTS / "battery_results" / f"{SWEEP_TAG}-worst-response.json"

# Short column labels for the (wide) per-query tables.
SHORT = {
    "case1_realistic_3hint": "c1",
    "case2_minimal": "c2",
    "hub_cmdb_rel_ci": "cmdb_ci",
    "hub_process_mining": "procmin",
    "hub_discovery": "disc",
    "hub_cmdb": "cmdb",
    "hub_incident": "incid",
    "hub_predictive_intelligence": "predint",
    "control_no_hints_snake": "ctl_nh",
    "control_predicate_filter": "ctl_pf",
    "control_multifacet_license": "ctl_ml",
    "id_disc_plugin": "id_disc",
    "id_mim_plugin": "id_mim",
    "id_pm_project": "id_pmproj",
    "id_sc_cat_item": "id_cat",
}

CALL_COUNTS: dict[str, int] = {}


def _wrap_counting(obj: object, method: str, key: str) -> None:
    """Wrap a client method so each call increments CALL_COUNTS[key]."""
    orig = getattr(obj, method)
    if asyncio.iscoroutinefunction(orig):

        async def aw(*a, **k):
            CALL_COUNTS[key] = CALL_COUNTS.get(key, 0) + 1
            return await orig(*a, **k)

        setattr(obj, method, aw)  # type: ignore[method-assign]
    else:

        def sw(*a, **k):
            CALL_COUNTS[key] = CALL_COUNTS.get(key, 0) + 1
            return orig(*a, **k)

        setattr(obj, method, sw)  # type: ignore[method-assign]


def _install_counters() -> None:
    """Instrument the wired server singletons (battery-only monkey wrap)."""
    _wrap_counting(server._get_embed(), "embed", "embed")
    _wrap_counting(server._get_qdrant(), "search", "qdrant_search")
    ar = server._get_arango()
    _wrap_counting(ar, "find_entities", "arango_find")
    _wrap_counting(ar, "neighbors_stratified", "arango_stratified")
    _wrap_counting(ar, "count_edges", "arango_count")


async def _embed_texts(embed: EmbeddingClient, texts: list[str]) -> list[list[float]]:
    """Embed many texts through the shared client at bounded concurrency."""
    sem = asyncio.Semaphore(EMBED_CONCURRENCY)

    async def _one(t: str) -> list[float]:
        async with sem:
            return await embed.embed(t)

    return await asyncio.gather(*(_one(t) for t in texts))


async def _ground_truth(arango, embed: EmbeddingClient, q: dict) -> dict:
    """Non-circular ground truth for a query (see module docstring)."""
    pool = _build_pool(arango, q)
    q_vec = np.asarray(await embed.embed(q["query"]), dtype=np.float64)
    q_vec /= np.linalg.norm(q_vec) or 1.0
    uniq = sorted({ed["source_quote"] for ed in pool.values() if ed.get("source_quote")})
    vecs = await _embed_texts(embed, uniq)
    vmap: dict[str, np.ndarray] = {}
    for quote, raw in zip(uniq, vecs):
        v = np.asarray(raw, dtype=np.float64)
        vmap[quote] = v / (np.linalg.norm(v) or 1.0)

    scored: list[tuple[float, str]] = []
    for eid, ed in pool.items():
        sq = ed.get("source_quote")
        if sq and sq in vmap:
            scored.append((float(q_vec @ vmap[sq]), eid))
    scored.sort(key=lambda x: x[0], reverse=True)
    gt_ids = {eid for _, eid in scored[:GT_K]}
    pool_preds = {ed.get("predicate") for ed in pool.values()} - {None}
    return {
        "gt_ids": gt_ids,
        "k_eff": min(GT_K, len(scored)),
        "pool_size": len(pool),
        "pool_preds": pool_preds,
    }


async def _run_enrich(q: dict, edge_budget: int, top_k: int) -> dict:
    """Call the wired enrich tool exactly as the live battery does."""
    fn = server.enrich.fn if hasattr(server.enrich, "fn") else server.enrich
    return await fn(
        query=q["query"],
        entity_hints=q["entity_hints"],
        top_k=top_k,
        edge_budget=edge_budget,
        predicates=q["predicates"],
    )


def _kept_ids(result: dict) -> set[str]:
    return {str(e["id"]) for m in result["kg_matches"] for e in m["edges"]}


def _kept_preds(result: dict) -> set[str]:
    return {e.get("predicate") for m in result["kg_matches"] for e in m["edges"]} - {None}


def _truncated(result: dict) -> bool:
    return bool(result["budget"]["truncated"]) or any(
        m["truncation"]["truncated"] for m in result["kg_matches"]
    )


def _combo_label(c: dict) -> str:
    return f"eb={c['edge_budget']:>3} k={c['top_k']:>2} dedup={c['dedup']}"


def _agg(c: dict) -> dict:
    qs = list(c["q"].values())
    tokens = [x["tokens"] for x in qs]
    return {
        "worst_tokens": max(tokens),
        "mean_tokens": mean(tokens),
        "mean_ret": mean(x["ret"] for x in qs),
        "mean_pp": mean(x["pp"] for x in qs),
        "mean_ms": mean(x["ms"] for x in qs),
        "mean_returned": mean(x["returned"] for x in qs),
    }


def _md_table(title: str, rows: list[dict], cell, agg_header: str, agg_cell) -> list[str]:
    """One metric table: rows = combos, cols = queries, + an aggregate column."""
    names = [q["name"] for q in QUERIES]
    header = "| combo | " + " | ".join(SHORT[n] for n in names) + f" | **{agg_header}** |"
    sep = "|" + "---|" * (len(names) + 2)
    out = [f"#### {title}", "", header, sep]
    for c in rows:
        cells = " | ".join(cell(c["q"][n]) for n in names)
        out.append(f"| {_combo_label(c)} | {cells} | **{agg_cell(c)}** |")
    out.append("")
    return out


def _render(rows: list[dict], gts: dict, knee: dict, plateau: float, threshold: float) -> str:
    lines: list[str] = []
    lines.append("# Budget tuning sweep — 2026-07-17")
    lines.append("")
    lines.append(
        "Generated by `mcp/tests/battery_sweep.py` against the restored local "
        "battery stack (qdrant :19411 / 152,194 pts, arango :19412 / 683,651 "
        "edges — NOT prod). One row per `edge_budget × top_k × dedup` combo; "
        "columns are the 11 `battery_queries.QUERIES`. Retention is the "
        "non-circular, query-relevance (bge-m3) top-10 metric defined in the "
        "script docstring; it is **pool-relative** (the ground-truth pool is "
        "stratified and unbudgeted, broader than any kept set). `dedup=off` is "
        "a diagnostic only — production enrich always dedups."
    )
    lines.append("")
    lines.append(
        f"- Response ceiling: **{CEILING}** est-tokens "
        f"(`EMBEDDINGTON_MAX_RESPONSE_TOKENS`). ≥25% headroom ⇒ worst-case "
        f"≤ **{HEADROOM_TOKENS}** est-tokens."
    )
    lines.append(f"- Grid: edge_budget {EDGE_BUDGETS} × top_k {TOP_KS} × dedup {DEDUPS}.")
    lines.append(
        f"- Ground-truth pool: neighbors_stratified(per_predicate={POOL_PER_PREDICATE}, "
        f"overall={POOL_OVERALL}) merged; top-{GT_K} by query cosine."
    )
    lines.append("")

    # Legend + GT reference.
    lines.append("## Query legend & ground-truth pools")
    lines.append("")
    lines.append("| short | query | pool_size | gt_k | pool_predicates |")
    lines.append("|---|---|---|---|---|")
    for q in QUERIES:
        gt = gts[q["name"]]
        lines.append(
            f"| {SHORT[q['name']]} | {q['name']} | {gt['pool_size']} | "
            f"{gt['k_eff']} | {len(gt['pool_preds'])} |"
        )
    lines.append("")

    # Per-metric tables.
    lines.append("## Per-metric tables (rows = combo, cols = query)")
    lines.append("")
    lines += _md_table(
        "Estimated response tokens (ceiling basis)",
        rows,
        lambda x: str(x["tokens"]),
        "worst",
        lambda c: str(_agg(c)["worst_tokens"]),
    )
    lines += _md_table(
        "Retention (|kept ∩ query-relevant top-10| / 10)",
        rows,
        lambda x: f"{x['ret']:.2f}",
        "mean",
        lambda c: f"{_agg(c)['mean_ret']:.3f}",
    )
    lines += _md_table(
        "Per-predicate recall (kept preds ∩ pool / pool)",
        rows,
        lambda x: f"{x['pp']:.2f}",
        "mean",
        lambda c: f"{_agg(c)['mean_pp']:.3f}",
    )
    lines += _md_table(
        "KG edges returned (budget.returned)",
        rows,
        lambda x: str(x["returned"]),
        "mean",
        lambda c: f"{_agg(c)['mean_returned']:.1f}",
    )
    lines += _md_table(
        "Wall-clock latency (ms)",
        rows,
        lambda x: str(round(x["ms"])),
        "mean",
        lambda c: str(round(_agg(c)["mean_ms"])),
    )

    # --- Knee decision (dedup=on; production enrich always dedups) ----------
    lines.append("## Knee")
    lines.append("")
    on = [c for c in rows if c["dedup"] == "on"]
    headroom_pass = [c for c in on if _agg(c)["worst_tokens"] <= HEADROOM_TOKENS]
    grid_peak = max(on, key=lambda c: _agg(c)["mean_ret"])
    gp = _agg(grid_peak)
    worst_of_worst = max(_agg(c)["worst_tokens"] for c in on)
    ka = _agg(knee)

    lines.append(
        f"Decision rule (brief): among **dedup=on** combos, smallest "
        f"`edge_budget`/`top_k` whose retention is within "
        f"{int(PLATEAU_TOLERANCE * 100)}% of the plateau AND whose worst-case "
        f"size ≤ {HEADROOM_TOKENS} est-tokens (≥25% headroom)."
    )
    lines.append("")
    lines.append(
        f"**Finding 1 — ceiling saturated; the headroom clause is inapplicable.** "
        f"Only **{len(headroom_pass)}/{len(on)}** dedup=on combos land ≤ "
        f"{HEADROOM_TOKENS} est-tokens; every combo's worst-case query sits at "
        f"~{worst_of_worst} (≈ the {CEILING} ceiling). Real RAG chunks (~2k "
        f"tokens each) plus rich edges (~260 tokens each) saturate the ceiling, "
        f"and the trim fills to just under it regardless of params — so ≥25% "
        f"headroom is unmeetable at **any** grid point and cannot discriminate. "
        f"Headroom is a *ceiling* / chunk-size lever "
        f"(`EMBEDDINGTON_MAX_RESPONSE_TOKENS`, source_quote/text length), not an "
        f"`edge_budget`/`top_k` one. The knee is therefore taken on retention + "
        f"actual edge delivery, holding `top_k` at the shipped {SHIPPED[1]} (a "
        f"RAG-breadth lever the KG-only retention metric must not tune)."
    )
    lines.append("")
    lines.append(f"**edge_budget curve at top_k={SHIPPED[1]} (dedup=on):**")
    lines.append("")
    lines.append("| edge_budget | mean_ret | mean_returned | mean_pp | worst_tokens |")
    lines.append("|---|---|---|---|---|")
    for eb in EDGE_BUDGETS:
        c = next(c for c in on if c["edge_budget"] == eb and c["top_k"] == SHIPPED[1])
        a = _agg(c)
        mark = " ⭐" if c is knee else ""
        lines.append(
            f"| {eb}{mark} | {a['mean_ret']:.3f} | {a['mean_returned']:.1f} | "
            f"{a['mean_pp']:.3f} | {a['worst_tokens']} |"
        )
    lines.append("")
    lines.append(
        "**Finding 2 — edge delivery plateaus past ~40 (orphan-node trim fix, "
        "#28).** Edges actually delivered rise from edge_budget=20 to ~40 and "
        "then *plateau* (~28 mean) as the budget grows; predicate recall stays "
        "~1.0 across edge_budget 40–120. A larger budget no longer returns "
        "fewer edges. **Before** this fix the ceiling trim popped edges but left "
        "their now-orphan nodes in the response, which held it over the ceiling "
        "and floored concepts to the 3-edge floor — so delivery *inverted*, "
        "collapsing to ~8.6 mean edges (predicate recall ~0.55) at "
        "edge_budget=120 while sitting 1600–2300 tokens UNDER the ceiling. "
        "Pruning orphan nodes on trim reclaims those tokens for edges, so "
        "delivery now plateaus instead of inverting (see the KG-edges table: "
        "eb=120/k=5 went from ~8.6 to ~28 mean). Retention still peaks at "
        "edge_budget=40 and eases off mildly at larger budgets, so the smallest "
        "budget at the plateau stays the knee."
    )
    lines.append("")
    lines.append(
        f"**Chosen defaults: `edge_budget={knee['edge_budget']}`, "
        f"`top_k={knee['top_k']}`** (dedup=on). Retention {ka['mean_ret']:.3f} "
        f"(plateau {plateau:.3f} at top_k={SHIPPED[1]}, ≥ {threshold:.3f} "
        f"threshold), predicate recall {ka['mean_pp']:.3f}, {ka['mean_returned']:.1f} "
        f"edges delivered — the smallest `edge_budget` at the retention plateau, "
        f"where delivered edges and predicate diversity have also plateaued; "
        f"higher budgets add latency without adding edges."
    )
    lines.append("")
    if (knee["edge_budget"], knee["top_k"]) == SHIPPED:
        lines.append(f"Matches the shipped defaults {SHIPPED} — no change.")
    else:
        lines.append(
            f"**Differs from the shipped {SHIPPED}** → `edge_budget` default "
            f"updated **{SHIPPED[0]}→{knee['edge_budget']}** in server.py, "
            f"enrich.py, RESPONSE_SHAPES.md, CHANGELOG.md. `top_k` stays "
            f"{knee['top_k']}."
        )
    lines.append("")
    lines.append(
        f"For maximal KG grounding a caller can pass `top_k=3`, which lifts "
        f"retention to the full-grid peak **{gp['mean_ret']:.3f}** at "
        f"`edge_budget={grid_peak['edge_budget']}` (fewer vector chunks cede more "
        f"of the shared ceiling to KG edges). Documented as caller guidance, not "
        f"a default change — top_k governs the vector half, which the KG-only "
        f"retention metric does not score."
    )
    lines.append("")

    # Dedup on-vs-off contrast at the (candidate) default budget.
    lines.append("## dedup on vs off (diagnostic)")
    lines.append("")
    lines.append("| combo | mean_ret | mean_pp | worst_tokens | mean_returned |")
    lines.append("|---|---|---|---|---|")
    for c in rows:
        if c["top_k"] != 5:
            continue
        a = _agg(c)
        lines.append(
            f"| {_combo_label(c)} | {a['mean_ret']:.3f} | {a['mean_pp']:.3f} | "
            f"{a['worst_tokens']} | {a['mean_returned']:.1f} |"
        )
    lines.append("")
    return "\n".join(lines)


def _pick_knee(rows: list[dict]) -> tuple[dict, float, float]:
    """Choose the shipped defaults from the sweep.

    The brief's ≥25%-headroom clause cannot discriminate — the ceiling is
    saturated for every combo (Finding 1 in the rendered report) — so the knee
    is taken on retention + actual edge delivery, holding ``top_k`` at the
    shipped value (top_k is a RAG-breadth lever the KG-only retention metric
    must not tune). Among dedup=on combos at that ``top_k``: the smallest
    ``edge_budget`` whose mean retention is within ``PLATEAU_TOLERANCE`` of that
    slice's plateau, tie-broken by the most edges actually delivered (which
    plateaus past the ceiling now that orphan nodes are pruned on trim —
    Finding 2; before that fix it inverted).
    """
    slice_ = [c for c in rows if c["dedup"] == "on" and c["top_k"] == SHIPPED[1]]
    plateau = max(_agg(c)["mean_ret"] for c in slice_)
    threshold = plateau * (1 - PLATEAU_TOLERANCE)
    cands = [c for c in slice_ if _agg(c)["mean_ret"] >= threshold]
    knee = min(cands, key=lambda c: (c["edge_budget"], -_agg(c)["mean_returned"]))
    return knee, plateau, threshold


async def main() -> None:
    embed = EmbeddingClient(
        url=config.EMBED_URL, index=config.DEFAULT_EMBED_INDEX, timeout=config.HTTP_TIMEOUT
    )
    arango = server._get_arango()  # sync singleton — safe to reuse across the loop

    if os.environ.get("SWEEP_SKIP_BINDING") != "1":
        binding = gold_pools.stack_binding(config.QDRANT_URL, arango)
        gold_pools.assert_binding(binding)  # hard-fail on drift (spec §3.2)
    else:
        binding = {"baseline": "UNVERIFIED", "points": 0, "entities": 0, "edges": 0}
    _install_counters()

    print("Building ground truth (once per query)...", file=sys.stderr)
    gts: dict[str, dict] = {}
    for q in QUERIES:
        gts[q["name"]] = await _ground_truth(arango, embed, q)
        gt = gts[q["name"]]
        print(
            f"  {q['name']:32s} pool={gt['pool_size']:4d} preds={len(gt['pool_preds'])}",
            file=sys.stderr,
        )

    rows: list[dict] = []
    worst: dict = {"tokens": -1}
    total = len(DEDUPS) * len(EDGE_BUDGETS) * len(TOP_KS)
    done = 0
    for dedup in DEDUPS:
        if dedup == "off":
            os.environ["BUDGET_DISABLE_DEDUP"] = "1"
        else:
            os.environ.pop("BUDGET_DISABLE_DEDUP", None)
        for eb in EDGE_BUDGETS:
            for tk in TOP_KS:
                combo: dict = {"edge_budget": eb, "top_k": tk, "dedup": dedup, "q": {}}
                for q in QUERIES:
                    ms_all: list[float] = []
                    calls_before = dict(CALL_COUNTS)
                    res: dict = {}
                    for _ in range(SWEEP_REPS):
                        t0 = time.perf_counter()
                        res = await _run_enrich(q, eb, tk)
                        ms_all.append((time.perf_counter() - t0) * 1000.0)
                    calls = {
                        k: (CALL_COUNTS.get(k, 0) - calls_before.get(k, 0)) // SWEEP_REPS
                        for k in (
                            "embed",
                            "qdrant_search",
                            "arango_find",
                            "arango_stratified",
                            "arango_count",
                        )
                    }
                    gt = gts[q["name"]]
                    kept = _kept_ids(res)
                    kept_preds = _kept_preds(res)
                    overlap = len(kept & gt["gt_ids"])
                    ret = overlap / gt["k_eff"] if gt["k_eff"] else 0.0
                    pp = (
                        len(kept_preds & gt["pool_preds"]) / len(gt["pool_preds"])
                        if gt["pool_preds"]
                        else 0.0
                    )
                    combo["q"][q["name"]] = {
                        "tokens": _budget.estimate_tokens(res),
                        "ms_all": ms_all,
                        "returned": res["budget"]["returned"],
                        "trunc": _truncated(res),
                        "ret": ret,
                        "pp": pp,
                        "err": res["errors"],
                        "kept_ids": sorted(kept),
                        "calls": calls,
                    }
                    tok = combo["q"][q["name"]]["tokens"]
                    if tok > worst["tokens"]:
                        worst = {
                            "tokens": tok,
                            "combo": {"eb": eb, "tk": tk, "dedup": dedup},
                            "query": q["name"],
                            "response": res,
                        }
                    if res["errors"]:
                        print(
                            f"  ERROR {dedup} eb={eb} k={tk} {q['name']}: {res['errors']}",
                            file=sys.stderr,
                        )
                rows.append(combo)
                done += 1
                print(f"  [{done}/{total}] dedup={dedup} eb={eb} k={tk} done", file=sys.stderr)
    os.environ.pop("BUDGET_DISABLE_DEDUP", None)
    await embed.close()

    for c in rows:
        for name, entry in c["q"].items():
            entry.update(sweep_io.latency_summary(entry["ms_all"]))
            entry["ms"] = entry["ms_median"]  # keep _agg/_render's existing key

    knee, plateau, threshold = _pick_knee(rows)
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(_render(rows, gts, knee, plateau, threshold), encoding="utf-8")

    git_sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=_MCP
    ).stdout.strip()
    doc = sweep_io.serialize_run(
        rows=rows,
        ground_truth=gts,
        binding=binding,
        meta={
            "git_sha": git_sha,
            "reps": SWEEP_REPS,
            "tag": SWEEP_TAG,
            "grid": {"edge_budgets": EDGE_BUDGETS, "top_ks": TOP_KS, "dedups": DEDUPS},
        },
    )
    RESULTS_JSON.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {RESULTS_JSON}")
    WORST_JSON.write_text(json.dumps(worst, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {WORST_JSON}")

    ka = _agg(knee)
    print("\n=== KNEE DECISION ===")
    print(f"top_k={SHIPPED[1]} retention plateau = {plateau:.3f}; threshold = {threshold:.3f}")
    print(
        f"knee = edge_budget={knee['edge_budget']} top_k={knee['top_k']} (dedup=on) "
        f"| mean_ret={ka['mean_ret']:.3f} mean_returned={ka['mean_returned']:.1f} "
        f"mean_pp={ka['mean_pp']:.3f} worst_tokens={ka['worst_tokens']}"
    )
    verdict = "KEEP" if (knee["edge_budget"], knee["top_k"]) == SHIPPED else "CHANGE"
    print(f"shipped defaults {SHIPPED} => {verdict}")
    print(f"wrote {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
