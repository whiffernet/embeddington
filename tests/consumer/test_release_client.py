import json
from pathlib import Path

import pytest

from consumer import release_client
from embeddington.errors import ChecksumError
from embeddington.format.manifest import sha256_file


class _FakeFetcher:
    """Maps URL -> bytes; raises KeyError on an unknown URL."""

    def __init__(self, urls):
        self._urls = urls

    def get(self, url):
        return self._urls[url]

    def download(self, url, dest):
        data = self._urls[url]
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest


def _url(repo, tag, name):
    return f"https://github.com/{repo}/releases/download/{tag}/{name}"


def test_fetch_manifest_parses_json():
    repo = "me/embeddington"
    manifest = {
        "schema_version": "1.0",
        "baselines": [{"tag": "b", "head_sha": "c3d4", "assets": {}, "sha256": {}}],
        "diffs": [],
    }
    fetcher = _FakeFetcher({_url(repo, "diffs", "manifest.json"): json.dumps(manifest).encode()})
    rc = release_client.ReleaseClient(fetcher, repo=repo)
    assert rc.fetch_manifest()["schema_version"] == "1.0"


def test_download_asset_verifies_checksum(tmp_path):
    repo = "me/embeddington"
    payload = b"diff-bytes"
    url = _url(repo, "diffs", "diff-e5f6.jsonl.zst")
    fetcher = _FakeFetcher({url: payload})
    rc = release_client.ReleaseClient(fetcher, repo=repo)

    # compute the correct sha by writing once
    good = tmp_path / "good.bin"
    good.write_bytes(payload)
    correct_sha = sha256_file(good)

    dest = tmp_path / "diff.zst"
    rc.download_asset("diffs", "diff-e5f6.jsonl.zst", dest, correct_sha)
    assert dest.read_bytes() == payload


def test_download_asset_rejects_bad_checksum(tmp_path):
    repo = "me/embeddington"
    url = _url(repo, "diffs", "diff-e5f6.jsonl.zst")
    fetcher = _FakeFetcher({url: b"tampered"})
    rc = release_client.ReleaseClient(fetcher, repo=repo)
    with pytest.raises(ChecksumError):
        rc.download_asset("diffs", "diff-e5f6.jsonl.zst", tmp_path / "d.zst", "0" * 64)
