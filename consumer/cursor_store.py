"""Read/write the local cursor file (the client's current head_sha)."""

from pathlib import Path


def read_cursor(path):
    """Return the stored head_sha, or None if the cursor file does not exist."""
    p = Path(path)
    return p.read_text(encoding="utf-8").strip() if p.exists() else None


def write_cursor(path, head_sha):
    """Write head_sha to the cursor file (creating parent dirs as needed)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(head_sha.strip(), encoding="utf-8")
