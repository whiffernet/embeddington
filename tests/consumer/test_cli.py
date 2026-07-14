import types
from pathlib import Path
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


def test_resolve_paths_uses_the_injected_home_and_cwd(tmp_path):
    """Exercise the real default rung -- not the EMBEDDINGTON_HOME short-circuit."""
    args = cli._build_parser().parse_args(["update"])
    resolved = cli._resolve_paths(args, env={}, home=tmp_path / "home", cwd=tmp_path / "cwd")

    assert resolved.cursor == tmp_path / "home" / ".local/share/embeddington/.cursor"
    assert resolved.work_dir == tmp_path / "home" / ".local/share/embeddington/work"


def test_resolve_paths_honours_xdg_data_home(tmp_path):
    args = cli._build_parser().parse_args(["update"])
    resolved = cli._resolve_paths(
        args, env={"XDG_DATA_HOME": str(tmp_path / "xdg")}, home=tmp_path, cwd=tmp_path
    )

    assert resolved.cursor == tmp_path / "xdg" / "embeddington" / ".cursor"


def test_resolve_paths_env_override_wins(tmp_path):
    args = cli._build_parser().parse_args(["update"])
    resolved = cli._resolve_paths(
        args, env={"EMBEDDINGTON_HOME": str(tmp_path / "s")}, home=tmp_path, cwd=tmp_path
    )

    assert resolved.cursor == tmp_path / "s" / ".cursor"


def test_explicit_cursor_flag_beats_the_env(tmp_path):
    args = cli._build_parser().parse_args(["update", "--cursor", str(tmp_path / "mine/.cursor")])
    resolved = cli._resolve_paths(
        args, env={"EMBEDDINGTON_HOME": str(tmp_path / "s")}, home=tmp_path, cwd=tmp_path
    )

    assert resolved.cursor == tmp_path / "mine" / ".cursor"  # explicit wins


def test_resolve_paths_discovers_legacy_cursors(tmp_path):
    clone = tmp_path / "clone"
    (clone / "data").mkdir(parents=True)
    (clone / "data" / ".cursor").write_text("abc")
    args = cli._build_parser().parse_args(["update"])

    resolved = cli._resolve_paths(args, env={}, home=tmp_path / "home", cwd=clone)

    assert resolved.legacy_cursors == [clone / "data" / ".cursor"]


def test_force_baseline_defaults_off_and_parses_on():
    assert cli._build_parser().parse_args(["update"]).force_baseline is False
    assert cli._build_parser().parse_args(["update", "--force-baseline"]).force_baseline is True


def _stub_heavy_deps(monkeypatch):
    """Stub everything _cmd_update touches except updater (the thing under test)."""
    monkeypatch.setattr(cli, "_preflight", lambda args: None)
    monkeypatch.setattr(cli, "HttpFetcher", lambda *a, **k: None)
    monkeypatch.setattr(cli, "release_client", types.SimpleNamespace(ReleaseClient=MagicMock()))
    monkeypatch.setattr(
        cli,
        "writers",
        types.SimpleNamespace(
            QdrantConsumerWriter=types.SimpleNamespace(connect=MagicMock()),
            ArangoConsumerWriter=types.SimpleNamespace(connect=MagicMock()),
        ),
    )
    monkeypatch.setattr(
        cli, "restore_ops", types.SimpleNamespace(make_baseline_importer=MagicMock())
    )


def test_cmd_update_forwards_legacy_cursors_and_force_baseline(monkeypatch, tmp_path):
    """The single wire the whole migration hangs on.

    Both kwargs are keyword-only WITH defaults and consumer/ is not typechecked, so dropping
    either at the call site is silent -- adoption would be dead in production with a green
    suite. This test is what makes that impossible.
    """
    clone = tmp_path / "clone"
    (clone / "data").mkdir(parents=True)
    (clone / "data" / ".cursor").write_text("abc")
    monkeypatch.chdir(clone)
    monkeypatch.setenv("EMBEDDINGTON_HOME", str(tmp_path / "state"))

    captured = {}

    def _spy_update(*a, **k):
        captured["args"] = a
        captured["kwargs"] = k
        return {"mode": "up_to_date", "applied": 0, "cursor": "x", "baseline": None}

    _stub_heavy_deps(monkeypatch)
    monkeypatch.setattr(
        cli,
        "updater",
        types.SimpleNamespace(
            update=_spy_update,
            BaselineRequired=type("BaselineRequired", (Exception,), {}),
            BaselineRefused=type("BaselineRefused", (Exception,), {}),
        ),
    )

    assert cli.main(["update"]) == 0

    assert captured["kwargs"]["legacy_cursors"] == [clone / "data" / ".cursor"]
    assert captured["kwargs"]["force_baseline"] is False
    assert captured["args"][3] == tmp_path / "state" / ".cursor"  # resolved cursor path

    assert cli.main(["update", "--force-baseline"]) == 0
    assert captured["kwargs"]["force_baseline"] is True


def test_baseline_refused_exits_3_and_prints_the_reason(monkeypatch, capsys):
    class _Refused(Exception):
        pass

    def _boom(*a, **k):
        raise _Refused("collection 'technology' already has 152,194 points")

    _stub_heavy_deps(monkeypatch)
    monkeypatch.setattr(
        cli,
        "updater",
        types.SimpleNamespace(
            update=_boom,
            BaselineRequired=type("BaselineRequired", (Exception,), {}),
            BaselineRefused=_Refused,
        ),
    )

    rc = cli.main(["update"])

    assert rc == 3
    assert "152,194 points" in capsys.readouterr().err


def test_adoption_is_reported_to_the_user(tmp_path):
    """A silent migration is a migration nobody can debug."""
    out = cli._format_update(
        {
            "mode": "diffs",
            "applied": 2,
            "cursor": "a7b8",
            "baseline": None,
            "adopted_from": Path("/home/u/embeddington/data/.cursor"),
        }
    )
    assert "Migrated" in out and "/home/u/embeddington/data/.cursor" in out
