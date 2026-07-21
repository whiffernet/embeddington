"""CLI flow: flag parsing, doctor exit codes, step ordering, error rendering."""

import io
from pathlib import Path

from rich.console import Console

from installer import cli, errors
from installer.state import InstallState

ALL_GOOD = InstallState(True, True, True, True, True, True)
FRESH = InstallState(False, False, False, False, False, False)


def console():
    return Console(file=io.StringIO(), force_terminal=False, width=100)


class Recorder:
    """Stub step functions that record call order."""

    def __init__(self, state=FRESH, fail_at=None):
        self.order = []
        self.state = state
        self.fail_at = fail_at

    def step(self, name, ret=None):
        def _step(*args, **kwargs):
            self.order.append(name)
            if name == self.fail_at:
                raise errors.SetupError("EMB-31", "compose died", "fix it")
            return ret

        return _step

    def _record(self, name):
        """Log a call and return None (for deps whose return value the test overrides)."""
        self.order.append(name)
        return None


def make_deps(rec):
    """The dependency bundle main() threads through the flow."""
    from installer.runner import RunResult

    return {
        "detect_state": rec.step("state", rec.state),
        "run_preflight": rec.step("preflight", []),
        "git_pull": rec.step("git_pull", RunResult(0, "", "")),
        "ensure_docker": rec.step("docker"),
        "ensure_env": rec.step("env"),
        "read_password": rec.step("password", "pw"),
        "compose_up": rec.step("compose"),
        "wait_for_services": rec.step("wait"),
        "run_import": rec.step(
            "import",
            {
                "mode": "baseline",
                "applied": 0,
                "cursor": "abc",
                "baseline": {"tag": "t", "points": 1, "entities": 1, "edges": 1},
                "adopted_from": None,
            },
        ),
        "proof_of_life": rec.step("proof", (152_194, 41_000)),
        "claude_wiring": rec.step("claude", "skipped"),
        "install_cron": rec.step("install_cron", "skipped-unattended"),
        "refresh_cron": rec.step("refresh_cron", "unchanged"),
        "run_uninstall": rec.step("uninstall", 0),
        "git_head": rec.step("git_head", "OLDSHA"),
        "git_changed_files": lambda _pre: rec._record("git_changed_files") or [],
        "resync_venv": rec.step("resync_venv", RunResult(0, "", "")),
        "merge_env": rec.step("merge_env", []),
        "index_absent": lambda: rec._record("index_absent") or False,
        "cron_present": lambda: rec._record("cron_present") or False,
    }


def run_main(argv, rec):
    return cli.main(argv, console=console(), deps=make_deps(rec), input_fn=lambda: "")


def test_fresh_install_runs_steps_in_order():
    rec = Recorder(state=FRESH)
    assert run_main(["--yes"], rec) == 0
    assert rec.order == [
        "state",
        "preflight",
        "docker",
        "env",
        "password",
        "compose",
        "wait",
        "import",
        "proof",
        "claude",
        "install_cron",
    ]


def test_step_failure_prints_the_code_and_exits_1():
    rec = Recorder(state=FRESH, fail_at="compose")
    assert run_main(["--yes"], rec) == 1


def test_doctor_mode_mutates_nothing_and_exit_reflects_state():
    healthy = Recorder(state=ALL_GOOD)
    assert run_main(["--check"], healthy) == 0
    assert healthy.order == ["state", "preflight"]  # read-only steps only

    broken = Recorder(state=InstallState(True, False, True, True, True, True))
    assert run_main(["--check"], broken) == 1

    embed_dead = Recorder(state=InstallState(True, True, False, True, True, True))
    assert run_main(["--check"], embed_dead) == 1  # a dead embed is NOT healthy


def test_existing_install_with_yes_defaults_to_update():
    rec = Recorder(state=ALL_GOOD)
    assert run_main(["--yes"], rec) == 0
    # Update now applies container/config drift too (idempotent), not just data.
    assert "git_pull" in rec.order
    assert "import" in rec.order and "compose" in rec.order


