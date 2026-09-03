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

# The two read-only probes diagnose() fires after a failing `docker info`
# (`docker context inspect`, then `docker context ls`). Splice these in wherever the
# first info result is DOWN, so a queue meant for the ladder's own calls isn't silently
# consumed by the diagnosis — an exhausted FakeRun returns rc=0 and would mask the rest.
NO_CONTEXT = [RunResult(1, "", "unknown command"), RunResult(1, "", "unknown command")]


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
    exists=lambda path: False,
    env=None,
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
        exists=exists,
        env={"PATH": "/usr/bin"} if env is None else env,
        home="/home/tester",
    )


def joined(run):
    return [" ".join(c["cmd"]) for c in run.calls]


def ensure_with_console(
    run,
    *,
    platform="macos",
    assume_yes=False,
    which=lambda n: None,
    answers=(),
    os_release="",
    wait_seconds=10,
    exists=lambda path: False,
    env=None,
):
    """Like ensure(), but also returns the console's captured output text and any
    SetupError raised (instead of letting it propagate) so callers can assert on both.
    """
    it = iter(answers)
    c = console()
    err = None
    try:
        docker_ladder.ensure_docker(
            c,
            run,
            platform=platform,
            assume_yes=assume_yes,
            which=which,
            os_release_text=os_release,
            input_fn=lambda: next(it),
            sleep=lambda s: None,
            wait_seconds=wait_seconds,
            exists=exists,
            env={"PATH": "/usr/bin"} if env is None else env,
            home="/home/tester",
        )
    except errors.SetupError as exc:
        err = exc
    return c.file.getvalue(), err


def assert_every_sudo_was_displayed(run, out):
    """Pin the invariant: no sudo command runs without first being echoed to the console."""
    for call in run.calls:
        if call["cmd"][0] == "sudo":
            assert "I'd run: " + " ".join(call["cmd"]) in out


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
    run = FakeRun([DOWN, *NO_CONTEXT, DOWN, UP, OK])
    ensure(
        run,
        platform="macos",
        which=lambda n: "/usr/bin/docker" if n == "docker" else None,
        answers=("",),
    )
    assert run.calls[-1]["cmd"] == ["docker", "compose", "version"]


def test_daemon_down_linux_offers_consented_start():
    run = FakeRun([DOWN, *NO_CONTEXT, OK, UP, OK])  # info, diag, start, poll, compose
    ensure(run, platform="linux", which=docker_and_systemctl, answers=("y",))
    assert "sudo systemctl start docker" in joined(run)


def test_daemon_down_wsl2_without_systemd_uses_service():
    def no_systemd(n):
        return "/usr/bin/docker" if n == "docker" else None

    run = FakeRun([DOWN, *NO_CONTEXT, OK, UP, OK])
    ensure(run, platform="wsl2", which=no_systemd, answers=("y",))
    assert "sudo service docker start" in joined(run)


def test_daemon_down_times_out_as_emb21():
    run = FakeRun([DOWN, *NO_CONTEXT] + [DOWN] * 50)
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
    out, err = ensure_with_console(
        run,
        platform="linux",
        which=lambda n: "/usr/bin/systemctl" if n == "systemctl" else None,
        os_release="ID=ubuntu\nID_LIKE=debian\n",
        answers=("y", "y"),  # install consent, start consent
    )
    assert err is None
    cmds = joined(run)
    assert any("apt-get install" in c and c.startswith("sudo") for c in cmds)
    assert "sudo systemctl start docker" in cmds
    assert cmds[-1] == "docker compose version"
    assert_every_sudo_was_displayed(run, out)


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
    out, err = ensure_with_console(
        run,
        platform="linux",
        which=lambda n: "/usr/bin/systemctl" if n == "systemctl" else None,
        os_release="ID=ubuntu\n",
        answers=("y", "y", "y", "y"),  # install, start, diagnostic, usermod
        wait_seconds=10,
    )
    assert err is not None
    assert err.code == "EMB-21"
    assert "log out" in err.fix.lower() or "newgrp" in err.fix
    assert any("usermod -aG docker" in c for c in joined(run))
    assert_every_sudo_was_displayed(run, out)


