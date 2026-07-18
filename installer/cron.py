"""All cron/crontab logic for the installer, in one place.

Consolidated here so the install step (install_cron) and the uninstall step
(strip_cron_lines) share one marker and one line-builder without reaching into each
other's module. cli.py and uninstall.py import these names.
"""

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
