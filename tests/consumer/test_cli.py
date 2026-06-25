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
