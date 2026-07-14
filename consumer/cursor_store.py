"""Read/write the local cursor file (the client's current head_sha).

The write is atomic: the cursor advances once per applied diff, and a torn write would
leave a blank cursor -- which reads back as "no cursor at all" and silently triggers a full
baseline re-download over a healthy store. Write to a temp file, then os.replace().
"""

import os
from pathlib import Path


def read_cursor(path):
    """Return the stored head_sha, or None if there isn't a usable one.

    An empty or whitespace-only file is treated as absent, NOT as the empty string -- an
    empty string would slip past every ``if cursor is None`` check downstream.

    Args:
        path: Path to the cursor file.

    Returns:
        The head_sha, or None if the file is missing or blank.
    """
    p = Path(path)
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8").strip() or None


def write_cursor(path, head_sha):
    """Atomically write head_sha to the cursor file, creating parent dirs as needed.

    Args:
        path: Path to the cursor file.
        head_sha: The SHA to store.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(head_sha.strip(), encoding="utf-8")
    os.replace(tmp, p)
