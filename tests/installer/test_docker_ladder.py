"""The ladder's branch matrix: platform x installed/down/absent x consent x outcome.

FakeRun replies from a queue; when the queue is empty it returns rc=0 — so every test
that needs "docker stays down" must queue enough DOWN results explicitly.
"""

import io

import pytest
from rich.console import Console

from installer import docker_ladder, errors
from installer.runner import RunResult
from tests.installer.conftest import FakeRun

DOWN = RunResult(1, "", "Cannot connect to the Docker daemon")
UP = RunResult(0, "", "")
OK = RunResult(0, "", "")
FAILED = RunResult(100, "", "E: Unable to locate package")
ABSENT = RunResult(127, "", "command not found: docker")


def console():
    return Console(file=io.StringIO(), force_terminal=False, width=100)


def docker_and_systemctl(name):
    return f"/usr/bin/{name}" if name in ("docker", "systemctl") else None


def brew_only(name):
    return "/opt/homebrew/bin/brew" if name == "brew" else None


def ensure(
    run,
    *,
    platform="macos",
    assume_yes=False,
    which=lambda n: None,
    answers=(),
    os_release="",
    wait_seconds=10,
):
    it = iter(answers)
    docker_ladder.ensure_docker(
        console(),
        run,
        platform=platform,
        assume_yes=assume_yes,
        which=which,
        os_release_text=os_release,
        input_fn=lambda: next(it),
        sleep=lambda s: None,
        wait_seconds=wait_seconds,
    )


def joined(run):
    return [" ".join(c["cmd"]) for c in run.calls]


def test_detect_platform():
    assert docker_ladder.detect_platform(sys_platform="darwin", proc_version_text="") == "macos"
    assert (
        docker_ladder.detect_platform(
            sys_platform="linux", proc_version_text="Linux version 5.15 microsoft-standard-WSL2"
        )
        == "wsl2"
    )
    assert (
        docker_ladder.detect_platform(sys_platform="linux", proc_version_text="Linux 6.1")
        == "linux"
    )


def test_daemon_already_up_verifies_compose_and_returns():
    run = FakeRun([UP, OK])  # docker info, docker compose version
    ensure(run, which=docker_and_systemctl)
    assert run.calls[0]["cmd"] == ["docker", "info"]
    assert run.calls[1]["cmd"] == ["docker", "compose", "version"]
    assert len(run.calls) == 2


def test_daemon_up_but_compose_plugin_missing_is_emb23():
    run = FakeRun([UP, RunResult(1, "", "'compose' is not a docker command")])
    with pytest.raises(errors.SetupError) as exc:
        ensure(run, which=docker_and_systemctl)
    assert exc.value.code == "EMB-23"


def test_daemon_down_macos_enter_then_recovers():
    # installed, down; user presses Enter after starting it; two polls then up.
    run = FakeRun([DOWN, DOWN, UP, OK])
    ensure(
        run,
        platform="macos",
        which=lambda n: "/usr/bin/docker" if n == "docker" else None,
        answers=("",),
    )
    assert run.calls[-1]["cmd"] == ["docker", "compose", "version"]


def test_daemon_down_linux_offers_consented_start():
    run = FakeRun([DOWN, OK, UP, OK])  # info, sudo systemctl start, info poll, compose
    ensure(run, platform="linux", which=docker_and_systemctl, answers=("y",))
    assert "sudo systemctl start docker" in joined(run)


def test_daemon_down_wsl2_without_systemd_uses_service():
    def no_systemd(n):
        return "/usr/bin/docker" if n == "docker" else None

    run = FakeRun([DOWN, OK, UP, OK])
    ensure(run, platform="wsl2", which=no_systemd, answers=("y",))
    assert "sudo service docker start" in joined(run)


def test_daemon_down_times_out_as_emb21():
    run = FakeRun([DOWN] * 50)
    with pytest.raises(errors.SetupError) as exc:
        ensure(
            run,
            platform="macos",
            which=lambda n: "/usr/bin/docker" if n == "docker" else None,
            answers=("",),
            wait_seconds=10,
        )
    assert exc.value.code == "EMB-21"


def test_absent_runtime_under_assume_yes_is_emb20():
    run = FakeRun([ABSENT])
    with pytest.raises(errors.SetupError) as exc:
        ensure(run, assume_yes=True)
    assert exc.value.code == "EMB-20"


