#!/usr/bin/env python3
"""Measured judge–cosine agreement (spec §3.3): AUC of bge-m3 cosine
separating judge-'relevant' edges from the rest, per query.

AUC ≈ 0.5: cosine can't predict the judge (fully independent signal).
AUC ≈ 1.0: cosine reproduces the judge — a cosine-ranking selector would be
graded by a metric that agrees with it (tautology risk); the gold README must
state the discount. Run with the battery env (EMBED_URL=:18100).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import numpy as np

_TESTS = Path(__file__).resolve().parents[1]
_MCP = _TESTS.parent
for p in (str(_MCP), str(_TESTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

import config  # noqa: E402
from embedding_client import EmbeddingClient  # noqa: E402

GOLD = Path(__file__).resolve().parent


def _auc(pos: list[float], neg: list[float]) -> float | None:
    """Rank-based AUC (probability a relevant edge out-scores a non-relevant)."""
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


async def main() -> None:
    pools = json.loads((GOLD / "pools.json").read_text())
    labels = json.loads((GOLD / "labels.json").read_text())
    embed = EmbeddingClient(url=config.EMBED_URL, index=config.DEFAULT_EMBED_INDEX)
    out: dict[str, float | None] = {}
    for name, q in pools["queries"].items():
        qv = np.asarray(await embed.embed(q["query"]), dtype=np.float64)
        qv /= np.linalg.norm(qv) or 1.0
        pos, neg = [], []
        for eid, ed in q["edges"].items():
            quote = ed.get("source_quote")
            if not quote:
                continue  # unscored edges can't enter a cosine comparison
            v = np.asarray(await embed.embed(quote), dtype=np.float64)
            v /= np.linalg.norm(v) or 1.0
            score = float(qv @ v)
            (pos if labels[name][eid]["label"] == "relevant" else neg).append(score)
        out[name] = _auc(pos, neg)
        print(f"{name:32s} auc={out[name]}", file=sys.stderr)
    await embed.close()
    aucs = [a for a in out.values() if a is not None]
    doc = {
        "per_query_auc": out,
        "mean_auc": sum(aucs) / len(aucs) if aucs else None,
        "note": "AUC of bge-m3 cosine predicting judge-relevant (spec §3.3)",
    }
    (GOLD / "independence.json").write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(doc["per_query_auc"], indent=1))


if __name__ == "__main__":
    asyncio.run(main())
