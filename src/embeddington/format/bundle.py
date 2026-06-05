"""Read/write diff bundles as newline-delimited JSON, optionally zstd-compressed.

A path ending in ``.zst`` is transparently compressed/decompressed.
"""

from pathlib import Path

import zstandard

from embeddington.format import records


def _is_zst(path):
    return str(path).endswith(".zst")


def write_bundle(path, record_list):
    """Write records to a bundle file (zstd-compressed iff the path ends in .zst).

    Args:
        path: Destination path.
        record_list: Iterable of record dicts (header first by convention).
    """
    body = "\n".join(records.encode(r) for r in record_list).encode("utf-8")
    if _is_zst(path):
        body = zstandard.ZstdCompressor().compress(body)
    Path(path).write_bytes(body)


def read_bundle(path):
    """Yield decoded records from a bundle file (handles .zst transparently).

    Yields:
        Record dicts, one per non-empty line.
    """
    raw = Path(path).read_bytes()
    if _is_zst(path):
        raw = zstandard.ZstdDecompressor().decompress(raw)
    for line in raw.decode("utf-8").splitlines():
        if line.strip():
            yield records.decode(line)
