"""Fetchers that return release-asset/manifest bytes for the ReleaseClient.

Two implementations:
  * ``GhFetcher`` — shells to the GitHub CLI (``gh``), so it works against a PRIVATE
    repo using the user's own ``gh auth login`` credentials. This is the default for
    Embeddington, which is shared by adding outside users as repo collaborators.
  * ``HttpFetcher`` — stdlib urllib with an optional bearer token, for environments
    without ``gh`` (set ``GITHUB_TOKEN`` to a token that can read the repo).
"""

import subprocess
import urllib.request


class GhFetcher:
    """Fetches release assets via ``gh release download`` (uses the user's gh auth).

    Resolves the asset name + tag from a standard GitHub release-download URL
    (``https://github.com/<repo>/releases/download/<tag>/<asset>``) and streams the
    bytes to stdout. Works for private repos the authenticated user can read.
    """

    def __init__(self, repo, timeout=600):
        """Args: repo: "owner/name"; timeout: per-download seconds."""
        self._repo = repo
        self._timeout = timeout

    def get(self, url):
        """GET the URL's bytes via ``gh`` (parsing tag + asset from the URL).

        Raises:
            ValueError: If the URL is not a ``/releases/download/<tag>/<asset>`` URL.
            subprocess.CalledProcessError: If the download fails (e.g. not authorized).
        """
        try:
            tag, asset = url.split("/releases/download/", 1)[1].split("/", 1)
        except (IndexError, ValueError) as exc:
            raise ValueError(f"not a release-download URL: {url}") from exc
        return subprocess.run(
            [
                "gh",
                "release",
                "download",
                tag,
                "--repo",
                self._repo,
                "-p",
                asset,
                "-O",
                "-",
            ],
            check=True,
            capture_output=True,
            timeout=self._timeout,
        ).stdout


class HttpFetcher:
    """Fetches URL bytes via urllib, with an optional Authorization bearer token."""

    def __init__(self, token=None, timeout=60):
        self._token = token
        self._timeout = timeout

    def get(self, url):
        """GET the URL and return the response body as bytes."""
        req = urllib.request.Request(url)
        if self._token:
            req.add_header("Authorization", f"Bearer {self._token}")
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            return resp.read()
