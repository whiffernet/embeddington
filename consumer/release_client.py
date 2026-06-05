"""Download the manifest + release assets from GitHub Releases, verifying integrity.

Asset URLs follow the GitHub Releases convention:
``https://github.com/<repo>/releases/download/<tag>/<asset>``. The HTTP fetcher is
injected (consumer.fetcher.HttpFetcher in production, a fake in tests).
"""

import json
from pathlib import Path

from embeddington.format.manifest import verify_asset


class ReleaseClient:
    """Fetches the manifest and downloads/verifies assets from a repo's Releases."""

    def __init__(self, fetcher, repo, diffs_tag="diffs", manifest_name="manifest.json"):
        """Args: fetcher: object with ``get(url)->bytes``; repo: "owner/name"."""
        self._fetcher = fetcher
        self._repo = repo
        self._diffs_tag = diffs_tag
        self._manifest_name = manifest_name

    def _asset_url(self, tag, asset):
        return f"https://github.com/{self._repo}/releases/download/{tag}/{asset}"

    def fetch_manifest(self):
        """Download and parse the diffs-release manifest.json."""
        raw = self._fetcher.get(self._asset_url(self._diffs_tag, self._manifest_name))
        return json.loads(raw.decode("utf-8"))

    def download_asset(self, tag, asset, dest, expected_sha256):
        """Download one asset to ``dest`` and verify its sha256.

        Raises:
            embeddington.errors.ChecksumError: If the downloaded bytes don't match.
        """
        data = self._fetcher.get(self._asset_url(tag, asset))
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        verify_asset(dest, expected_sha256)
        return dest
