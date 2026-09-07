"""Read/write a single ``KEY=value`` line in a dotenv-style file.

Both the setup wizard (``installer/``) and the standalone ``embeddington-consume`` CLI
need to persist small local facts -- e.g. which KG schema generation this install's
Arango collections are currently named for -- in ``consumer/.env``, the one env file
every deployment shape already has (see ``installer/stack.py``'s password-hygiene
rules: one fact, one place on disk). Kept here, in ``consumer/``, rather than in
``installer/stack.py``, so ``consumer/cli.py`` can use it too without a
consumer -> installer dependency (installer already depends on consumer; the reverse
must never be true).

``consumer/.env`` holds the only copy of the Arango root password (baked into the
Arango volume at first init -- there is no second source), so replacing an existing
key's value is done via a sibling temp file + ``os.replace``, never a truncating
``write_text``: an interrupt between truncate and write would otherwise leave an
empty or partial file, and losing this one means an uninstall plus a ~1 GB re-download.
"""

import os
import stat
import tempfile


def read_key(path, key, default=None):
    """Return ``key``'s value from a dotenv-style file.

    Args:
        path: Path to the file. Need not exist.
        key: The ``KEY`` name to look up (matched at the start of a line as ``KEY=``).
        default: Returned when the file is unreadable, the key is absent, or its
            value is empty/whitespace-only (the shape ``cp .env.example .env``
            leaves behind for an unset value).

    Returns:
        The value (stripped), or ``default``.
    """
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return default
    for line in lines:
        if line.startswith(f"{key}="):
            value = line.split("=", 1)[1].strip()
            return value or default
    return default


def set_key(path, key, value):
    """Set ``key=value`` in a dotenv-style file: replace the existing line, or append.

    Creates the file (and writes only this one line) if it doesn't already exist.
    Every other line and its order is preserved untouched. Replacing an EXISTING
    key's value writes the whole file atomically (see ``_atomic_write``); adding a
    missing key to an existing file only ever appends, which cannot corrupt what was
    already on disk.

    Args:
        path: Path to the file.
        key: The ``KEY`` name to set.
        value: The value to assign (written verbatim, unquoted).
    """
    text = path.read_text() if path.exists() else ""
    lines = text.splitlines()
    new_line = f"{key}={value}"
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = new_line
            _atomic_write(path, "\n".join(lines) + "\n")
            return
    prefix = "" if text == "" or text.endswith("\n") else "\n"
    with open(path, "a") as handle:
        handle.write(prefix + new_line + "\n")


def _atomic_write(path, content):
    """Replace ``path``'s content via a sibling temp file + ``os.replace``.

    The temp file is created in ``path``'s OWN directory, so the final rename stays
    on one filesystem and is therefore atomic -- a crash or interrupt either leaves
    the original file completely untouched or lands the complete new content, never
    something in between. The original file's permission bits (e.g. an 0600
    ``consumer/.env``) are copied onto the replacement before the rename, so a key
    update never loosens them. Any failure before the rename (including one raised
    while writing the temp file's content) removes the temp file and re-raises,
    leaving ``path`` exactly as it was.

    Args:
        path: Path to the file to replace (need not exist).
        content: The complete new file content.
    """
    mode = path.stat().st_mode if path.exists() else None
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
        if mode is not None:
            os.chmod(tmp_name, stat.S_IMODE(mode))
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
