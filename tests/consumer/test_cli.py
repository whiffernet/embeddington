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
    """Pins the (cwd, home) argument order into legacy_cursor_candidates.

    Both cwd and home get a cursor so a transposed call at the ``_resolve_paths``
    call site produces the wrong ORDER, not just a differently-sourced single
    element -- a same-length list was how this bug survived the suite before.
    install_root_dir is pointed at an empty, isolated directory so this test
    never depends on whether the real clone happens to have a data/.cursor.
    """
    clone, home = tmp_path / "clone", tmp_path / "home"
    (clone / "data").mkdir(parents=True)
    (clone / "data" / ".cursor").write_text("abc")
    (home / "data").mkdir(parents=True)
    (home / "data" / ".cursor").write_text("xyz")
    args = cli._build_parser().parse_args(["update"])

    resolved = cli._resolve_paths(
        args, env={}, home=home, cwd=clone, install_root_dir=tmp_path / "unrelated_install_root"
    )

    assert resolved.legacy_cursors == [
        clone / "data" / ".cursor",
        home / "data" / ".cursor",
    ]


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

    ``_cmd_update`` calls ``_resolve_paths(args)`` with no seams, so it always probes the
    real install root's data/.cursor. Patch ``install_root`` where legacy_cursor_candidates
    actually looks it up (the state_paths module, not this one) so the exact-equality
    assertion below stays true regardless of whatever a developer's real clone holds.
    """
    clone = tmp_path / "clone"
    (clone / "data").mkdir(parents=True)
    (clone / "data" / ".cursor").write_text("abc")
    monkeypatch.chdir(clone)
    monkeypatch.setenv("EMBEDDINGTON_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(cli.state_paths, "install_root", lambda: tmp_path / "unrelated_root")

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


def test_cmd_update_schema_version_error_is_clean(monkeypatch, capsys):
    from embeddington import SchemaVersionError

    _stub_heavy_deps(monkeypatch)
    monkeypatch.setattr(
        cli,
        "updater",
        types.SimpleNamespace(
            update=lambda *a, **k: (_ for _ in ()).throw(SchemaVersionError("major 2 > 1")),
            BaselineRequired=type("BR", (Exception,), {}),
            BaselineRefused=type("BF", (Exception,), {}),
        ),
    )

    rc = cli.main(["update"])
    err = capsys.readouterr().err

    assert rc == 4
    assert "out of date" in err and "one-liner" in err and "Traceback" not in err


def test_cmd_update_passes_ensure_index(monkeypatch, tmp_path):
    """`update` must wire the shared chunk_text index hook using this surface's own URLs."""
    captured = {}

    def _spy_update(*a, **k):
        captured["kwargs"] = k
        return {
            "mode": "diffs",
            "applied": 1,
            "cursor": "x",
            "baseline": None,
            "adopted_from": None,
        }

    _stub_heavy_deps(
        monkeypatch
    )  # patches _preflight, HttpFetcher, release_client, writers, restore_ops
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
    assert callable(captured["kwargs"].get("ensure_index"))


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


# --- ensure-index -----------------------------------------------------------


def test_ensure_index_parses_and_dispatches(monkeypatch):
    calls = {}

    def fake_cmd(args):
        calls["qdrant_url"] = args.qdrant_url
        calls["collection"] = args.collection
        return 0

    monkeypatch.setattr(cli, "_cmd_ensure_index", fake_cmd)
    rc = cli.main(["ensure-index", "--qdrant-url", "http://q:6333", "--collection", "tech"])

    assert rc == 0
    assert calls == {"qdrant_url": "http://q:6333", "collection": "tech"}


def test_ensure_index_defaults_match_update(tmp_path):
    """No reason for the two commands to point at different collections by default."""
    up = cli._build_parser().parse_args(["update"])
    ei = cli._build_parser().parse_args(["ensure-index"])

    assert ei.qdrant_url == up.qdrant_url
    assert ei.collection == up.collection


@pytest.mark.parametrize(
    "status,expected_rc", [("ready", 0), ("building", 1), ("absent", 1), ("unavailable", 1)]
)
def test_ensure_index_exit_code_follows_status(monkeypatch, capsys, status, expected_rc):
    monkeypatch.setattr(
        cli,
        "lexical_index",
        types.SimpleNamespace(ensure_chunk_text_index=lambda url, collection: status),
    )

    rc = cli.main(["ensure-index"])

    assert rc == expected_rc
    assert status in capsys.readouterr().out


def test_ensure_index_passes_through_the_configured_url_and_collection(monkeypatch):
    captured = {}

    def fake_ensure(url, collection):
        captured["url"] = url
        captured["collection"] = collection
        return "ready"

    monkeypatch.setattr(
        cli, "lexical_index", types.SimpleNamespace(ensure_chunk_text_index=fake_ensure)
    )

    cli.main(["ensure-index", "--qdrant-url", "http://custom:6333", "--collection", "mine"])

    assert captured == {"url": "http://custom:6333", "collection": "mine"}


