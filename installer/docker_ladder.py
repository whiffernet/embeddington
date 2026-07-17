"""The Docker ladder: get from "no runtime / daemon down" to a working `docker compose`.

Rules: nothing installs without displayed-command consent; sudo is stated before the
prompt; a DECLINED install is EMB-20, a FAILED one EMB-23, a guide-only path EMB-22 —
never conflated ([CRITIC]: a missing package must not be reported as the user saying
no); the ladder never uninstalls or reconfigures an existing runtime; and success means
BOTH `docker info` and `docker compose version` work, because several install routes
ship without the compose v2 plugin and every later step runs `docker compose <cmd>`.
"""

import getpass
import sys
import time
from pathlib import Path

from installer import ui
from installer.errors import SetupError

POLL_SECONDS = 5


def detect_platform(*, sys_platform=None, proc_version_text=None):
    """Return "macos", "wsl2", or "linux"."""
    sys_platform = sys.platform if sys_platform is None else sys_platform
    if sys_platform == "darwin":
        return "macos"
    if proc_version_text is None:
        try:
            proc_version_text = Path("/proc/version").read_text()
        except OSError:
            proc_version_text = ""
    if "microsoft" in proc_version_text.lower():
        return "wsl2"
    return "linux"


def _docker_ok(run):
    return run(["docker", "info"]).rc == 0


def _start_cmd(which):
    """systemd-aware daemon start command ([CRITIC]: default WSL2 has no systemd)."""
    if which("systemctl") is not None:
        return ["sudo", "systemctl", "start", "docker"]
    return ["sudo", "service", "docker", "start"]


def _wait_for_daemon(console, run, sleep, wait_seconds):
    """Poll `docker info` until success or timeout (EMB-21)."""
    waited = 0
    with console.status("[cyan]Waiting for the Docker daemon... The Dude abides.[/cyan]"):
        while waited < wait_seconds:
            if _docker_ok(run):
                return
            sleep(POLL_SECONDS)
            waited += POLL_SECONDS
    raise SetupError(
        "EMB-21",
        f"The Docker daemon didn't come up within {wait_seconds} seconds.",
        "Start it manually (open OrbStack/Docker Desktop, or `colima start`, or "
        "`sudo systemctl start docker`), then re-run the installer.",
    )


def _wait_with_group_check(console, run, *, assume_yes, input_fn, sleep, wait_seconds):
    """_wait_for_daemon, but a socket-permission denial gets the docker-group fix.

    [CRITIC] On a fresh Linux install the invoking user is NOT in the docker group, so
    plain `docker info` fails forever even though the daemon came up fine — without
    this check that reports as a misleading "daemon didn't come up". The group change
    cannot take effect inside the current session, so this path always ends in EMB-21
    with the re-login fix; the offer just saves the user the usermod command. The
    `sudo docker info` diagnostic itself is consent-gated like every other sudo call —
    a decline (or a failure) re-raises the original wait error unchanged.
    """
    try:
        _wait_for_daemon(console, run, sleep, wait_seconds)
    except SetupError:
        console.print(
            "[yellow]The daemon may be up but unreachable as your user — a read-only "
            "`sudo docker info` can tell a stopped daemon from a permissions problem.[/yellow]"
        )
        if (
            _consented(
                console, run, ["sudo", "docker", "info"], assume_yes=assume_yes, input_fn=input_fn
            )
            != "ok"
        ):
            raise
        console.print(
            "[yellow]The daemon IS running — your user just can't reach its socket "
            "yet (docker group membership).[/yellow]"
        )
        _consented(
            console,
            run,
            ["sudo", "usermod", "-aG", "docker", getpass.getuser()],
            assume_yes=assume_yes,
            input_fn=input_fn,
            sudo_note=True,
        )
        raise SetupError(
            "EMB-21",
            "Docker is running, but your user isn't in the docker group yet.",
            "Log out and back in (or run `newgrp docker`), then re-run the installer.",
        )


def _verify_compose(run):
    """Refuse success while `docker compose` (v2) is missing — later steps all use it."""
    if run(["docker", "compose", "version"]).rc != 0:
        raise SetupError(
            "EMB-23",
            "Docker is up, but the `docker compose` (v2) plugin is missing.",
            "Install the compose plugin (docker-compose-plugin from your package "
            "manager, or https://docs.docker.com/compose/install/), then re-run.",
        )


