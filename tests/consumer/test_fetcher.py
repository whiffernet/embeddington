"""Tests for the release-asset fetcher.

embeddington is a public repo. A release asset is a plain HTTPS GET of a public
URL: no credentials, no GitHub CLI, no flags. The fetcher must never send an
Authorization header -- presenting one to a release-download URL is what made
the old private-repo path 404 and grow a REST-API workaround.
"""

import io
import urllib.error
from pathlib import Path

import pytest

from consumer.fetcher import HttpFetcher


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def test_get_returns_body_bytes(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        return _FakeResponse(b"asset-bytes")

    monkeypatch.setattr("consumer.fetcher.urllib.request.urlopen", fake_urlopen)
    body = HttpFetcher(timeout=42).get("https://github.com/o/r/releases/download/t/a.bin")

    assert body == b"asset-bytes"
    assert captured["url"] == "https://github.com/o/r/releases/download/t/a.bin"
    assert captured["timeout"] == 42


def test_get_never_sends_an_authorization_header(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["headers"] = dict(req.header_items())
        return _FakeResponse(b"x")

    monkeypatch.setattr("consumer.fetcher.urllib.request.urlopen", fake_urlopen)
    HttpFetcher().get("https://github.com/o/r/releases/download/t/a.bin")

    assert "authorization" not in {k.lower() for k in seen["headers"]}


def test_download_streams_to_disk_not_ram(monkeypatch, tmp_path):
    """download() must write chunks as they arrive, never buffer the whole body.

    The baseline is 828 MB; one bytes object of it can OOM an 8 GB laptop that
    is also running the embedder.
    """
    reads = []

    class _ChunkedResponse:
        def __init__(self):
            self._chunks = [b"a" * 10, b"b" * 10, b""]

        def read(self, n=-1):
            chunk = self._chunks.pop(0)
            reads.append(len(chunk))
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        "consumer.fetcher.urllib.request.urlopen", lambda req, timeout=None: _ChunkedResponse()
    )
    dest = tmp_path / "sub" / "asset.bin"
    out = HttpFetcher().download("https://github.com/o/r/releases/download/t/a.bin", dest)

    assert out == dest
    assert dest.read_bytes() == b"a" * 10 + b"b" * 10
    assert len(reads) >= 3, "body must be consumed in multiple reads, not one .read()"


def test_download_is_atomic_on_failure(monkeypatch, tmp_path):
    """A death mid-download must not leave a plausible-looking partial file at dest."""

    class _DyingResponse:
        def read(self, n=-1):
            raise OSError("connection reset")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        "consumer.fetcher.urllib.request.urlopen", lambda req, timeout=None: _DyingResponse()
    )
    dest = tmp_path / "asset.bin"
    with pytest.raises(OSError):
        HttpFetcher().download("https://github.com/o/r/releases/download/t/a.bin", dest)
    assert not dest.exists(), "failed download must not leave dest behind"


def test_missing_asset_raises_file_not_found(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr("consumer.fetcher.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(FileNotFoundError, match="a.bin"):
        HttpFetcher().get("https://github.com/o/r/releases/download/t/a.bin")


def test_other_http_errors_propagate(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 500, "Server Error", {}, None)

    monkeypatch.setattr("consumer.fetcher.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(urllib.error.HTTPError):
        HttpFetcher().get("https://github.com/o/r/releases/download/t/a.bin")


def test_no_auth_machinery_survives():
    """Guard the deletion: nothing may reintroduce an auth path here.

    Checked structurally (symbols), NOT by grepping the docstring -- the
    docstring legitimately explains WHY there is no token path.
    """
    import consumer.fetcher as f

    assert not hasattr(f, "GhFetcher")
    assert not hasattr(f, "_parse_release_url")
    assert not hasattr(f, "_DropAuthOnRedirect")
    assert "token" not in HttpFetcher.__init__.__doc__.lower()


def test_release_client_streams_large_assets(monkeypatch, tmp_path):
    """download_asset must go through fetcher.download (streaming), not get()."""
    import hashlib

    from consumer.release_client import ReleaseClient

    calls = []

    class _SpyFetcher:
        def get(self, url):
            calls.append(("get", url))
            return b"{}"

        def download(self, url, dest):
            calls.append(("download", url))
            p = Path(dest)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"data")
            return p

    rc = ReleaseClient(_SpyFetcher(), repo="o/r")
    sha = hashlib.sha256(b"data").hexdigest()
    out = rc.download_asset("t", "a.bin", tmp_path / "a.bin", sha)

    assert out == tmp_path / "a.bin"
    assert calls == [("download", "https://github.com/o/r/releases/download/t/a.bin")]
