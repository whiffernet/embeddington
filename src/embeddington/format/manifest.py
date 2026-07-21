"""Manifest validation/IO plus sha256 asset-integrity helpers."""

import hashlib
import json

from embeddington.errors import ChecksumError, ManifestError

_REQUIRED_KEYS = ("schema_version", "baselines", "diffs")


def validate_manifest(m):
    """Raise ManifestError unless the manifest has the required structure.

    Args:
        m: A manifest dict.

    Raises:
        ManifestError: On missing keys, no baselines, or malformed diff entries.
    """
    if not isinstance(m, dict):
        raise ManifestError("manifest must be an object")
    for key in _REQUIRED_KEYS:
        if key not in m:
            raise ManifestError(f"manifest missing required key: {key!r}")
    if not m["baselines"]:
        raise ManifestError("manifest must list at least one baseline")
    for baseline in m["baselines"]:
        for key in ("tag", "head_sha", "assets", "sha256"):
            if key not in baseline:
                raise ManifestError(f"baseline entry missing key: {key!r}")
        fmt = baseline.get("format", "snapshot")
        if fmt not in ("snapshot", "bundle"):
            raise ManifestError(f"baseline format must be 'snapshot' or 'bundle', got {fmt!r}")
        if fmt == "bundle":
            cfg = baseline.get("qdrant_collection")
            if not isinstance(cfg, dict):
                raise ManifestError("bundle baseline missing qdrant_collection config")
            for key in ("size", "distance", "hnsw_m", "hnsw_ef_construct"):
                if key not in cfg:
                    raise ManifestError(f"qdrant_collection missing key: {key!r}")
    for diff in m["diffs"]:
        for key in ("prev_sha", "head_sha", "asset", "sha256"):
            if key not in diff:
                raise ManifestError(f"diff entry missing key: {key!r}")


def load_manifest(path):
    """Load and validate a manifest JSON file.

    Args:
        path: Path-like object pointing to the manifest JSON file.

    Returns:
        The parsed manifest dict.

    Raises:
        ManifestError: If the manifest fails validation.
    """
    with open(path, encoding="utf-8") as fh:
        m = json.load(fh)
    validate_manifest(m)
    return m


def dump_manifest(m, path):
    """Validate then write a manifest to disk as pretty JSON.

    Args:
        m: A manifest dict to write.
        path: Destination path for the JSON file.

    Raises:
        ManifestError: If the manifest fails validation.
    """
    validate_manifest(m)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(m, fh, indent=2, sort_keys=True)


def sha256_file(path, chunk_size=1 << 20):
    """Return the hex sha256 of a file, streamed in chunks.

    Args:
        path: Path-like object to the file.
        chunk_size: Read chunk size in bytes (default 1 MiB).

    Returns:
        64-character lowercase hex digest string.
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_asset(path, expected_sha256):
    """Raise ChecksumError unless the file's sha256 matches expected_sha256.

    Args:
        path: Path-like object to the file to check.
        expected_sha256: The expected 64-character hex digest.

    Raises:
        ChecksumError: If the actual digest does not match the expected one.
    """
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise ChecksumError(
            f"checksum mismatch for {path}: expected {expected_sha256}, got {actual}"
        )