def test_update_flow_runs_full_sequence_in_order():
    from rich.console import Console

    con = Console(record=True, width=200)
    rec = Recorder(state=ALL_GOOD)
    deps = make_deps(rec)
    # A pull that changed mcp/ and pyproject so venv-resync fires and the mcp hint shows.
    deps["git_changed_files"] = lambda _pre: ["pyproject.toml", "mcp/server.py"]
    assert cli.main([], console=con, deps=deps, input_fn=lambda: "u") == 0
    # compose + wait now run on Update (the whole point), and after git_pull.
    assert rec.order.index("git_pull") < rec.order.index("compose")
    assert rec.order.index("compose") < rec.order.index("wait")
    assert rec.order.index("wait") < rec.order.index("import")
    assert "resync_venv" in rec.order  # pyproject changed
    assert "merge_env" in rec.order
    # mcp/ changed -> the receipt must carry the restart hint (end-to-end, not just the renderer).
    assert "reopen Claude Desktop" in con.export_text()


def test_failing_disk_preflight_aborts_before_any_mutation():
    from installer.preflight import CheckResult

    rec = Recorder(state=FRESH)
    deps = make_deps(rec)
    deps["run_preflight"] = lambda _c: [CheckResult("disk", False, "2 GB free")]
    assert cli.main(["--yes"], console=console(), deps=deps, input_fn=lambda: "") == 1
    assert "compose" not in rec.order and "import" not in rec.order


def test_foreign_port_preflight_aborts_before_any_mutation():
    from installer.preflight import CheckResult

    rec = Recorder(state=FRESH)
    deps = make_deps(rec)
    deps["run_preflight"] = lambda _c: [
        CheckResult("port 6333", False, "taken by something that isn't qdrant")
    ]
    assert cli.main(["--yes"], console=console(), deps=deps, input_fn=lambda: "") == 1
    assert "compose" not in rec.order and "import" not in rec.order


def test_uninstall_flag_routes_to_uninstall():
    rec = Recorder(state=ALL_GOOD)
    assert run_main(["--yes", "--uninstall"], rec) == 0
    assert rec.order[-1] == "uninstall"


def test_repair_with_dead_embed_still_runs_compose():
    # containers_running=True but embed_running=False: an install where qdrant+arango
    # are up but embed crashed. Repair must not skip compose_up just because the menu
    # gate (containers_running alone) says "already running".
    state = InstallState(True, True, False, True, True, True)
    rec = Recorder(state=state)
    result = cli.main(
        [],
        console=console(),
        deps=make_deps(rec),
        input_fn=lambda: "r",  # menu -> Repair
    )
    assert result == 0
    assert "compose" in rec.order


def test_install_flow_offers_cron_after_claude():
    rec = Recorder(state=FRESH)
    deps = make_deps(rec)
    deps["install_cron"] = rec.step("install_cron", "installed")
    assert cli.main(["--yes"], console=console(), deps=deps, input_fn=lambda: "") == 0
    assert rec.order.index("install_cron") > rec.order.index("claude")


def test_cron_receipt_enabled():
    line = cli._cron_receipt("installed", "/opt/emb")
    assert "enabled" in line.lower()
    assert "--uninstall" in line


def test_cron_receipt_cron_down_warns():
    line = cli._cron_receipt("installed-cron-down", "/opt/emb")
    assert "enabled" in line.lower()
    assert "cron" in line.lower() and ("won't" in line.lower() or "not running" in line.lower())
    # generic-correct: must NOT hardcode a platform-specific start command (macOS has no
    # `service`), so it points at the README instead.
    assert "service cron start" not in line


def test_cron_receipt_not_set_up_prints_manual_line():
    for outcome in ("declined", "skipped-unattended", "no-crontab"):
        line = cli._cron_receipt(outcome, "/opt/emb")
        assert "not set up" in line.lower()
        assert "cd /opt/emb" in line  # the manual crontab line is printed


