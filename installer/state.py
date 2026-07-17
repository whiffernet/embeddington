"""Read-only detection of what already exists — powers idempotent re-runs and doctor mode.

Never mutates anything: pure reads of the filesystem, docker compose ps, and the store
counters the consumer already ships (point_count / entity_count).
"""

import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path

from consumer import state_paths


@dataclass(frozen=True)
class InstallState:
    env_present: bool
    containers_running: bool  # qdrant + arango (menu gating)
    embed_running: bool  # separate: builds late; doctor cares, menu gating doesn't
    stores_populated: bool
    cursor_present: bool
    mcp_deps: bool


def detect_state(repo_root, run, point_count, entity_count, *, env=None, home=None, find_spec=None):
    """Detect install state.

    Args:
        repo_root: the clone root (consumer/ lives beneath it).
        run: runner.run-compatible callable.
        point_count: callable() -> int (QdrantConsumerWriter.point_count or a fake).
        entity_count: callable() -> int (ArangoConsumerWriter.entity_count or a fake).
        env / home: forwarded to consumer.state_paths for cursor resolution.
        find_spec: importlib.util.find_spec-compatible; injected in tests.

    Returns:
        InstallState. Any store error reads as "not populated" — detection must never
        crash a doctor run; the preflight/docker checks own reporting connectivity.
    """
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    find_spec = importlib.util.find_spec if find_spec is None else find_spec

    repo_root = Path(repo_root)
    env_present = (repo_root / "consumer" / ".env").exists()

    ps = run(
        ["docker", "compose", "ps", "--services", "--status", "running"],
        cwd=repo_root / "consumer",
    )
    services = set(ps.out.split()) if ps.rc == 0 else set()
    containers_running = {"qdrant", "arango"} <= services
    embed_running = "embed" in services

    try:
        stores_populated = point_count() > 0 and entity_count() > 0
    except Exception:
        stores_populated = False

    cursor_present = state_paths.default_cursor_path(env, home).exists()
    mcp_deps = find_spec("mcp") is not None
    return InstallState(
        env_present,
        containers_running,
        embed_running,
        stores_populated,
        cursor_present,
        mcp_deps,
    )
