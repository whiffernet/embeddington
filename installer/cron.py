"""All cron/crontab logic for the installer, in one place.

Consolidated here so the install step (install_cron) and the uninstall step
(strip_cron_lines) share one marker and one line-builder without reaching into each
other's module. cli.py and uninstall.py import these names.
"""

import os
import tempfile

from installer import ui
from installer.errors import SetupError

CRON_MARKER = "embeddington-consume"


def cron_line(repo_root):
    """The nightly-update crontab line for THIS install location.

    [CRITIC] Built from the actual repo_root, never hardcoded to $HOME/embeddington —
    the installer honors EMBEDDINGTON_INSTALL_DIR and an interactive location prompt,
    and a receipt that prints a cron line for the wrong directory fails silently every
    night.

    Args:
        repo_root: the clone root the cron job cd's into.

    Returns:
        A single crontab line (no trailing newline).
    """
    return (
        f"0 6 * * * cd {repo_root} && set -a && . consumer/.env && set +a && "
        f".venv/bin/embeddington-consume update >> $HOME/embeddington-update.log 2>&1"
    )


def strip_cron_lines(crontab_text):
    """Return the crontab minus every line mentioning embeddington-consume.

    Args:
        crontab_text: the full crontab body.

    Returns:
        The body with every embeddington-consume line removed (trailing newline
        preserved iff the input had one).
    """
    return "\n".join(line for line in crontab_text.splitlines() if CRON_MARKER not in line) + (
        "\n" if crontab_text.endswith("\n") else ""
    )


def cron_daemon_running(run):
    """Best-effort check for a running cron daemon. Never raises.

    Tries ``pgrep -x cron`` then ``pgrep -x crond``; if pgrep is absent (rc 127) or both
    miss, tries ``systemctl is-active --quiet`` for each. Anything inconclusive returns
    False, which only drives an advisory warning — it never blocks installation.

    Args:
        run: runner.run-compatible callable (a missing binary comes back as rc 127).

    Returns:
        True only when a cron daemon is positively detected.
    """
    for name in ("cron", "crond"):
        if run(["pgrep", "-x", name]).rc == 0:
            return True
    for name in ("cron", "crond"):
        if run(["systemctl", "is-active", "--quiet", name]).rc == 0:
            return True
    return False


def install_cron(console, run, repo_root, *, assume_yes, input_fn=input):
    """Offer to install the daily-update cron job; return the outcome.

    Never raises: every failure degrades to a printed manual line. Unattended runs
    (assume_yes) never read a prompt or touch the crontab.

    Args:
        console: rich Console.
        run: runner.run-compatible callable.
        repo_root: the clone root the cron job cd's into.
        assume_yes: unattended — skip the whole step (no prompt, no crontab access).
        input_fn: prompt reader.

    Returns:
        "installed" | "installed-cron-down" | "declined" | "skipped-unattended" |
        "no-crontab".
    """
    if assume_yes:
        return "skipped-unattended"
    if not ui.confirm(
        console, "Set up daily auto-updates at 06:00?", default=True, input_fn=input_fn
    ):
        return "declined"

    res = run(["crontab", "-l"])
    if res.rc == 127:  # no crontab binary at all
        return "no-crontab"
    existing = res.out if res.rc == 0 else ""  # non-zero, non-127 = "no crontab yet"

    body = strip_cron_lines(existing).rstrip("\n")
    new_tab = (body + "\n" if body else "") + cron_line(repo_root) + "\n"

    # Write to a temp file, install it, and always clean the temp file up (a per-run
    # crontab tempfile left in /tmp is untidy). mkstemp + finally, not NamedTemporaryFile
    # (delete=False), so nothing lingers.
    fd, path = tempfile.mkstemp(suffix=".cron", prefix="embeddington-")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(new_tab)
        if run(["crontab", path]).rc != 0:
            ui.show_error(
                console,
                SetupError(
                    "EMB-62",
                    "Couldn't write the crontab, so auto-updates weren't enabled.",
                    "Add the line yourself with `crontab -e` — the receipt prints it. On "
                    "macOS a write failure usually means the Terminal app needs Full Disk "
                    "Access (System Settings → Privacy & Security).",
                ),
            )
            return "declined"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    return "installed" if cron_daemon_running(run) else "installed-cron-down"
