"""Read/write diff bundles as newline-delimited JSON, optionally zstd-compressed.

A path ending in ``.zst`` is transparently compressed/decompressed. Both
directions stream: ``write_bundle`` never materializes the full body in
memory, and ``read_bundle`` yields records lazily as it decodes them.
"""

import io
from pathlib import Path

import zstandard

from embeddington.format import records


def _is_zst(path):
    return str(path).endswith(".zst")


def write_bundle(path, record_list):
    """Write records to a bundle file, streaming (never materializes the body).

    Args:
        path: Destination path (zstd-compressed iff it ends in ``.zst``).
        record_list: Iterable (list or generator) of record dicts, header first.
    """
    path = Path(path)
    with open(path, "wb") as fh:
        if _is_zst(path):
            with zstandard.ZstdCompressor().stream_writer(fh) as sink:
                _write_lines(sink, record_list)
        else:
            _write_lines(fh, record_list)


def _write_lines(sink, record_list):
    first = True
    for r in record_list:
        if not first:
            sink.write(b"\n")
        sink.write(records.encode(r).encode("utf-8"))
        first = False


def read_bundle(path):
    """Yield decoded records from a bundle file, streaming (handles .zst).

    Yields:
        Record dicts, one per non-empty line.
    """
    path = Path(path)
    with open(path, "rb") as fh:
        if _is_zst(path):
            with zstandard.ZstdDecompressor().stream_reader(fh) as raw:
                yield from _read_lines(raw)
        else:
            yield from _read_lines(fh)


def _read_lines(raw):
    for line in io.TextIOWrapper(raw, encoding="utf-8"):
        if line.strip():
            yield records.decode(line)