def test_linux_declined_start_falls_back_to_manual_wait():
    # Install consented; the daemon-start offer is declined; user presses Enter after
    # starting it themselves; the poll immediately finds it up.
    run = FakeRun([ABSENT, OK, UP, OK])  # info, apt install, poll(up), compose
    ensure(
        run,
        platform="linux",
        which=lambda n: "/usr/bin/systemctl" if n == "systemctl" else None,
        os_release="ID=ubuntu\n",
        answers=("y", "n", ""),  # install consent, start DECLINED, Enter at manual prompt
    )
    cmds = joined(run)
    assert not any("sudo systemctl start" in c for c in cmds)
    assert cmds[-1] == "docker compose version"


def test_declined_diagnostic_raises_plain_emb21_without_sudo_info():
    # Same group-denied setup, but the diagnostic consent is declined: the original
    # EMB-21 wait error must still surface, and `sudo docker info` must never run.
    run = FakeRun([ABSENT, OK, OK, DOWN, DOWN])
    # info, apt install, start, poll, poll(timeout) — no diagnostic, no usermod
    with pytest.raises(errors.SetupError) as exc:
        ensure(
            run,
            platform="linux",
            which=lambda n: "/usr/bin/systemctl" if n == "systemctl" else None,
            os_release="ID=ubuntu\n",
            answers=("y", "y", "n"),  # install, start, diagnostic DECLINED
            wait_seconds=10,
        )
    assert exc.value.code == "EMB-21"
    assert not any("sudo docker info" in c for c in joined(run))


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


# --- the guard cli.main() routes on must agree with this module (issue #87) ---


def test_an_installed_but_stopped_runtime_is_never_an_install_path():
    """cli.main() now runs this ladder BEFORE routing when Docker is present but not
    answering, deciding "present" from `docker info` rc != 127 (runner's missing-binary
    contract). That is safe only while this module's own branch — install when
    `which("docker")` is None, start when it isn't — agrees with it. If the two ever
    drift, a runtime INSTALL would run ahead of preflight's disk and port gates.

    macOS is where it would hurt: the install path here brew-installs OrbStack or guides
    Colima. With the binary present, none of that may happen — only "start it, I'll wait".
    """
    run = FakeRun([DOWN, *NO_CONTEXT, DOWN, UP, OK])
    ensure(
        run,
        platform="macos",
        which=lambda n: "/usr/local/bin/docker" if n == "docker" else None,
        answers=("",),
    )
    issued = [" ".join(c["cmd"]) for c in run.calls]
    assert not [c for c in issued if "brew" in c], f"installed a runtime: {issued}"
    assert all(c.startswith("docker ") for c in issued), issued


def test_an_absent_binary_still_reaches_the_install_ladder():
    """The other half of the same agreement: with no binary, cli.main() deliberately does
    NOT pre-run this ladder, because this is the path that installs things."""
    run = FakeRun([DOWN, *NO_CONTEXT])
    with pytest.raises(errors.SetupError):
        ensure(run, platform="macos", which=lambda n: None, answers=("x",))


# --- finding a runtime that is already here, before offering to install one (#119) ---

ORB = "/home/tester/.orbstack/bin/docker"
CTX_INSPECT = RunResult(
    0,
    '[{"Name": "desktop-linux", "Endpoints": {"docker": '
    '{"Host": "unix:///Users/u/.docker/run/docker.sock"}}}]',
    "",
)
CTX_LS = RunResult(0, "default\ndesktop-linux\norbstack\n", "")


# A location NOT in docker_probe.CANDIDATE_PATHS: sensing cannot find it, so the menu
# is reached and the "point me at it" option is the only way through — which is the
# whole case that option exists for.
CUSTOM = "/opt/custom/bin/docker"


def only_orbstack(path):
    return str(path) == ORB


def only_custom(path):
    return str(path) == CUSTOM


def test_offpath_runtime_is_adopted_instead_of_offering_an_install():
    """The reported OrbStack shape: installed, daemon fine, simply not on this PATH."""
    run = FakeRun([DOWN, UP, OK])  # info (no PATH docker), info (adopted), compose
    out, err = ensure_with_console(run, which=lambda n: None, exists=only_orbstack)
    assert err is None
    assert not any("brew" in c for c in joined(run)), joined(run)
    assert "Which one shall we set up?" not in out
    assert ORB in out


def test_adopting_puts_the_binarys_directory_on_path_for_children():
    """stack.py's `docker compose` and cron.py's which() both depend on this."""
    env = {"PATH": "/usr/bin"}
    ensure(FakeRun([DOWN, UP, OK]), which=lambda n: None, exists=only_orbstack, env=env)
    assert env["PATH"].split(":")[0] == "/home/tester/.orbstack/bin"


