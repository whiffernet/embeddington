"""Unit tests for battery_sweep's call-counting instrumentation (M3, #44).

Only exercises the pure wrap/count mechanism (`_wrap_counting`/`CALL_COUNTS`)
against a fake client — the module's own singletons need a live stack, which
these tests must not require.
"""

import battery_sweep
import pytest


class _FakeEmbedClient:
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]


@pytest.mark.asyncio
async def test_wrap_counting_embed_batch_increments_call_counts():
    battery_sweep.CALL_COUNTS.clear()
    fake = _FakeEmbedClient()
    battery_sweep._wrap_counting(fake, "embed_batch", "embed_batch")

    await fake.embed_batch(["a", "b"])

    assert battery_sweep.CALL_COUNTS["embed_batch"] == 1