def test_macos_without_brew_is_emb22():
    run = FakeRun([ABSENT])
    with pytest.raises(errors.SetupError) as exc:
        ensure(run, which=lambda name: None)  # neither docker nor brew
    assert exc.value.code == "EMB-22"


def test_macos_orbstack_consented_install_runs_brew_then_waits():
    run = FakeRun([ABSENT, OK, DOWN, UP, OK])  # info, brew install, poll, poll, compose
    ensure(run, which=brew_only, answers=("o", "y", ""))
    brew_call = run.calls[1]["cmd"]
    assert brew_call[:2] == ["brew", "install"] and "orbstack" in brew_call
    assert run.calls[1]["stream"] is True  # user watches the install live


def test_macos_orbstack_command_level_decline_is_emb20():
    # Menu choice "o" but 'n' at the displayed-command consent: NOTHING must run.
    run = FakeRun([ABSENT])
    with pytest.raises(errors.SetupError) as exc:
        ensure(run, which=brew_only, answers=("o", "n"))
    assert exc.value.code == "EMB-20"
    assert not any(c.startswith("brew") for c in joined(run))


def test_macos_colima_is_guide_only_emb22():
    run = FakeRun([ABSENT])
    with pytest.raises(errors.SetupError) as exc:
        ensure(run, which=brew_only, answers=("c",))
    assert exc.value.code == "EMB-22"
    assert not any(c.startswith("brew") for c in joined(run))


def test_macos_docker_desktop_is_guide_only_emb22():
    run = FakeRun([ABSENT])
    with pytest.raises(errors.SetupError) as exc:
        ensure(run, which=brew_only, answers=("d",))
    assert exc.value.code == "EMB-22"


def test_macos_menu_none_is_emb20():
    run = FakeRun([ABSENT])
    with pytest.raises(errors.SetupError) as exc:
        ensure(run, which=brew_only, answers=("n",))
    assert exc.value.code == "EMB-20"


def test_linux_apt_consented_install_start_verify():
    run = FakeRun([ABSENT, OK, OK, UP, OK])  # info, apt install, start, poll, compose
    ensure(
        run,
        platform="linux",
        which=lambda n: "/usr/bin/systemctl" if n == "systemctl" else None,
        os_release="ID=ubuntu\nID_LIKE=debian\n",
        answers=("y",),
    )
    cmds = joined(run)
    assert any("apt-get install" in c and c.startswith("sudo") for c in cmds)
    assert "sudo systemctl start docker" in cmds
    assert cmds[-1] == "docker compose version"


def test_linux_apt_install_failure_is_emb23_not_declined():
    # The package may not exist on this release (e.g. docker-compose-v2 on jammy):
    # an install FAILURE must not be reported as the user declining (EMB-20).
    run = FakeRun([ABSENT, FAILED])
    with pytest.raises(errors.SetupError) as exc:
        ensure(
            run, platform="linux", which=lambda n: None, os_release="ID=ubuntu\n", answers=("y",)
        )
    assert exc.value.code == "EMB-23"


def test_linux_group_denied_after_install_is_emb21_with_usermod_offer():
    # Daemon comes up but the user's socket access doesn't (fresh docker group).
    # Plain `docker info` fails through the timeout; `sudo docker info` succeeds.
    run = FakeRun([ABSENT, OK, OK, DOWN, DOWN, OK, OK])
    # info, apt install, start, poll, poll(timeout), sudo docker info, usermod
    with pytest.raises(errors.SetupError) as exc:
        ensure(
            run,
            platform="linux",
            which=lambda n: "/usr/bin/systemctl" if n == "systemctl" else None,
            os_release="ID=ubuntu\n",
            answers=("y", "y"),
            wait_seconds=10,
        )
    assert exc.value.code == "EMB-21"
    assert "log out" in exc.value.fix.lower() or "newgrp" in exc.value.fix
    assert any("usermod -aG docker" in c for c in joined(run))


def test_linux_unknown_distro_is_emb23():
    run = FakeRun([ABSENT])
    with pytest.raises(errors.SetupError) as exc:
        ensure(run, platform="linux", which=lambda n: None, os_release="ID=slackware\n")
    assert exc.value.code == "EMB-23"


def test_linux_install_declined_is_emb20():
    run = FakeRun([ABSENT])
    with pytest.raises(errors.SetupError) as exc:
        ensure(
            run, platform="linux", which=lambda n: None, os_release="ID=ubuntu\n", answers=("n",)
        )
    assert exc.value.code == "EMB-20"
