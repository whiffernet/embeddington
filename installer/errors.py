"""Single registry of every EMB-nn error the installer can raise.

The code is the stable, greppable contract between a failing install and the README's
troubleshooting table: every registered code has a `#### EMB-nn` heading in the README
(pinned by tests/test_readme_pins.py). install.sh hard-codes only EMB-10..EMB-14, the
codes reachable before Python exists; everything else is raised from this module's
SetupError.
"""

ANCHOR_BASE = "https://github.com/whiffernet/embeddington#"

# code -> short internal description (the user-facing text lives at each raise site,
# where it can name the actual port/path/command involved).
CODES = {
    "EMB-10": "no interactive terminal and EMBEDDINGTON_YES not set",
    "EMB-11": "git is not installed",
    "EMB-12": "python 3.12+ not found",
    "EMB-13": "cannot reach github.com",
    "EMB-14": "venv/pip bootstrap failed",
    "EMB-15": "less than 3 GB of free disk",
    "EMB-16": "install dir exists but isn't an embeddington clone",
    "EMB-20": "no container runtime and every install offer was declined",
    "EMB-21": "docker daemon not reachable (down, start timed out, or socket permission denied)",
    "EMB-22": "a manual runtime install is required — re-run the installer after it",
    "EMB-23": "automatic docker install unavailable or failed on this distro — install manually",
    "EMB-24": "a required port is taken by something that is not embeddington",
    "EMB-31": "docker compose up failed",
    "EMB-32": "embed service did not come up",
    "EMB-33": "consumer/.env exists but has no usable ARANGO_ROOT_PASSWORD",
    "EMB-41": "download failed (network)",
    "EMB-42": "asset checksum mismatch",
    "EMB-43": "populated store with no cursor — refusing to re-restore (guard)",
    "EMB-44": "proof-of-life query returned zero",
    "EMB-45": "updater error (chain gap, schema version, ...)",
    "EMB-51": "MCP dependency install failed (Claude wiring is optional)",
    "EMB-61": "could not inspect store contents before volume deletion",
    "EMB-62": "crontab rewrite failed",
    "EMB-63": "clone self-delete handoff failed",
}


def anchor(code):
    """Return the README troubleshooting anchor URL for an EMB code."""
    return ANCHOR_BASE + code.lower()


class SetupError(Exception):
    """An installer failure with a stable code, a friendly line, and a concrete fix.

    Args:
        code: A key of CODES (e.g. "EMB-21"). Unregistered codes are a programming
            error and raise ValueError immediately.
        friendly: One plain-English sentence saying what happened.
        fix: The concrete next action for the user (a command, a URL, a choice).
    """

    def __init__(self, code, friendly, fix):
        if code not in CODES:
            raise ValueError(f"unregistered error code: {code}")
        self.code = code
        self.friendly = friendly
        self.fix = fix
        super().__init__(f"[{code}] {friendly}")
