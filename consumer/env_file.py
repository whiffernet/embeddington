"""Read/write a single ``KEY=value`` line in a dotenv-style file.

Both the setup wizard (``installer/``) and the standalone ``embeddington-consume`` CLI
need to persist small local facts -- e.g. which KG schema generation this install's
Arango collections are currently named for -- in ``consumer/.env``, the one env file
every deployment shape already has (see ``installer/stack.py``'s password-hygiene
rules: one fact, one place on disk). Kept here, in ``consumer/``, rather than in
``installer/stack.py``, so ``consumer/cli.py`` can use it too without a
consumer -> installer dependency (installer already depends on consumer; the reverse
must never be true).
"""


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
    Every other line and its order is preserved untouched.

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
            path.write_text("\n".join(lines) + "\n")
            return
    prefix = "" if text == "" or text.endswith("\n") else "\n"
    with open(path, "a") as handle:
        handle.write(prefix + new_line + "\n")
