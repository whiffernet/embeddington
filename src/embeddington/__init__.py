"""Embeddington: versioned format + idempotent apply library for KG distribution."""

from embeddington.apply.apply_diff import apply_diff
from embeddington.apply.cursor import UpdatePlan, plan_update
from embeddington.errors import (
    ChainGapError,
    ChecksumError,
    EmbeddingtonError,
    ManifestError,
    RecordError,
    SchemaVersionError,
)

__version__ = "0.1.0"

__all__ = [
    "apply_diff",
    "plan_update",
    "UpdatePlan",
    "EmbeddingtonError",
    "RecordError",
    "ManifestError",
    "ChecksumError",
    "ChainGapError",
    "SchemaVersionError",
    "__version__",
]