def _consented(console, run, cmd, *, assume_yes, input_fn, sudo_note=False):
    """Show the exact command, ask, run it streamed.

    Returns:
        "declined" | "ok" | "failed" — callers MUST keep declined (EMB-20) and failed
        (EMB-23/22) apart; conflating them misreports package problems as user refusal.
    """
    note = " [yellow](needs sudo)[/yellow]" if sudo_note else ""
    console.print(f"  I'd run: [bold]{' '.join(cmd)}[/bold]{note}")
    if not ui.confirm(console, "Run it?", default=False, assume_yes=assume_yes, input_fn=input_fn):
        return "declined"
    return "ok" if run(cmd, stream=True).rc == 0 else "failed"


def _manual_start_wait(console, run, assume_yes, input_fn, sleep, wait_seconds):
    """Advise how to start an installed-but-down daemon, then poll for it."""
    console.print(
        "[yellow]Docker is installed but the daemon isn't answering.[/yellow] "
        "Start it (open OrbStack/Docker Desktop, `colima start`, or "
        "`sudo systemctl start docker`) — I'll wait. Press Enter when you've kicked it."
    )
    if not assume_yes:
        input_fn()
    _wait_for_daemon(console, run, sleep, wait_seconds)


def _macos_ladder(console, run, *, assume_yes, which, input_fn, sleep, wait_seconds):
    if which("brew") is None:
        raise SetupError(
            "EMB-22",
            "No container runtime, and no Homebrew to install one with.",
            "Install Homebrew (https://brew.sh) and re-run — or install OrbStack, Colima, "
            "or Docker Desktop yourself, then re-run.",
        )
    choice = ui.choose(
        console,
        "No container runtime found. Which one shall we set up?",
        [
            ("o", "OrbStack — fast, native, free for personal use (I can install this one)"),
            ("c", "Colima — open source (manual: two commands + one config line)"),
            ("d", "Docker Desktop — the classic (manual download)"),
            ("n", "none of these — I'll handle it myself"),
        ],
        default_key="o",
        assume_yes=assume_yes,
        input_fn=input_fn,
    )
    if choice == "o":
        outcome = _consented(
            console,
            run,
            ["brew", "install", "--cask", "orbstack"],
            assume_yes=assume_yes,
            input_fn=input_fn,
        )
        if outcome == "ok":
            console.print(
                "Open OrbStack once so it can finish its setup — I'll keep polling. "
                "Press Enter to start the clock."
            )
            if not assume_yes:
                input_fn()
            _wait_for_daemon(console, run, sleep, wait_seconds)
            return
        if outcome == "failed":
            raise SetupError(
                "EMB-22",
                "The OrbStack install failed (brew's error is above).",
                "Fix the brew error or install a runtime manually, then re-run the installer.",
            )
    elif choice == "c":
        # Guide-only: Homebrew's docker-compose formula does NOT wire itself in as the
        # `docker compose` v2 plugin (it only prints a cliPluginsExtraDirs caveat), and
        # this installer doesn't edit ~/.docker/config.json on the user's behalf.
        console.print(
            "Colima needs three manual steps:\n"
            "  [bold]brew install colima docker docker-compose[/bold]\n"
            "  add Homebrew's cliPluginsExtraDirs caveat to [bold]~/.docker/config.json[/bold]\n"
            "  [bold]colima start[/bold]"
        )
        raise SetupError(
            "EMB-22",
            "Colima selected — it's a short manual install.",
            "Run the three steps above, then re-run the installer.",
        )
    elif choice == "d":
        raise SetupError(
            "EMB-22",
            "Docker Desktop is a GUI download — I can't install it for you.",
            "Get it at https://www.docker.com/products/docker-desktop/, start it, re-run me.",
        )
    raise SetupError(
        "EMB-20",
        "No container runtime, and every offer was declined. That's cool, man.",
        "Install OrbStack, Colima, or Docker Desktop yourself, then re-run the installer.",
    )


def _linux_install_cmd(os_release_text):
    """Map /etc/os-release to an install argv, or None for unknown distros.

    The file's content only SELECTS a hardcoded argv — nothing from it is interpolated
    into the command, so a hostile os-release has no injection surface.
    """
    fields = dict(
        line.split("=", 1) for line in os_release_text.strip().splitlines() if "=" in line
    )
    distro = fields.get("ID", "").strip('"')
    like = fields.get("ID_LIKE", "").strip('"')
    family = f"{distro} {like}"
    if any(d in family for d in ("ubuntu", "debian")):
        return ["sudo", "apt-get", "install", "-y", "docker.io", "docker-compose-v2"]
    if any(d in family for d in ("fedora", "rhel", "centos")):
        return ["sudo", "dnf", "install", "-y", "docker", "docker-compose"]
    return None


