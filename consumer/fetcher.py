"""Fetchers that return release-asset/manifest bytes for the ReleaseClient.

Two implementations:
  * ``GhFetcher`` — shells to the GitHub CLI (``gh``), so it works against a PRIVATE
    repo using the user's own ``gh auth login`` credentials. This is the default for
    Embeddington when the user authenticates with ``gh``.
  * ``HttpFetcher`` — stdlib urllib with an optional bearer token, for environments
    without ``gh`` (set ``GITHUB_TOKEN`` to a token that can read the repo).

Private-repo note: the public ``https://github.com/<repo>/releases/download/<tag>/<asset>``
URL only honors browser *session cookies*, not a PAT — presenting a token there returns
404 for a private repo. So ``HttpFetcher`` resolves assets through the GitHub REST API
(``/releases/assets/<id>`` with ``Accept: application/octet-stream``), which is the only
token-authenticated way to download a private release asset. ``GhFetcher`` already does
the right thing because ``gh`` uses the API internally.
"""

import json
import subprocess
import urllib.request

_GITHUB_HOST = "https://github.com/"
_GITHUB_API = "https://api.github.com"


def _parse_release_url(url):
    """Parse a github.com release-download URL into ``(repo, tag, asset)``.

    Args:
        url: e.g. ``https://github.com/owner/name/releases/download/diffs/manifest.json``.

    Returns:
        A ``(repo, tag, asset)`` tuple, e.g. ``("owner/name", "diffs", "manifest.json")``.

    Raises:
        ValueError: If ``url`` is not a ``/releases/download/<tag>/<asset>`` URL.
    """
    if not url.startswith(_GITHUB_HOST) or "/releases/download/" not in url:
        raise ValueError(f"not a github release-download URL: {url}")
    repo_part, tail = url[len(_GITHUB_HOST) :].split("/releases/download/", 1)
    segs = repo_part.split("/")
    if len(segs) != 2 or not all(segs):
        raise ValueError(f"cannot parse owner/name from: {url}")
    tag, _, asset = tail.partition("/")
    if not tag or not asset:
        raise ValueError(f"cannot parse tag/asset from: {url}")
    return f"{segs[0]}/{segs[1]}", tag, asset


class _DropAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects but strip ``Authorization`` on the redirected request.

    GitHub 302s an asset download to a presigned storage URL (S3/Azure) on a different
    host; that signed URL carries its own auth and rejects a forwarded GitHub token. The
    asset flow only ever redirects cross-host, so dropping the header unconditionally here
    is both safe and necessary.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.headers.pop("Authorization", None)
            new.unredirected_hdrs.pop("Authorization", None)
        return new


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
    """Fetches release-asset bytes over HTTPS, with an optional bearer token.

    Without a token, GETs the public release-download URL directly (fine for public
    repos). WITH a token, the public download URL 404s for a private repo, so the asset
    is resolved through the GitHub REST API instead — see the module docstring.
    """

    def __init__(self, token=None, timeout=600):
        """Args: token: a PAT that can read the repo (or None); timeout: per-request seconds."""
        self._token = token
        self._timeout = timeout
        self._opener = urllib.request.build_opener(_DropAuthOnRedirect())

    def get(self, url):
        """GET the release asset at ``url`` and return its bytes.

        Routes through the GitHub REST API when a token is set (the only token-auth path
        that works for private-repo assets); otherwise GETs ``url`` directly.

        Raises:
            FileNotFoundError: If the named asset is not present on the release.
            urllib.error.HTTPError: On other HTTP failures.
        """
        if not self._token:
            return self._http_get(url)
        return self._get_private_asset(url)

    def _http_get(self, url, accept=None):
        """GET ``url`` (adding the bearer token + optional Accept) and return the body."""
        req = urllib.request.Request(url)
        if self._token:
            req.add_header("Authorization", f"Bearer {self._token}")
        if accept:
            req.add_header("Accept", accept)
        with self._opener.open(req, timeout=self._timeout) as resp:
            return resp.read()

    def _get_private_asset(self, url):
        """Resolve ``url`` to its REST asset id and download it via the API."""
        repo, tag, asset = _parse_release_url(url)
        release = json.loads(
            self._http_get(
                f"{_GITHUB_API}/repos/{repo}/releases/tags/{tag}",
                accept="application/vnd.github+json",
            ).decode("utf-8")
        )
        try:
            asset_id = next(a["id"] for a in release["assets"] if a["name"] == asset)
        except StopIteration as exc:
            raise FileNotFoundError(
                f"asset {asset!r} not found on release {tag!r} of {repo}"
            ) from exc
        return self._http_get(
            f"{_GITHUB_API}/repos/{repo}/releases/assets/{asset_id}",
            accept="application/octet-stream",
        )
