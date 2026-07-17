#!/usr/bin/env bash
# embeddington one-line installer — the thin bootstrap.
#
#   curl -fsSL https://raw.githubusercontent.com/whiffernet/embeddington/main/install.sh | bash
#
# Deliberately boring: check prerequisites, clone, venv, pip install, then exec the
# Rich TUI (embeddington-setup), which owns everything interesting. Errors print a
# stable EMB-1x code; the README's troubleshooting table is keyed on them.
# Unattended mode: EMBEDDINGTON_YES=1. Install dir: EMBEDDINGTON_INSTALL_DIR
# (default ~/embeddington). Clone source override (CI): EMBEDDINGTON_CLONE_URL.
set -euo pipefail

CLONE_URL="${EMBEDDINGTON_CLONE_URL:-https://github.com/whiffernet/embeddington.git}"
DEFAULT_DIR="${EMBEDDINGTON_INSTALL_DIR:-$HOME/embeddington}"
YES="${EMBEDDINGTON_YES:-}"
ANCHOR="https://github.com/whiffernet/embeddington#"

say()  { printf '%s\n' "$*"; }
fail() { # fail EMB-nn "friendly" "fix"
  printf '\n  ✗  %s\n\n     Fix: %s\n\n     [%s]  %semb-%s\n' \
    "$2" "$3" "$1" "$ANCHOR" "${1#EMB-}" >&2
  exit 1
}

# --- TTY: prompts must come from the terminal, not the curl pipe -------------
# Attempt a real open: permission bits ([ -r /dev/tty ]) pass in containers and
# daemons that have no controlling terminal, where any actual open fails (ENXIO)
# and a later `< /dev/tty` redirect would kill the script.
INTERACTIVE=0
if { : < /dev/tty; } 2>/dev/null; then INTERACTIVE=1; fi
if [ "$INTERACTIVE" -eq 0 ] && [ -z "$YES" ]; then
  fail EMB-10 "No interactive terminal, and EMBEDDINGTON_YES isn't set." \
    "Run from a real terminal, or set EMBEDDINGTON_YES=1 for an unattended install."
fi
# NOTE: this script must stay bash-3.2 clean (stock macOS) — in particular, never
# expand a possibly-empty array with "${ARR[@]}" under set -u (aborts before bash 4.4).

# --- Prerequisites ------------------------------------------------------------
command -v git >/dev/null 2>&1 || fail EMB-11 "git is not installed." \
  "Install git (xcode-select --install on macOS; apt/dnf install git on Linux), re-run."

PY=""
for candidate in python3.13 python3.12 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)'; then
      PY="$candidate"; break
    fi
  fi
done
[ -n "$PY" ] || fail EMB-12 "Python 3.12+ not found." \
  "Install Python 3.12 or newer (python.org, brew install python@3.12, or your distro), re-run."

if ! git ls-remote --heads "$CLONE_URL" >/dev/null 2>&1; then
  fail EMB-13 "Can't reach the embeddington repository ($CLONE_URL)." \
    "Check your connection / proxy, then re-run."
fi

# --- Install location ----------------------------------------------------------
DIR="$DEFAULT_DIR"
if [ "$INTERACTIVE" -eq 1 ] && [ -z "$YES" ] && [ -z "${EMBEDDINGTON_INSTALL_DIR:-}" ]; then
  printf 'Where should embeddington live? [%s] ' "$DEFAULT_DIR" > /dev/tty
  read -r answer < /dev/tty || answer=""
  [ -n "$answer" ] && DIR="$answer"
fi
case "$DIR" in "~"*) DIR="$HOME${DIR#\~}";; esac

# --- Clone or refresh ----------------------------------------------------------
if [ -d "$DIR/.git" ]; then
  say "Existing install found at $DIR — refreshing (the wizard will offer update/repair/uninstall)."
  git -C "$DIR" pull --ff-only || say "warning: git pull failed (local changes?) — continuing."
elif [ -e "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
  fail EMB-16 "$DIR already exists, isn't empty, and isn't an embeddington clone." \
    "Pick a different location (EMBEDDINGTON_INSTALL_DIR=...), or move that directory aside."
else
  say "Cloning into $DIR ..."
  git clone --depth 1 "$CLONE_URL" "$DIR"
fi
cd "$DIR"

# --- Venv + install -------------------------------------------------------------
say "Setting up the Python environment (a minute or two) ..."
if ! { "$PY" -m venv .venv \
       && .venv/bin/pip install --quiet --upgrade pip \
       && .venv/bin/pip install --quiet -e ".[setup]"; } >> install.log 2>&1; then
  tail -n 20 install.log >&2
  # Debian/Ubuntu/WSL2 ship python without the venv module — name the real fix.
  if grep -qi "ensurepip" install.log; then
    fail EMB-14 "Python can't create a venv here — the python3-venv package is missing." \
      "sudo apt install python3-venv   (or python3.12-venv), then re-run the installer."
  fi
  fail EMB-14 "Python environment setup failed (last lines above; full log: $DIR/install.log)." \
    "Fix the pip error shown, then re-run the installer."
fi

# pip exits 0 even when the [setup] extra doesn't exist (stale clone after a failed
# pull) — verify the wizard actually landed before exec'ing into nothing.
if [ ! -x .venv/bin/embeddington-setup ]; then
  fail EMB-14 "The setup wizard wasn't installed — your clone is probably outdated (did the git pull above fail?)." \
    "cd $DIR && git stash && git pull --ff-only, then re-run the installer."
fi

# --- Handoff to the wizard ------------------------------------------------------
# ${YES:+--yes} expands to nothing when unset — bash-3.2-safe (an empty array
# expansion under set -u would abort on stock macOS). Unattended mode never reads
# a prompt, so it never gets the /dev/tty redirect — headless boxes have none.
if [ -z "$YES" ] && [ "$INTERACTIVE" -eq 1 ]; then
  exec .venv/bin/embeddington-setup < /dev/tty
else
  exec .venv/bin/embeddington-setup ${YES:+--yes}
fi