def test_env_override_is_adopted_by_the_ladder():
    env = {"PATH": "/usr/bin", "EMBEDDINGTON_DOCKER_BIN": "/opt/weird/docker"}
    ensure(
        FakeRun([DOWN, UP, OK]),
        which=lambda n: None,
        exists=lambda p: str(p) == "/opt/weird/docker",
        env=env,
    )
    assert env["PATH"].split(":")[0] == "/opt/weird"


def test_adopted_runtime_with_a_down_daemon_starts_the_wait_not_the_menu():
    run = FakeRun([DOWN, DOWN, *NO_CONTEXT, UP, OK])
    out, err = ensure_with_console(run, which=lambda n: None, exists=only_orbstack, answers=("",))
    assert err is None
    assert "daemon isn't answering" in out
    assert not any("brew" in c for c in joined(run))


def test_menu_offers_pointing_at_an_existing_runtime():
    out, _ = ensure_with_console(FakeRun([ABSENT]), which=brew_only, answers=("n",))
    assert "not on my PATH" in out


def test_pointing_at_a_valid_binary_adopts_it_and_installs_nothing():
    run = FakeRun([ABSENT, OK, UP, OK])  # info, <path> --version, info, compose
    env = {"PATH": "/usr/bin"}
    out, err = ensure_with_console(
        run, which=brew_only, exists=only_custom, env=env, answers=("p", CUSTOM)
    )
    assert err is None
    assert f"{CUSTOM} --version" in joined(run)
    assert not any("brew" in c for c in joined(run))
    assert env["PATH"].split(":")[0] == "/opt/custom/bin"


def test_pointing_at_nothing_is_emb22_and_names_the_path():
    """[CRITIC] EMB-22, not EMB-20: the user aimed and missed, they didn't decline."""
    _, err = ensure_with_console(
        FakeRun([ABSENT]), which=brew_only, exists=lambda p: False, answers=("p", "/nope/docker")
    )
    assert err.code == "EMB-22"
    assert "/nope/docker" in err.friendly


def test_pointing_at_something_that_isnt_a_docker_client_is_emb22():
    run = FakeRun([ABSENT, RunResult(1, "", "not an executable")])
    _, err = ensure_with_console(run, which=brew_only, exists=only_custom, answers=("p", CUSTOM))
    assert err.code == "EMB-22"
    assert "docker client" in err.friendly


# --- saying WHY the daemon isn't answering (#119) ---


def test_stale_context_is_named_before_the_user_is_told_to_wait():
    """The Docker-Desktop-to-OrbStack migration shape: the daemon is up, the client is
    dialing a socket that no longer exists. Telling them to start it wastes the wait."""
    run = FakeRun([DOWN, CTX_INSPECT, CTX_LS, UP, OK])
    out, err = ensure_with_console(
        run, which=lambda n: "/usr/local/bin/docker" if n == "docker" else None, answers=("",)
    )
    assert err is None
    assert "unix:///Users/u/.docker/run/docker.sock" in out
    assert "desktop-linux" in out
    assert "docker context use orbstack" in out


def test_emb21_carries_the_endpoint_and_the_context_hint():
    run = FakeRun([DOWN, CTX_INSPECT, CTX_LS] + [DOWN] * 20)
    _, err = ensure_with_console(
        run,
        which=lambda n: "/usr/local/bin/docker" if n == "docker" else None,
        answers=("",),
        wait_seconds=10,
    )
    assert err.code == "EMB-21"
    assert "unix:///Users/u/.docker/run/docker.sock" in err.friendly
    assert "docker context use orbstack" in err.fix


def test_a_hung_daemon_reads_as_a_timeout_not_a_missing_runtime():
    """An unbounded `docker info` made a cold-starting daemon look like a frozen
    installer; it must now come back as its own, stated, outcome."""
    import subprocess

    inner = FakeRun([UP, UP, DOWN, DOWN, DOWN, DOWN, DOWN])
    calls = {"n": 0}

    def hangs_once(cmd, **kwargs):
        if list(cmd) == ["docker", "info"] and calls["n"] == 0:
            calls["n"] += 1
            raise subprocess.TimeoutExpired(cmd, 20)
        return inner(cmd, **kwargs)

    hangs_once.calls = inner.calls
    out, err = ensure_with_console(
        hangs_once,
        which=lambda n: "/usr/local/bin/docker" if n == "docker" else None,
        answers=("",),
        wait_seconds=10,
    )
    assert "no answer within the timeout" in out
    assert "Which one shall we set up?" not in out