def test_cron_line_is_built_from_the_actual_repo_root():
    # Note: the line's log redirect legitimately contains the literal string
    # "$HOME/embeddington" (as a prefix of $HOME/embeddington-update.log), so the
    # assertion below targets the `cd` clause specifically rather than the whole line.
    line = cli.cron_line(Path("/custom/spot/embeddington"))
    assert "cd /custom/spot/embeddington &&" in line
    assert "cd $HOME/embeddington &&" not in line


def test_force_baseline_reaches_run_import():
    captured = {}

    rec = Recorder(state=FRESH)
    deps = make_deps(rec)

    def spy_import(*args, **kwargs):
        captured.update(kwargs)
        rec.order.append("import")
        return {
            "mode": "baseline",
            "applied": 0,
            "cursor": "x",
            "baseline": {"tag": "t", "points": 1, "entities": 1, "edges": 1},
            "adopted_from": None,
        }

    deps["run_import"] = spy_import
    assert (
        cli.main(["--yes", "--force-baseline"], console=console(), deps=deps, input_fn=lambda: "")
        == 0
    )
    assert captured.get("force_baseline") is True


def test_update_skips_venv_resync_when_no_packaging_change():
    rec = Recorder(state=ALL_GOOD)
    deps = make_deps(rec)
    deps["git_changed_files"] = lambda _pre: ["consumer/updater.py"]  # code, not packaging
    assert cli.main(["--yes"], console=console(), deps=deps, input_fn=lambda: "") == 0
    assert "resync_venv" not in rec.order
    assert "compose" in rec.order  # everything else still runs


def test_update_skips_cron_offer_when_already_present():
    rec = Recorder(state=ALL_GOOD)
    deps = make_deps(rec)
    deps["git_changed_files"] = lambda _pre: []
    deps["cron_present"] = lambda: True
    called = []
    deps["install_cron"] = lambda *a, **k: called.append(1) or "installed"
    assert cli.main(["--yes"], console=console(), deps=deps, input_fn=lambda: "") == 0
    assert called == []  # never offered when a line already exists


def test_update_with_cron_present_silently_refreshes_never_installs():
    # Present -> refresh_cron only, prompt-free: input_fn raises if ever called, which
    # would only happen if the flow fell through to install_cron's confirm prompt.
    def explode():
        raise AssertionError("update with cron present must never prompt")

    rec = Recorder(state=ALL_GOOD)
    deps = make_deps(rec)
    deps["git_changed_files"] = lambda _pre: []
    deps["cron_present"] = lambda: True
    assert cli.main(["--yes"], console=console(), deps=deps, input_fn=explode) == 0
    assert "refresh_cron" in rec.order
    assert "install_cron" not in rec.order


def test_update_with_cron_absent_offers_install_as_before():
    rec = Recorder(state=ALL_GOOD)
    deps = make_deps(rec)
    deps["git_changed_files"] = lambda _pre: []
    deps["cron_present"] = lambda: False
    installed = []
    deps["install_cron"] = lambda *a, **k: installed.append(1) or "installed"
    refreshed = []
    deps["refresh_cron"] = lambda _c: refreshed.append(1) or "refreshed"
    assert cli.main(["--yes"], console=console(), deps=deps, input_fn=lambda: "") == 0
    assert installed == [1]
    assert refreshed == []


def test_update_receipt_refreshed_line_only_on_refreshed():
    line = cli._update_receipt(
        {"data_mode": "up_to_date", "applied": 0}, 152194, 41000, False, "refreshed", "/opt/emb"
    )
    assert "cron refreshed" in line

    unchanged_line = cli._update_receipt(
        {"data_mode": "up_to_date", "applied": 0}, 152194, 41000, False, "unchanged", "/opt/emb"
    )
    assert "cron refreshed" not in unchanged_line
    assert "One-time upgrades" not in unchanged_line


