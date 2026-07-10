"""Release-asset fetching.

A release asset is a plain HTTPS GET of
``https://github.com/<owner>/<repo>/releases/download/<tag>/<asset>``. No
credentials, no GitHub CLI, no REST API.

Do not add an ``Authorization`` header to these requests: GitHub answers a
release download with a redirect to signed object storage, which carries its
own auth and rejects a forwarded token.

The download timeout is a per-read socket timeout, not a whole-transfer
deadline: a slow-but-alive link keeps going; only a stall kills it.
"""

import urllib.error
import urllib.request
from pathlib import Path

_CHUNK = 1 << 20  # 1 MiB


class HttpFetcher:
    """Fetches release assets over HTTPS."""

    def __init__(self, timeout=600):
        """Args: timeout: per-read socket timeout in seconds."""
        self._timeout = timeout

    def get(self, url):
        """GET a small asset (the manifest) and return its bytes.

        Args:
            url: A release-download URL.

        Returns:
            The asset body as bytes.

        Raises:
            FileNotFoundError: If the asset is not present (HTTP 404).
            urllib.error.HTTPError: On any other HTTP failure.
        """
        with self._open(url) as resp:
            return resp.read()

    def download(self, url, dest):
        """Stream a large asset to ``dest`` without buffering it in RAM.

        Writes to ``dest.part`` and renames on success, so a died download never
        leaves a plausible-looking partial file where the caller expects a
        verified asset. The baseline is ~900 MB; one bytes object of it can OOM
        a laptop that is also running the embedder.

        Args:
            url: A release-download URL.
            dest: Target path; parent directories are created.

        Returns:
            ``dest`` as a Path.

        Raises:
            FileNotFoundError: If the asset is not present (HTTP 404).
            urllib.error.HTTPError: On any other HTTP failure.
            OSError: If the connection dies mid-stream.
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        try:
            with self._open(url) as resp, open(tmp, "wb") as fh:
                while chunk := resp.read(_CHUNK):
                    fh.write(chunk)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(dest)
        return dest

    def _open(self, url):
        """Open ``url``, translating a 404 into FileNotFoundError."""
        req = urllib.request.Request(url)
        try:
            return urllib.request.urlopen(req, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise FileNotFoundError(f"release asset not found: {url}") from exc
            raise
