"""Tests for consumer.fetcher — the gh-based and token-based release fetchers."""

import pytest

from consumer import fetcher


def test_gh_fetcher_parses_tag_and_asset(monkeypatch):
    captured = {}

    class _Completed:
        stdout = b"asset-bytes"

    def fake_run(argv, **kw):
        captured["argv"] = argv
        return _Completed()

    monkeypatch.setattr(fetcher.subprocess, "run", fake_run)
    f = fetcher.GhFetcher("owner/repo")
    out = f.get(
        "https://github.com/owner/repo/releases/download/baseline-2026-06/technology.snapshot.zst"
    )
    assert out == b"asset-bytes"
    argv = captured["argv"]
    assert argv[:4] == ["gh", "release", "download", "baseline-2026-06"]
    assert "--repo" in argv and "owner/repo" in argv
    # asset name passed via -p, streamed to stdout via -O -
    assert "technology.snapshot.zst" in argv and argv[-2:] == ["-O", "-"]


def test_gh_fetcher_rejects_non_release_url():
    with pytest.raises(ValueError):
        fetcher.GhFetcher("owner/repo").get("https://github.com/owner/repo/blob/main/x")