def test_ensure_index_help_documents_the_exit_codes():
    parser = cli._build_parser()
    sub_action = next(
        a
        for a in parser._subparsers._group_actions
        if a.dest == "command"  # noqa: SLF001
    )
    help_text = sub_action.choices["ensure-index"].format_help()

    assert "0" in help_text and "ready" in help_text


# --- target echo: pre-flight visibility into what a write command touches --------


def _target_line(out, field):
    """Return the whitespace-split tokens of the echoed target line for ``field``.

    Asserting URL/tag membership against this token list (rather than doing a
    substring check against the whole ``out`` blob) also pins the assertion to
    the right *line* -- e.g. that the qdrant URL is on the qdrant line, not
    merely present somewhere in the output.
    """
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0] == field:
            return parts
    raise AssertionError(f"no target line for {field!r} in:\n{out}")


def test_echo_update_targets_marks_defaults_when_nothing_was_passed(tmp_path, capsys):
    args = cli._build_parser().parse_args(["update"])
    cli._resolve_paths(args, env={}, home=tmp_path, cwd=tmp_path)

    cli._echo_update_targets(args)
    out = capsys.readouterr().out

    qdrant, arango, cursor = (
        _target_line(out, "qdrant"),
        _target_line(out, "arango"),
        _target_line(out, "cursor"),
    )
    assert out.count("(default)") == 3, "qdrant, arango, and cursor are all unset here"
    assert "(explicit)" not in out
    assert args.qdrant_url in qdrant and "(default)" in qdrant
    assert "collection=technology" in qdrant
    assert args.arango_url in arango and "(default)" in arango
    assert "db=technology_kg" in arango and "user=root" in arango
    assert str(args.cursor) in cursor and "(default)" in cursor


def test_echo_update_targets_marks_explicit_flags(tmp_path, capsys):
    cursor = tmp_path / "mine" / ".cursor"
    qdrant_url = "http://q.example:6333"
    arango_url = "http://a.example:8529"
    args = cli._build_parser().parse_args(
        [
            "update",
            "--qdrant-url",
            qdrant_url,
            "--arango-url",
            arango_url,
            "--cursor",
            str(cursor),
        ]
    )
    cli._resolve_paths(args, env={}, home=tmp_path, cwd=tmp_path)

    cli._echo_update_targets(args)
    out = capsys.readouterr().out

    qdrant, arango, cursor_line = (
        _target_line(out, "qdrant"),
        _target_line(out, "arango"),
        _target_line(out, "cursor"),
    )
    assert out.count("(explicit)") == 3
    assert "(default)" not in out
    assert qdrant_url in qdrant and "(explicit)" in qdrant
    assert arango_url in arango and "(explicit)" in arango
    assert str(cursor) in cursor_line and "(explicit)" in cursor_line


def test_echo_update_targets_treats_each_flag_independently(tmp_path, capsys):
    """Passing only --qdrant-url must not mark arango or cursor as explicit too."""
    args = cli._build_parser().parse_args(["update", "--qdrant-url", "http://q.example:6333"])
    cli._resolve_paths(args, env={}, home=tmp_path, cwd=tmp_path)

    cli._echo_update_targets(args)
    out = capsys.readouterr().out

    qdrant, arango, cursor = (
        _target_line(out, "qdrant"),
        _target_line(out, "arango"),
        _target_line(out, "cursor"),
    )
    # Per-field assertions -- not aggregate counts -- so an inverted _flag_tag
    # (one that swaps "(default)" and "(explicit)") cannot pass by coincidence.
    assert "(explicit)" in qdrant and "(default)" not in qdrant
    assert "(default)" in arango and "(explicit)" not in arango
    assert "(default)" in cursor and "(explicit)" not in cursor


def test_echo_ensure_index_targets_marks_default(capsys):
    args = cli._build_parser().parse_args(["ensure-index"])

    cli._echo_ensure_index_targets(args)
    out = capsys.readouterr().out

    assert "ensure-index — targets" in out
    qdrant = _target_line(out, "qdrant")
    assert args.qdrant_url in qdrant and "(default)" in qdrant
    assert "collection=technology" in qdrant


def test_echo_ensure_index_targets_marks_explicit(capsys):
    qdrant_url = "http://custom:6333"
    args = cli._build_parser().parse_args(
        ["ensure-index", "--qdrant-url", qdrant_url, "--collection", "mine"]
    )

    cli._echo_ensure_index_targets(args)
    out = capsys.readouterr().out

    qdrant = _target_line(out, "qdrant")
    assert qdrant_url in qdrant and "(explicit)" in qdrant
    assert "collection=mine" in qdrant


def test_preflight_prints_targets_even_when_unreachable(monkeypatch, tmp_path, capsys):
    """The echo is most valuable exactly when the reachability check is about to fail --
    it must fire before that check, not only on the happy path."""
    import urllib.error

    def fake_urlopen(*a, **k):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr("consumer.cli.urllib.request.urlopen", fake_urlopen)
    args = cli._build_parser().parse_args(["update"])
    cli._resolve_paths(args, env={}, home=tmp_path, cwd=tmp_path)

    with pytest.raises(SystemExit):
        cli._preflight(args)

    out = capsys.readouterr().out
    assert "update — targets" in out
    assert args.qdrant_url in _target_line(out, "qdrant")
