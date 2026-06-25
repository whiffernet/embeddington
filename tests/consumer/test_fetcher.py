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


def test_parse_release_url():
    assert fetcher._parse_release_url(
        "https://github.com/owner/name/releases/download/diffs/manifest.json"
    ) == ("owner/name", "diffs", "manifest.json")


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/owner/name/blob/main/x",  # not a release-download URL
        "https://github.com/owner/releases/download/diffs/manifest.json",  # missing name
        "https://example.com/owner/name/releases/download/diffs/manifest.json",  # not github
    ],
)
def test_parse_release_url_rejects_bad(url):
    with pytest.raises(ValueError):
        fetcher._parse_release_url(url)


def test_http_fetcher_no_token_gets_url_directly(monkeypatch):
    """Without a token, get() hits the public download URL as-is (no API rerouting)."""
    calls = []
    f = fetcher.HttpFetcher(token=None)
    monkeypatch.setattr(f, "_http_get", lambda url, accept=None: calls.append(url) or b"raw")
    url = "https://github.com/owner/name/releases/download/diffs/manifest.json"
    assert f.get(url) == b"raw"
    assert calls == [url]  # the public URL, untouched


def test_http_fetcher_token_routes_through_api(monkeypatch):
    """With a token, get() resolves the asset id and downloads via the REST API."""
    f = fetcher.HttpFetcher(token="t0ken")
    calls = []

    def fake_http_get(url, accept=None):
        calls.append((url, accept))
        if url.endswith("/releases/tags/diffs"):
            return b'{"assets": [{"id": 42, "name": "manifest.json"}]}'
        return b"asset-bytes"

    monkeypatch.setattr(f, "_http_get", fake_http_get)
    out = f.get("https://github.com/owner/name/releases/download/diffs/manifest.json")
    assert out == b"asset-bytes"
    # 1) resolved the release by tag, 2) downloaded the asset by id as an octet-stream
    assert calls[0] == (
        "https://api.github.com/repos/owner/name/releases/tags/diffs",
        "application/vnd.github+json",
    )
    assert calls[1] == (
        "https://api.github.com/repos/owner/name/releases/assets/42",
        "application/octet-stream",
    )


def test_http_fetcher_token_missing_asset_raises(monkeypatch):
    f = fetcher.HttpFetcher(token="t0ken")
    monkeypatch.setattr(
        f,
        "_http_get",
        lambda url, accept=None: b'{"assets": [{"id": 1, "name": "other"}]}',
    )
    with pytest.raises(FileNotFoundError):
        f.get("https://github.com/owner/name/releases/download/diffs/manifest.json")
