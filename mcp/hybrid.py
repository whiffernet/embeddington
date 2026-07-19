"""Pure hybrid-retrieval helpers: identifier tokens, RRF merge, score threshold.

Kept free of I/O so the merge/threshold contract is unit-tested without a
stack (spec §5 PR 4). The lexical lane exists because this corpus is
saturated with exact identifiers — precisely where dense-only retrieval is
weakest (issue #38).
"""

from __future__ import annotations

import re

# snake_case with >=2 segments (cmdb_rel_ci) or dotted ids with >=2 segments
# (com.snc.discovery). A trailing sentence dot is not a segment separator.
_IDENTIFIER = re.compile(r"\b[a-z0-9]+(?:[._][a-z0-9]+)+\b(?<!\.)")
MAX_TOKENS = 3
RRF_K = 60


def extract_identifier_tokens(query: str) -> list[str]:
    """Identifier-like tokens from a query, deduped, first-seen order, capped.

    Args:
        query: Natural-language query text.

    Returns:
        Up to MAX_TOKENS lowercased identifier tokens (snake_case or dotted,
        each with at least two segments).
    """
    seen: list[str] = []
    for m in _IDENTIFIER.finditer(query.lower()):
        tok = m.group(0)
        if tok not in seen:
            seen.append(tok)
        if len(seen) >= MAX_TOKENS:
            break
    return seen


def rrf_merge(lanes: list[list[dict]], k: int = RRF_K, limit: int | None = None) -> list[dict]:
    """Reciprocal-rank fusion across result lanes (rank-based, no score calibration).

    Args:
        lanes: Result lists (chunk dicts with an ``id`` key), best-first.
        k: RRF constant; larger k flattens rank differences.
        limit: Optional cap on fused results.

    Returns:
        Fused, deduped (first occurrence's fields kept) chunk list, ordered by
        descending RRF score with deterministic id tie-break.
    """
    scores: dict[str, float] = {}
    first: dict[str, dict] = {}
    for lane in lanes:
        for rank, chunk in enumerate(lane, start=1):
            cid = str(chunk.get("id"))
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
            first.setdefault(cid, chunk)
    ordered = sorted(scores, key=lambda cid: (-scores[cid], cid))
    fused = [first[cid] for cid in ordered]
    return fused[:limit] if limit is not None else fused


def apply_threshold(chunks: list[dict], threshold: float) -> list[dict]:
    """Drop dense chunks scoring below ``threshold`` (<=0 disables).

    Weak chunks are dropped rather than padded in to fill ``top_k`` — a
    result set may legitimately be smaller than requested (issue #38/#47).
    """
    if threshold <= 0:
        return chunks
    return [c for c in chunks if float(c.get("score", 0.0)) >= threshold]