def _linux_ladder(
    console, run, *, platform, assume_yes, which, os_release_text, input_fn, sleep, wait_seconds
):
    if platform == "wsl2":
        console.print(
            "[dim]WSL2 detected. Easiest path: Docker Desktop on Windows with WSL "
            "integration enabled. Or install docker inside this distro:[/dim]"
        )
    if os_release_text is None:
        try:
            os_release_text = Path("/etc/os-release").read_text()
        except OSError:
            os_release_text = ""
    install_cmd = _linux_install_cmd(os_release_text)
    if install_cmd is None:
        raise SetupError(
            "EMB-23",
            "I don't recognize this Linux distribution, so I won't guess at package managers.",
            "Install Docker Engine per https://docs.docker.com/engine/install/ and re-run.",
        )
    outcome = _consented(
        console, run, install_cmd, assume_yes=assume_yes, input_fn=input_fn, sudo_note=True
    )
    if outcome == "declined":
        raise SetupError(
            "EMB-20",
            "Docker isn't installed, and the install offer was declined.",
            "Install Docker Engine (https://docs.docker.com/engine/install/), then re-run.",
        )
    if outcome == "failed":
        raise SetupError(
            "EMB-23",
            "The package install failed — it may not exist on this release "
            "(the package manager's error is in the output above).",
            "Install Docker Engine + the compose plugin per "
            "https://docs.docker.com/engine/install/, then re-run the installer.",
        )
    if (
        _consented(
            console,
            run,
            _start_cmd(which),
            assume_yes=assume_yes,
            input_fn=input_fn,
            sudo_note=True,
        )
        != "ok"
    ):
        console.print(
            "[yellow]Start the daemon yourself[/yellow] (`sudo systemctl start docker` or "
            "`sudo service docker start`) — I'll wait. Press Enter when you've kicked it."
        )
        if not assume_yes:
            input_fn()
    _wait_with_group_check(
        console,
        run,
        assume_yes=assume_yes,
        input_fn=input_fn,
        sleep=sleep,
        wait_seconds=wait_seconds,
    )


def ensure_docker(
    console,
    run,
    *,
    platform,
    assume_yes,
    which=None,
    os_release_text=None,
    input_fn=input,
    sleep=None,
    wait_seconds=120,
):
    """Return once `docker info` AND `docker compose version` succeed; else EMB-20/21/22/23.

    Args:
        console: rich Console.
        run: runner.run-compatible callable (a missing binary comes back as rc 127,
            never as an exception — see installer/runner.py).
        platform: "macos" | "wsl2" | "linux" (from detect_platform).
        assume_yes: unattended mode — consent prompts auto-decline (installs need a human).
        which: shutil.which-compatible; injected in tests.
        os_release_text: /etc/os-release contents; injected in tests.
        input_fn: prompt reader.
        sleep / wait_seconds: daemon-poll knobs.
    """
    import shutil

    which = shutil.which if which is None else which
    sleep = time.sleep if sleep is None else sleep

    if not _docker_ok(run):
        if which("docker") is not None:
            # Installed but the daemon is down.
            if platform in ("linux", "wsl2"):
                outcome = _consented(
                    console,
                    run,
                    _start_cmd(which),
                    assume_yes=assume_yes,
                    input_fn=input_fn,
                    sudo_note=True,
                )
                if outcome == "ok":
                    _wait_with_group_check(
                        console,
                        run,
                        assume_yes=assume_yes,
                        input_fn=input_fn,
                        sleep=sleep,
                        wait_seconds=wait_seconds,
                    )
                else:
                    _manual_start_wait(console, run, assume_yes, input_fn, sleep, wait_seconds)
            else:
                _manual_start_wait(console, run, assume_yes, input_fn, sleep, wait_seconds)
        elif assume_yes:
            raise SetupError(
                "EMB-20",
                "No container runtime found, and unattended mode can't consent to installing one.",
                "Run interactively (without EMBEDDINGTON_YES), or install "
                "Docker/OrbStack/Colima first.",
            )
        elif platform == "macos":
            _macos_ladder(
                console,
                run,
                assume_yes=assume_yes,
                which=which,
                input_fn=input_fn,
                sleep=sleep,
                wait_seconds=wait_seconds,
            )
        else:
            _linux_ladder(
                console,
                run,
                platform=platform,
                assume_yes=assume_yes,
                which=which,
                os_release_text=os_release_text,
                input_fn=input_fn,
                sleep=sleep,
                wait_seconds=wait_seconds,
            )
    _verify_compose(run)
