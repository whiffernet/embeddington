"""Typed error hierarchy shared across the Embeddington format + apply layers."""


class EmbeddingtonError(Exception):
    """Base class for all Embeddington errors."""


class RecordError(EmbeddingtonError):
    """A diff record is malformed or has an unknown op/kind."""


class ManifestError(EmbeddingtonError):
    """A manifest is malformed or missing required structure."""


class ChecksumError(EmbeddingtonError):
    """A downloaded asset's sha256 does not match the manifest."""


class ChainGapError(EmbeddingtonError):
    """The diff chain has a gap: a diff's prev_sha does not match the running cursor."""


class SchemaVersionError(EmbeddingtonError):
    """The manifest's schema_version major exceeds what this client supports."""
