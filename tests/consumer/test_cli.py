import types
from unittest.mock import MagicMock

import pytest

from consumer import cli


def test_cli_update_parses_and_dispatches(monkeypatch, tmp_path):
    calls = {}

    def fake_run_update(args):
        calls["repo"] = args.repo
        calls["cursor"] = args.cursor
        return 0

    monkeypatch.setattr(cli, "_cmd_update", fake_run_update)
    rc = cli.main(["update", "--repo", "me/embeddington", "--cursor", str(tmp_path / ".cursor")])
    assert rc == 0
    assert calls["repo"] == "me/embeddington"


def test_cli_requires_subcommand():
    import pytest

    with pytest.raises(SystemExit):
        cli.main([])  # no subcommand -> argparse exits


# --- go-public: no auth, preflight before download, --repo defaults ----------


def _fake_modules(monkeypatch, built):
    """Stub every heavy dependency of _cmd_update; record fetcher construction."""

    class _SpyFetcher:
        def __init__(self, *a, **k):
            built["args"] = a
            built["kwargs"] = k

    fake_updater = types.SimpleNamespace(
        update=lambda *a, **k: {"mode": "up_to_date", "cursor": "abc123"},
        BaselineRequired=type("BaselineRequired", (Exception,), {}),
    )
    monkeypatch.setattr(cli, "HttpFetcher", _SpyFetcher)
    monkeypatch.setattr(cli, "updater", fake_updater)
    monkeypatch.setattr(
        cli,
        "release_client",
        types.SimpleNamespace(ReleaseClient=lambda *a, **k: MagicMock()),
    )
    monkeypatch.setattr(
        cli,
        "writers",
        types.SimpleNamespace(
            QdrantConsumerWriter=types.SimpleNamespace(connect=lambda *a, **k: MagicMock()),
            ArangoConsumerWriter=types.SimpleNamespace(connect=lambda *a, **k: MagicMock()),
        ),
    )
    monkeypatch.setattr(
        cli,
        "restore_ops",
        types.SimpleNamespace(make_baseline_importer=lambda *a, **k: MagicMock()),
    )
    monkeypatch.setattr(cli, "_preflight", lambda args: None)


def test_update_builds_an_unauthenticated_fetcher(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    built = {}
    _fake_modules(monkeypatch, built)

    rc = cli.main(["update"])

    assert rc == 0
    assert built["args"] == () and "token" not in built["kwargs"], (
        "the fetcher must be constructed with no token"
    )


def test_repo_defaults_so_update_needs_no_arguments():
    ns = cli._build_parser().parse_args(["update"])
    assert ns.repo == "whiffernet/embeddington"


def test_preflight_runs_before_any_release_fetch(monkeypatch):
    """The 828 MB mistake: v1 pulled the whole baseline before ever checking the
    Arango password. Preflight must fire before ReleaseClient is even built."""
    order = []
    built = {}
    _fake_modules(monkeypatch, built)
    monkeypatch.setattr(cli, "_preflight", lambda args: order.append("preflight"))
    monkeypatch.setattr(
        cli,
        "release_client",
        types.SimpleNamespace(
            ReleaseClient=lambda *a, **k: order.append("release_client") or MagicMock()
        ),
    )

    cli.main(["update"])

    assert order and order[0] == "preflight"


def test_preflight_rejects_bad_arango_credentials(monkeypatch):
    import urllib.error

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if "/_api/version" in url:
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

        class _OK:
            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _OK()

    monkeypatch.setattr("consumer.cli.urllib.request.urlopen", fake_urlopen)
    ns = cli._build_parser().parse_args(["update"])
    with pytest.raises(SystemExit) as exc:
        cli._preflight(ns)
    assert "consumer/.env" in str(exc.value), "the 401 message must tell the user the fix"


def test_no_token_symbols_remain():
    import inspect

    src = inspect.getsource(cli)
    assert "GITHUB_TOKEN" not in src
    assert "GhFetcher" not in src