def test_update_retries_import_once_when_stores_recovering(monkeypatch):
    from installer import errors

    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)  # no real wait
    rec = Recorder(state=ALL_GOOD)
    deps = make_deps(rec)
    deps["git_changed_files"] = lambda _pre: []
    attempts = {"n": 0}

    def flaky_import(*a, **k):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise errors.SetupError("EMB-45", "stores recovering", "wait and re-run")
        rec.order.append("import")
        return {
            "mode": "diffs",
            "applied": 1,
            "cursor": "x",
            "baseline": None,
            "adopted_from": None,
        }

    deps["run_import"] = flaky_import
    assert cli.main(["--yes"], console=console(), deps=deps, input_fn=lambda: "") == 0
    assert attempts["n"] == 2  # failed once (WAL), retried, succeeded


def test_update_does_not_retry_on_non_readiness_error(monkeypatch):
    from installer import errors

    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    rec = Recorder(state=ALL_GOOD)
    deps = make_deps(rec)
    deps["git_changed_files"] = lambda _pre: []
    attempts = {"n": 0}

    def bad_import(*a, **k):
        attempts["n"] += 1
        raise errors.SetupError("EMB-42", "checksum", "re-run")  # not a readiness code

    deps["run_import"] = bad_import
    assert cli.main(["--yes"], console=console(), deps=deps, input_fn=lambda: "") == 1
    assert attempts["n"] == 1  # surfaced immediately, no retry


def test_update_receipt_light_shape():
    line = cli._update_receipt(
        {"data_mode": "up_to_date", "applied": 0}, 152194, 41000, False, None, "/opt/emb"
    )
    assert "already current" in line
    assert "One-time upgrades" not in line


def test_update_receipt_heavy_shape_enumerates():
    line = cli._update_receipt(
        {"data_mode": "diffs", "applied": 41, "deps": True, "env": True},
        152194,
        41000,
        True,
        "installed",
        "/opt/emb",
    )
    assert "One-time upgrades applied" in line
    assert "Claude search tools updated" in line
    assert "consumer/.env" in line
    assert "Auto-updates: enabled" in line


def test_update_flow_wraps_merge_env_oserror_as_setup_error():
    # merge_env ultimately does a bare `open(env_file, "a")`; a read-only fs or full
    # disk raises OSError. Every other _update_flow step maps failure to a SetupError
    # that main()'s `except errors.SetupError` renders -- merge_env must not be the
    # one step that raw-tracebacks instead.
    rec = Recorder(state=ALL_GOOD)
    deps = make_deps(rec)

    def boom(_console):
        raise OSError("Read-only file system")

    deps["merge_env"] = boom
    assert cli.main(["--yes"], console=console(), deps=deps, input_fn=lambda: "") == 1


def test_update_flow_surfaces_resync_venv_failure_without_claiming_resynced():
    from installer.runner import RunResult

    con = Console(record=True, width=200)
    rec = Recorder(state=ALL_GOOD)
    deps = make_deps(rec)
    deps["git_changed_files"] = lambda _pre: ["pyproject.toml"]
    deps["resync_venv"] = lambda _c: RunResult(1, "", "boom")
    assert cli.main([], console=con, deps=deps, input_fn=lambda: "u") == 0
    out = con.export_text()
    assert "re-sync" in out.lower() and "fail" in out.lower()
    assert "re-synced" not in out


def test_production_merge_env_writes_memory_cap(tmp_path):
    import types

    (tmp_path / "consumer").mkdir()
    (tmp_path / "consumer" / ".env").write_text("ARANGO_ROOT_PASSWORD=secret\n")
    deps = cli._production_deps(tmp_path, types.SimpleNamespace())
    added = deps["merge_env"](None)
    assert added == ["ARANGO_MEMORY_CAP"]
    text = (tmp_path / "consumer" / ".env").read_text()
    assert "ARANGO_MEMORY_CAP=" in text  # real value written to the real path
    assert "ARANGO_ROOT_PASSWORD=secret" in text  # user value untouched
