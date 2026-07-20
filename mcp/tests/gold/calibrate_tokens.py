#!/usr/bin/env python3
"""Calibrate budget.estimate_tokens against a real-tokenizer proxy (#44).

Issue #44's core complaint: the response-ceiling gate polices its 12,000-token
ceiling using ``budget.estimate_tokens`` -- a divide-by-3 heuristic over
compact-JSON character length -- with zero calibration against how many tokens
a real tokenizer actually counts. This script closes that gap using the
committed worst-case response dumps under ``mcp/tests/battery_results/`` as
the fixed corpus (no live stack required).

Tokenizer choice: cl100k_base (tiktoken) is a documented PROXY, not a claim
that it matches Claude's tokenizer -- Anthropic does not publish one. This is
acceptable here because the calibration only cares about the DIRECTION and
MAGNITUDE of estimate_tokens's underestimation (does the divide-by-3 heuristic
undercount relative to a real BPE tokenizer, and by how much), not about
reproducing Claude's exact token counts. Any reasonable modern BPE tokenizer
serves that comparison; cl100k_base is well-established and freely available.

For each ``*worst-response.json`` battery result, this script loads the
stored ``response`` object, computes the heuristic estimate and the cl100k
real count over the SAME compact-JSON serialization estimate_tokens itself
uses (``json.dumps(obj, separators=(",", ":"), ensure_ascii=False)``), and
derives:

- ``ratio`` per file: est / real
- ``e``: the worst-case (maximum) underestimation fraction across all files,
  clamped to >= 0 -- files where the heuristic OVERestimates don't reduce e
  (this is a safety margin, not an average-case fit)
- ``calibrated_bar``: ``int(9000 * (1 - e))`` -- the corrected gate bar the
  Task 3 re-tune sweep consumes

Usage (from mcp/, or anywhere -- paths are resolved relative to this file)::

    ../../.venv/bin/python calibrate_tokens.py

Writes ``token_calibration.json`` next to this script.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import tiktoken

_MCP = Path(__file__).resolve().parents[2]  # mcp/
sys.path.insert(0, str(_MCP))

import budget  # noqa: E402

GOLD = Path(__file__).resolve().parent
BATTERY_RESULTS = GOLD.parent / "battery_results"
TOKENIZER = "cl100k_base"
NOMINAL_BAR = 9000


def main() -> None:
    files = sorted(BATTERY_RESULTS.glob("*worst-response.json"))
    if not files:
        raise SystemExit(f"no *worst-response.json files found under {BATTERY_RESULTS}")

    enc = tiktoken.get_encoding(TOKENIZER)
    rows = []
    for path in files:
        data = json.loads(path.read_text())
        response = data["response"]
        est = budget.estimate_tokens(response)
        compact = json.dumps(response, separators=(",", ":"), ensure_ascii=False)
        real = len(enc.encode(compact))
        rows.append(
            {
                "file": path.name,
                "est": est,
                "real": real,
                "ratio": est / real,
            }
        )

    e = max(0.0, max(1 - r["ratio"] for r in rows))
    calibrated_bar = int(NOMINAL_BAR * (1 - e))

    doc = {
        "tokenizer": TOKENIZER,
        "note": (
            "cl100k_base is a documented PROXY for the LLM tokenizer actually in "
            "use (Claude's tokenizer is not public). Acceptable here because the "
            "calibrated bar cares about estimate_tokens's underestimation "
            "DIRECTION and magnitude relative to a real BPE tokenizer, not exact "
            "token-count parity."
        ),
        "rows": rows,
        "e": e,
        "nominal_bar": NOMINAL_BAR,
        "calibrated_bar": calibrated_bar,
    }
    (GOLD / "token_calibration.json").write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")

    header = f"{'file':45s} {'est':>8s} {'real':>8s} {'ratio':>8s}"
    print(header)
    for r in rows:
        print(f"{r['file']:45s} {r['est']:>8d} {r['real']:>8d} {r['ratio']:>8.3f}")
    print()
    print(f"e (max underestimation fraction) = {e:.4f}")
    print(f"calibrated_bar = int({NOMINAL_BAR} * (1 - e)) = {calibrated_bar}")


if __name__ == "__main__":
    main()
