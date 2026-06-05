from embeddington import errors


def test_all_errors_subclass_base():
    for cls in (
        errors.RecordError,
        errors.ManifestError,
        errors.ChecksumError,
        errors.ChainGapError,
        errors.SchemaVersionError,
    ):
        assert issubclass(cls, errors.EmbeddingtonError)


def test_errors_carry_message():
    err = errors.ChainGapError("expected abc, got xyz")
    assert "expected abc" in str(err)
