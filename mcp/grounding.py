"""Pure grounding classifier for enrich responses (spec §5 PR 5, issue #47).

Labels what retrieval actually returned so a caller can tell "nothing found"
and "the asked-for identifier is absent" apart from a full-looking result.
Observation only — never mutates or filters content. The MCP guarantees its
own output (everything it returns comes from a retrieved chunk or KG record);
this signal removes the padded-input condition behind confident fabrication,
it cannot control the downstream LLM.
"""

from __future__ import annotations

REASON_NO_CHUNKS = "no vector chunks cleared the score threshold"
REASON_NO_KG = "no KG concepts resolved"
REASON_KG_EMPTY = "KG returned nothing for this query"


def classify(chunks: list[dict], kg_matches: list[dict], identifier_tokens: list[str]) -> dict:
    """Classify grounding as ok / weak / none with machine-readable reasons.

    Args:
        chunks: Post-threshold vector chunks the response will carry.
        kg_matches: The response's kg_matches (edges may be empty per match).
        identifier_tokens: Identifier-like tokens extracted from the query
            (already lowercase, from hybrid.extract_identifier_tokens).

    Returns:
        {"tier": "ok"|"weak"|"none", "reasons": [str, ...]} — reasons is
        empty exactly when tier is "ok".
    """
    n_edges = sum(len(m.get("edges", [])) for m in kg_matches)
    has_chunks = len(chunks) > 0
    has_kg = n_edges > 0

    if not has_chunks and not has_kg:
        return {"tier": "none", "reasons": [REASON_NO_CHUNKS, REASON_NO_KG]}

    reasons: list[str] = []
    if identifier_tokens:
        haystack = "\n".join(
            [str(c.get("text", "")) for c in chunks]
            + [str(e.get("source_quote", "")) for m in kg_matches for e in m.get("edges", [])]
        ).lower()
        missing = [t for t in identifier_tokens if t not in haystack]
        if missing:
            reasons.append(f"identifier(s) {', '.join(missing)} not found in any returned content")
    if not has_chunks:
        reasons.append(REASON_NO_CHUNKS)
    if not has_kg:
        reasons.append(REASON_KG_EMPTY)

    if reasons:
        return {"tier": "weak", "reasons": reasons}
    return {"tier": "ok", "reasons": []}
