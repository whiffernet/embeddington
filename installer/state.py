"""Read-only detection of what already exists — powers idempotent re-runs and doctor mode.

Never mutates anything: pure reads of the filesystem, docker compose ps, and the store
counters the consumer already ships (point_count / entity_count).
"""

import os
from dataclasses import dataclass
from pathlib import Path

from consumer import state_paths
from installer import claude_step


@dataclass(frozen=True)
class InstallState:
    env_present: bool
    containers_running: bool  # qdrant + arango (menu gating)
    embed_running: bool  # separate: builds late; doctor cares, menu gating doesn't
    stores_populated: bool
    cursor_present: bool
    mcp_deps: bool  # importable by the interpreter that actually RUNS the server
    mcp_password_resolvable: bool = False  # from mcp/.env or the consumer stack's .env
    mcp_registered: bool = False  # reachable outside the clone


def _has_key(env_file, key):
    """True iff env_file assigns key a non-empty value.

    A present-but-empty assignment is the shape `cp .env.example .env` leaves behind, and
    it fails exactly like a missing file — so it must not read as configured.
    """
    try:
        lines = env_file.read_text().splitlines()
    except OSError:
        return False
    return any(line.startswith(f"{key}=") and line.split("=", 1)[1].strip() for line in lines)


def _mcp_password_resolvable(repo_root):
    """True iff the server can find a password, in the order server.py resolves them:
    an explicit one in mcp/.env, else the consumer stack's own credential."""
    return _has_key(repo_root / "mcp" / ".env", "ARANGO_PASSWORD") or _has_key(
        repo_root / "consumer" / ".env", "ARANGO_ROOT_PASSWORD"
    )


def detect_state(repo_root, run, point_count, entity_count, *, env=None, home=None, find_spec=None):
    """Detect install state.

    Args:
        repo_root: the clone root (consumer/ lives beneath it).
        run: runner.run-compatible callable.
        point_count: callable() -> int (QdrantConsumerWriter.point_count or a fake).
        entity_count: callable() -> int (ArangoConsumerWriter.entity_count or a fake).
        env / home: forwarded to consumer.state_paths for cursor resolution.
        find_spec: accepted for call-compatibility; no longer used (see the note on
            mcp_deps below).

    Returns:
        InstallState. Any store error reads as "not populated" — detection must never
        crash a doctor run; the preflight/docker checks own reporting connectivity.
    """
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)

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

    # [CRITIC] NOT find_spec from this process. That tests the wizard's interpreter, not
    # the one .mcp.json launches — and the repo's own `mcp/` directory (no __init__.py)
    # is importable as a namespace package, so find_spec("mcp") can report success on a
    # machine with no SDK installed at all. Ask the interpreter that does the work.
    mcp_deps = claude_step.mcp_deps_installed(run, repo_root)
    return InstallState(
        env_present,
        containers_running,
        embed_running,
        stores_populated,
        cursor_present,
        mcp_deps,
        _mcp_password_resolvable(repo_root),
        claude_step.user_scope_present(run),
    )
