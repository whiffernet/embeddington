"""Render a shortest_path() result into the arrow-chain text a judge reads.

Format is pinned to match the 2026-07-22 seed-label validation run (spec
§2.7/§4/M2) — the consensus concordance bar was measured against text in
exactly this shape, so changing the separator or arrow style invalidates that
validation and requires re-running it.
"""

from typing import Any, Optional


def render_path(path: Optional[dict[str, Any]]) -> str:
    """Render nodes/edges into "A (Type)  --[PRED]-->  B (Type)  --..." text.

    Args:
        path: A shortest_path() result (``{"nodes": [...], "edges": [...]}``),
            or None / a no-path result (``{"nodes": [], "edges": [], "no_path": True}``).

    Returns:
        The rendered chain, or "" if there is no path to render.
    """
    if not path or not path.get("nodes"):
        return ""

    nodes = path["nodes"]
    edges = path["edges"]
    parts = [f"{nodes[0]['name']} ({nodes[0]['type']})"]
    for i, edge in enumerate(edges):
        parts.append(f"--[{edge['predicate']}]-->")
        parts.append(f"{nodes[i + 1]['name']} ({nodes[i + 1]['type']})")
    return "  ".join(parts)
