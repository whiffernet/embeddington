"""Record where this clone lives, so the bootstrap can find it again.

`install.sh` runs before any of this package is importable, and it has no way of knowing
where a previous install put itself — so it used to default the location prompt to
`$HOME/embeddington` no matter what. A user who installed anywhere else and pressed Enter
got a SECOND clone, with a fresh `.env` whose new random password could not open the
ArangoDB volume the first one created.

The fix is a breadcrumb: the wizard knows its own clone root exactly, so it writes that
path somewhere machine-global and the bootstrap reads it back. The state directory is the
right home — it is already machine-global (one stack per machine), already the answer to
"where does this install keep things", and already removed by uninstall.
"""

from consumer import state_paths

POINTER_NAME = "install_path"


def pointer_path(env=None, home=None):
    """Where the breadcrumb lives, following the same ladder as every other state file.

    Args:
        env: environment mapping (default: os.environ).
        home: home directory (default: Path.home()).

    Returns:
        Path to the pointer file. Not created here.
    """
    import os
    from pathlib import Path

    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    return state_paths.resolve_state_dir(env, home) / POINTER_NAME


def record_install_path(repo_root, *, env=None, home=None):
    """Write the clone root to the pointer file; report whether it stuck.

    Never raises. This is a convenience for the next bootstrap, not part of the install:
    a read-only or full state directory must not fail a run that otherwise worked.

    Args:
        repo_root: the clone root to record.
        env / home: forwarded to `pointer_path` (injected in tests).

    Returns:
        True iff the path was written.
    """
    from pathlib import Path

    target = pointer_path(env=env, home=home)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{Path(repo_root).resolve()}\n")
        return True
    except OSError:
        return False
