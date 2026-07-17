"""CLI flow: flag parsing, doctor exit codes, step ordering, error rendering."""

import io

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
        "run_uninstall": rec.step("uninstall", 0),
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
    # Update path: refresh the clone, then import — no env/compose mutations.
    assert "git_pull" in rec.order
    assert "import" in rec.order and "compose" not in rec.order


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
