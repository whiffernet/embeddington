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


# --- the bootstrap journal: one log file, not two (#128) --------------------

_RUNLOG = _ROOT / "installer" / "runlog.py"


def _constants(*names):
    """The named top-level assignments, verbatim from install.sh.

    _extract() only lifts functions, and journal_append reads two module-level constants
    — unbound under `set -u`. Taking them from the real file keeps the test honest rather
    than restating values that could drift.
    """
    text = _INSTALL_SH.read_text()
    lines = []
    for name in names:
        found = re.search(rf"^{name}=.*$", text, re.M)
        assert found, f"install.sh no longer defines {name}"
        lines.append(found.group(0))
    return "\n".join(lines)


def _journal(script, env=None):
    """Run `script` with the journal functions from install.sh in scope."""
    return _bash(
        ["state_dir", "redact_secrets", "journal_append"],
        script,
        env=env,
        prelude=_constants("JOURNAL_MARKER", "JOURNAL_MAX_BYTES"),
    )


def _env(state_dir, **extra):
    return {
        "HOME": "/home/someone",
        "PATH": "/usr/bin:/bin",
        "EMBEDDINGTON_HOME": str(state_dir),
        **extra,
    }


def test_session_marker_matches_the_python_journal():
    """install.sh appends to the same file installer/runlog.py writes, so the two must
    agree on the session header — otherwise a shared log stops reading as one timeline.
    Same class of duplication as the state-dir ladder above, pinned the same way."""
    from installer import runlog

    marker = re.search(r'^JOURNAL_MARKER="([^"]+)"', _INSTALL_SH.read_text(), re.M)
    assert marker, "install.sh no longer defines JOURNAL_MARKER"
    assert marker.group(1) == runlog.SESSION_MARKER


def test_journal_append_writes_into_the_state_dir(tmp_path):
    src = tmp_path / "boot.log"
    src.write_text("Successfully installed embeddington\n")
    out = _journal(
        f'journal_append "{src}" "python environment bootstrap"; cat "$(state_dir)/run.log"',
        env=_env(tmp_path / "state"),
    )
    assert "Successfully installed embeddington" in out
    assert "python environment bootstrap" in out


def test_journal_append_redacts_credentials_embedded_in_urls(tmp_path):
    """[CRITIC] run.log is documented as safe to share and users are told to send it. pip
    echoes its index URL on failure, and a corporate PIP_INDEX_URL routinely carries
    basic-auth — so this content cannot be appended raw. Must happen shell-side: the
    wizard's Python redaction does not exist yet at this point in the run."""
    src = tmp_path / "boot.log"
    src.write_text(
        "Looking in indexes: https://svc-account:hunter2@pypi.internal.example/simple\n"
        "ERROR: Could not find a version that satisfies the requirement\n"
    )
    out = _journal(
        f'journal_append "{src}" "bootstrap"; cat "$(state_dir)/run.log"',
        env=_env(tmp_path / "state"),
    )
    assert "hunter2" not in out
    assert "svc-account" not in out
    assert "REDACTED" in out
    assert "pypi.internal.example" in out  # the host still has to be diagnosable


def test_journal_append_never_aborts_the_install(tmp_path):
    """install.sh runs under `set -euo pipefail`. An unwritable state dir must cost the
    journal, never the install — runlog.py's first rule, honoured on the shell side."""
    blocker = tmp_path / "state"
    blocker.write_text("i am a file, not a directory")
    src = tmp_path / "boot.log"
    src.write_text("whatever\n")
    assert _journal(f'journal_append "{src}" "x"; echo SURVIVED', env=_env(blocker)) == "SURVIVED"


def test_journal_append_bounds_what_it_writes(tmp_path):
    """A pathological pip failure must not evict the journal's history on its own."""
    src = tmp_path / "boot.log"
    src.write_text("".join(f"noise line {i}\n" for i in range(200_000)))
    out = _journal(
        f'journal_append "{src}" "bootstrap"; wc -c < "$(state_dir)/run.log"',
        env=_env(tmp_path / "state"),
    )
    assert int(out) < 400_000, out


def test_the_clone_no_longer_collects_its_own_log_file():
    """The whole point of #128: one file to ask a user for, in a place that survives a
    re-clone. A stray `>> install.log` would quietly restore the second one.

    Comments are stripped first — the block explaining why the file went away naturally
    names it, and an assertion that forbids discussing the change is not a useful one.
    """
    code = "\n".join(
        line for line in _INSTALL_SH.read_text().splitlines() if not line.lstrip().startswith("#")
    )
    assert "install.log" not in code


def test_the_ensurepip_probe_reads_only_this_run(tmp_path):
    """Pre-existing bug this fixes: the old log was appended to across runs with `>>`, so
    the ensurepip grep matched a venv failure the user had already fixed months ago and
    misdiagnosed an unrelated pip error as a missing python3-venv."""
    text = _INSTALL_SH.read_text()
    assert re.search(r'grep -qi "ensurepip" "\$BOOTSTRAP_LOG"', text), text[-2000:]
    assert re.search(r'\} > "\$BOOTSTRAP_LOG" 2>&1', text), "bootstrap log must be truncated"
