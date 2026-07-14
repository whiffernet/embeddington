"""Resolve where the consumer keeps its state (cursor + scratch work dir).

The local stack is machine-global: Qdrant and ArangoDB live in named Docker volumes on
fixed ports, so there is exactly ONE stack per machine. The cursor recording which version
of that stack's data the user holds must therefore be machine-global too -- one stack, one
cursor. It used to default to a relative ``data/.cursor``, so a second working directory
silently became a second "fresh install" and re-downloaded the whole baseline.

Everything here is a pure function over an injected environment/paths, so the resolution
ladder is testable without touching real env vars or the real filesystem.
"""

from pathlib import Path

_APP = "embeddington"


def resolve_state_dir(env, home):
    """Return the directory holding this user's consumer state.

    Resolution ladder, highest precedence first:
      1. ``$EMBEDDINGTON_HOME``               -- explicit override
      2. ``$XDG_DATA_HOME/embeddington``      -- when XDG_DATA_HOME is set
      3. ``<home>/.local/share/embeddington`` -- the default

    Args:
        env: A mapping of environment variables (typically ``os.environ``).
        home: The user's home directory.

    Returns:
        The resolved state directory. It is not created here.
    """
    override = env.get("EMBEDDINGTON_HOME")
    if override:
        return Path(override)
    xdg = env.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / _APP
    return Path(home) / ".local" / "share" / _APP


def default_cursor_path(env, home):
    """Return the default cursor file path (``<state_dir>/.cursor``).

    Args:
        env: A mapping of environment variables.
        home: The user's home directory.

    Returns:
        The default cursor path.
    """
    return resolve_state_dir(env, home) / ".cursor"


def default_work_dir(env, home):
    """Return the default scratch directory for downloads (``<state_dir>/work``).

    Args:
        env: A mapping of environment variables.
        home: The user's home directory.

    Returns:
        The default work directory.
    """
    return resolve_state_dir(env, home) / "work"


def install_root():
    """Return the directory this package is installed under.

    For the documented install (``pip install -e .`` from a clone) that is the user's
    clone, which is where their pre-v0.2 ``data/.cursor`` lives. Anchoring cursor discovery
    here -- rather than to the working directory -- is what lets the migration work from
    anywhere, including cron.

    Returns:
        The parent directory of the ``consumer`` package.
    """
    return Path(__file__).resolve().parent.parent


def legacy_cursor_candidates(cwd, home, install_root_dir=None):
    """Return existing pre-v0.2 cursor files, in preference order.

    Before the state directory existed, ``--cursor`` defaulted to a relative
    ``data/.cursor``, so real cursors are scattered across three places: the working
    directory of whoever ran it, the clone (the README always said "run from the repo
    root"), and ``$HOME`` (the cron line we shipped did not cd, and cron starts there).
    All three are offered; the caller decides which to trust.

    Args:
        cwd: The current working directory.
        home: The user's home directory.
        install_root_dir: Override for the install root (tests inject this).

    Returns:
        A de-duplicated list of cursor paths that exist on disk, most-specific first.
    """
    root = install_root() if install_root_dir is None else Path(install_root_dir)
    candidates = [
        Path(cwd) / "data" / ".cursor",
        root / "data" / ".cursor",
        Path(home) / "data" / ".cursor",
    ]
    seen, out = set(), []
    for path in candidates:
        if not path.exists():
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(path)
    return out
