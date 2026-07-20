"""Pure serialization/aggregation helpers for battery_sweep (spec §5 PR 1).

Kept free of I/O and stack access so the JSON contract every later PR diffs
against is unit-tested (the sweep script itself needs the live stack).
"""

from __future__ import annotations

import asyncio
from statistics import median
from typing import Any

from battery_queries import IDENTIFIER_QUERIES
from battery_queries import QUERIES as _FIXED_QUERIES

_COHORTS: dict[str, list[dict]] = {"fixed": _FIXED_QUERIES, "identifier": IDENTIFIER_QUERIES}

CALL_COUNTS: dict[str, int] = {}


def wrap_counting(obj: object, method: str, key: str) -> None:
    """Wrap a client method in place so each call increments ``CALL_COUNTS[key]``.

    Lives here (not in ``battery_sweep.py``) so it — and ``CALL_COUNTS`` — can
    be unit-tested against a fake client without importing ``battery_sweep``,
    which pulls in ``numpy`` (a live-battery-only dependency not installed
    for the unit-test job).

    Args:
        obj: The object whose method is wrapped, mutated in place.
        method: Name of the (sync or async) method to wrap.
        key: ``CALL_COUNTS`` key incremented on each call.
    """
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


def select_cohort(name: str) -> list[dict]:
    """Select a battery query cohort by name (spec §3.4).

    Args:
        name: ``"fixed"`` for the frozen 11-query battery
            (``battery_queries.QUERIES``) or ``"identifier"`` for the
            identifier cohort (``battery_queries.IDENTIFIER_QUERIES``).

    Returns:
        The selected query list.

    Raises:
        ValueError: if ``name`` is not a recognized cohort.
    """
    try:
        return _COHORTS[name]
    except KeyError:
        raise ValueError(f"unknown cohort: {name!r}") from None


def latency_summary(ms_all: list[float]) -> dict[str, float]:
    """Median + IQR over repeated wall-clock samples (spec §3.5)."""
    ordered = sorted(ms_all)
    n = len(ordered)
    q1 = ordered[max(0, (n // 4))]
    q3 = ordered[min(n - 1, (3 * n) // 4)]
    return {"ms_median": float(median(ordered)), "ms_iqr": float(q3 - q1)}


def render_title(tag: str) -> str:
    """H1 line for the rendered sweep report, tagged by the run (spec §5, #44 M2).

    Args:
        tag: The run's ``SWEEP_TAG`` (never a hardcoded date — a stale
            committed report should not read as a fresh run).

    Returns:
        The Markdown H1 line.
    """
    return f"# Budget tuning sweep — {tag}"


def render_knee_verdict(knee_eb: int, knee_tk: int, shipped: tuple[int, int]) -> str:
    """Knee-vs-shipped verdict paragraph, honest about what the sweep did (#44 M1).

    The sweep only measures and reports — it never edits server.py, enrich.py,
    or any docs. When the knee differs from the shipped defaults this must
    read as a RECOMMENDATION ("suggests"), not a false action claim ("default
    updated"): defaults stay whatever they were before this run.

    Args:
        knee_eb: The knee's chosen ``edge_budget``.
        knee_tk: The knee's chosen ``top_k``.
        shipped: The currently-committed ``(edge_budget, top_k)`` defaults.

    Returns:
        One Markdown paragraph.
    """
    if (knee_eb, knee_tk) == shipped:
        return f"Matches the shipped defaults {shipped} — no change."
    return (
        f"**Differs from the shipped {shipped}** → this sweep suggests "
        f"`edge_budget={knee_eb}` (top_k stays {knee_tk}); defaults unchanged "
        f"by this run — apply via a config/docs commit (server.py, enrich.py, "
        f"RESPONSE_SHAPES.md, CHANGELOG.md) if accepted."
    )


def render_finding_2(
    curve: list[tuple[int, dict[str, float]]], top_k: int, edge_budgets: list[int]
) -> str:
    """Data-derived 'Finding 2' paragraph: edge delivery + retention by edge_budget.

    Reports what a sweep run actually measured rather than asserting a fixed
    historical peak/plateau value. A prior version of this paragraph hardcoded
    a specific PR 1 (#28) finding ("retention still peaks at edge_budget=40",
    "~28 mean" edges, "predicate recall stays ~1.0") that a later run's
    relevance-aware selection (PR 3) falsified — retention no longer
    necessarily peaks where edge delivery plateaus (#44 final-review finding
    B2). Lives here (not in ``battery_sweep.py``) so it's unit-testable
    without importing ``battery_sweep``, which pulls in ``numpy``.

    Args:
        curve: ``(edge_budget, agg)`` pairs in sweep order, where ``agg`` has
            ``mean_ret``, ``mean_returned``, and ``mean_pp`` keys (as produced
            by the sweep's per-combo aggregation).
        top_k: The top_k value the curve was measured at (for the caption).
        edge_budgets: The edge_budget values swept (for the caption).

    Returns:
        The Markdown paragraph text (no leading/trailing blank lines).
    """
    peak_eb, peak_agg = max(curve, key=lambda item: item[1]["mean_ret"])
    returned_values = [a["mean_returned"] for _, a in curve]
    pp_values = [a["mean_pp"] for _, a in curve]
    return (
        f"**Finding 2 — edge delivery and retention by `edge_budget` "
        f"(top_k={top_k}, dedup=on), this run's own numbers.** Mean edges "
        f"delivered range {min(returned_values):.1f}–{max(returned_values):.1f} "
        f"and mean predicate recall ranges {min(pp_values):.3f}–{max(pp_values):.3f} "
        f"across edge_budget {edge_budgets}; mean retention peaks at "
        f"edge_budget={peak_eb} ({peak_agg['mean_ret']:.3f}). See the curve "
        f"table above for the full per-`edge_budget` breakdown, including "
        f"whether delivery/retention plateau, keep rising, or fall past a peak."
    )


def serialize_run(
    rows: list[dict],
    ground_truth: dict[str, dict],
    binding: dict[str, Any],
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Build the JSON-serializable run document (sets become sorted lists)."""
    out_rows = []
    for c in rows:
        row = {"edge_budget": c["edge_budget"], "top_k": c["top_k"], "dedup": c["dedup"], "q": {}}
        for name, q in c["q"].items():
            entry = dict(q)
            ms_all = entry.pop("ms_all", None)
            if ms_all:
                entry.update(latency_summary(ms_all))
            row["q"][name] = entry
        out_rows.append(row)
    gts = {
        name: {**gt, "gt_ids": sorted(gt["gt_ids"]), "pool_preds": sorted(gt["pool_preds"])}
        for name, gt in ground_truth.items()
    }
    return {"binding": binding, **meta, "rows": out_rows, "ground_truth": gts}
