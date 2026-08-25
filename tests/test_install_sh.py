"""Behaviour tests for install.sh's path logic, run as real bash.

install.sh is the one part of this project that runs before Python exists, so its logic is
otherwise untested — and it is also where the two defects this file guards used to live:
the bootstrap could not find an existing install, and it happily cloned into a macOS folder
that background jobs cannot read.

Each test extracts the function under test from the shipped script and runs it, so nothing
here can pass against a copy that has drifted from what users download.
"""

import re
import subprocess
from pathlib import Path

import pytest

from consumer import state_paths

_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _ROOT / "install.sh"


def _extract(name):
    """The named shell function, verbatim from install.sh."""
    body = re.search(rf"^{name}\(\) \{{.*?^\}}", _INSTALL_SH.read_text(), re.S | re.M)
    assert body, f"install.sh no longer defines {name}()"
    return body.group(0)


def _bash(functions, script, env=None, prelude=""):
    """Run `script` with the given install.sh functions in scope."""
    source = "\n".join(_extract(f) for f in functions)
    proc = subprocess.run(
        ["bash", "-c", f"set -eu\n{source}\n{prelude}\n{script}"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


# --- the state-dir ladder, implemented twice -------------------------------


@pytest.mark.parametrize(
    "env_extra",
    [
        {},
        {"XDG_DATA_HOME": "/xdg/data"},
        {"EMBEDDINGTON_HOME": "/explicit/state"},
        {"EMBEDDINGTON_HOME": "/explicit/state", "XDG_DATA_HOME": "/xdg/data"},
    ],
)
def test_bash_state_dir_matches_the_python_ladder(env_extra):
    """install.sh resolves the state directory in bash; consumer/state_paths.py resolves it
    in Python. Two implementations of one ladder drift silently — and a bootstrap looking
    in the wrong directory simply fails to find an install that is right there."""
    env = {"HOME": "/home/someone", "PATH": "/usr/bin:/bin", **env_extra}
    from_bash = _bash(["state_dir"], "state_dir", env=env)
    from_python = state_paths.resolve_state_dir(env, Path(env["HOME"]))
    assert from_bash == str(from_python)


# --- finding an existing install -------------------------------------------


def _fake_clone(tmp_path, name="clone"):
    clone = tmp_path / name
    (clone / ".git").mkdir(parents=True)
    return clone


def test_reads_the_pointer_the_wizard_wrote(tmp_path):
    clone = _fake_clone(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    (state / "install_path").write_text(f"{clone}\n")
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "EMBEDDINGTON_HOME": str(state)}
    assert _bash(["state_dir", "recorded_install_dir"], "recorded_install_dir", env=env) == str(
        clone
    )


def test_a_stale_pointer_is_ignored(tmp_path):
    """A clone that was deleted or moved must not become the default — the prompt would
    offer a path that no longer exists."""
    state = tmp_path / "state"
    state.mkdir()
    (state / "install_path").write_text(f"{tmp_path / 'gone'}\n")
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "EMBEDDINGTON_HOME": str(state)}
    assert _bash(["state_dir", "recorded_install_dir"], "recorded_install_dir", env=env) == ""


def test_falls_back_to_the_scheduler_entry(tmp_path):
    """Installs that predate the pointer have no breadcrumb — but the nightly job embeds
    the clone path in its own `cd`. This is what reaches users who are already stuck."""
    clone = _fake_clone(tmp_path)
    cron_line = (
        f"0 6 * * * cd {clone} && set -a && . consumer/.env && set +a && "
        ".venv/bin/embeddington-setup --yes >> $HOME/embeddington-update.log 2>&1"
    )
    env = {
        "HOME": str(tmp_path),
        "PATH": "/usr/bin:/bin",
        "EMBEDDINGTON_HOME": str(tmp_path / "empty"),
    }
    out = _bash(
        ["state_dir", "recorded_install_dir"],
        "recorded_install_dir",
        env=env,
        prelude=f"crontab() {{ printf '%s\\n' '{cron_line}'; }}",
    )
    assert out == str(clone)


def test_legacy_scheduler_entries_are_understood(tmp_path):
    """Older installs scheduled the data-only command; their line still names the clone."""
    clone = _fake_clone(tmp_path)
    cron_line = f"0 6 * * * cd {clone} && .venv/bin/embeddington-consume update"
    env = {
        "HOME": str(tmp_path),
        "PATH": "/usr/bin:/bin",
        "EMBEDDINGTON_HOME": str(tmp_path / "empty"),
    }
    out = _bash(
        ["state_dir", "recorded_install_dir"],
        "recorded_install_dir",
        env=env,
        prelude=f"crontab() {{ printf '%s\\n' '{cron_line}'; }}",
    )
    assert out == str(clone)


def test_a_path_containing_spaces_is_recovered(tmp_path):
    """An iCloud Drive install lives under "Mobile Documents" — a directory with a space in
    its name. Cutting the field at the first space truncated it, so the clone was not found
    and the prompt fell back to the default, which is how a second clone gets made."""
    clone = tmp_path / "Mobile Documents" / "com~apple~CloudDocs" / "embeddington"
    (clone / ".git").mkdir(parents=True)
    cron_line = (
        f"0 6 * * * cd {clone} && set -a && . consumer/.env && set +a && "
        ".venv/bin/embeddington-setup --yes"
    )
    env = {
        "HOME": str(tmp_path),
        "PATH": "/usr/bin:/bin",
        "EMBEDDINGTON_HOME": str(tmp_path / "empty"),
    }
    out = _bash(
        ["state_dir", "recorded_install_dir"],
        "recorded_install_dir",
        env=env,
        prelude=f"crontab() {{ printf '%s\\n' '{cron_line}'; }}",
    )
    assert out == str(clone)


def test_an_unrelated_crontab_is_not_mistaken_for_an_install(tmp_path):
    env = {
        "HOME": str(tmp_path),
        "PATH": "/usr/bin:/bin",
        "EMBEDDINGTON_HOME": str(tmp_path / "empty"),
    }
    out = _bash(
        ["state_dir", "recorded_install_dir"],
        "recorded_install_dir",
        env=env,
        prelude="crontab() { printf '%s\\n' '0 3 * * * cd /srv/other && ./backup.sh'; }",
    )
    assert out == ""


def test_no_crontab_binary_is_survivable(tmp_path):
    """`crontab` is absent on plenty of machines; that must not abort the installer."""
    env = {
        "HOME": str(tmp_path),
        "PATH": "/usr/bin:/bin",
        "EMBEDDINGTON_HOME": str(tmp_path / "empty"),
    }
    out = _bash(
        ["state_dir", "recorded_install_dir"],
        "recorded_install_dir",
        env=env,
        prelude="crontab() { return 127; }",
    )
    assert out == ""


# --- the macOS folders background jobs cannot read -------------------------

_DARWIN = "uname() { printf 'Darwin\\n'; }"


def _protected(path, prelude=_DARWIN, home="/Users/someone"):
    env = {"HOME": home, "PATH": "/usr/bin:/bin"}
    out = _bash(
        ["is_protected_macos_path"],
        f'if is_protected_macos_path "{path}"; then echo yes; else echo no; fi',
        env=env,
        prelude=prelude,
    )
    return out == "yes"


@pytest.mark.parametrize(
    "path",
    [
        "/Users/someone/Documents/embeddington",
        "/Users/someone/Desktop/embeddington",
        "/Users/someone/Downloads/embeddington",
        "/Users/someone/Library/Mobile Documents/com~apple~CloudDocs/embeddington",
        "/Users/someone/documents/embeddington",  # the default macOS filesystem folds case
    ],
)
def test_protected_folders_are_recognised(path):
    assert _protected(path)


@pytest.mark.parametrize(
    "path",
    [
        "/Users/someone/embeddington",
        "/Users/someone/code/embeddington",
        "/opt/embeddington",
        "/Users/someone/Documentation/embeddington",  # a prefix, not the folder
    ],
)
def test_ordinary_folders_are_left_alone(path):
    assert not _protected(path)


def test_the_check_is_macos_only():
    """Linux has no equivalent restriction; warning there would be noise."""
    assert not _protected(
        "/home/someone/Documents/embeddington",
        prelude="uname() { printf 'Linux\\n'; }",
        home="/home/someone",
    )


# --- the banner exists in two languages (issue: logo arrived last) ---------


def _installer_banner_lines():
    """The ASCII art install.sh prints, from its quoted heredoc."""
    body = re.search(r"cat <<'EMB_BANNER'\n(.*?)\nEMB_BANNER", _INSTALL_SH.read_text(), re.S)
    assert body, "install.sh no longer prints a banner heredoc"
    return [ln for ln in body.group(1).splitlines() if ln.strip()]


def _installer_quotes():
    """The quote list install.sh rotates through."""
    body = re.search(r"^QUOTES=\(\n(.*?)^\)", _INSTALL_SH.read_text(), re.S | re.M)
    assert body, "install.sh no longer defines QUOTES"
    return [ln.strip().strip('"') for ln in body.group(1).splitlines() if ln.strip()]


def test_the_bootstrap_banner_matches_the_wizard_banner():
    """install.sh runs before the clone exists, so it cannot read the art from the repo and
    has to carry its own copy. Two copies of one thing are only safe while a test says they
    agree."""
    from installer import ui

    assert _installer_banner_lines() == [ln for ln in ui.BANNER.splitlines() if ln.strip()]


def test_the_bootstrap_quotes_match_the_wizard_quotes():
    from installer import ui

    assert _installer_quotes() == list(ui.QUOTES)


def test_the_bootstrap_hands_the_wizard_a_do_not_repeat_flag():
    """Without this the logo prints twice in a single piped install."""
    text = _INSTALL_SH.read_text()
    assert "export EMBEDDINGTON_BANNER_SHOWN=1" in text
    assert text.index("export EMBEDDINGTON_BANNER_SHOWN=1") < text.index("exec .venv/bin/")


def test_the_banner_is_the_first_thing_the_script_does():
    """The whole point: a piped install used to show a bare prompt first and the logo only
    after the venv build, which is the slowest step in the run."""
    text = _INSTALL_SH.read_text()
    assert text.index("\nshow_banner\n") < text.index("command -v git")
    assert text.index("\nshow_banner\n") < text.index("Checking prerequisites")
