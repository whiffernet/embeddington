import embeddington


def test_public_surface_is_importable():
    assert embeddington.apply_diff is not None
    assert embeddington.plan_update is not None
    assert embeddington.UpdatePlan is not None
    assert issubclass(embeddington.EmbeddingtonError, Exception)
