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
YES="${EMBEDDINGTON_YES:-}"
ANCHOR="https://github.com/whiffernet/embeddington#"

say()  { printf '%s\n' "$*"; }
fail() { # fail EMB-nn "friendly" "fix"
  printf '\n  ✗  %s\n\n     Fix: %s\n\n     [%s]  %semb-%s\n' \
    "$2" "$3" "$1" "$ANCHOR" "${1#EMB-}" >&2
  exit 1
}

# --- Banner ---------------------------------------------------------------------
# Printed FIRST, before any check runs. With `curl | bash` the whole script is
# downloaded before execution, so this appears the instant the command is accepted —
# previously the first thing a user saw was a bare location prompt, and the logo only
# arrived after the venv build, which is the slowest step in the run.
#
# The art is duplicated from installer/ui.py because install.sh runs before the clone
# exists and has nothing to read it from. tests/test_install_sh.py pins the two copies
# against each other; a quoted heredoc keeps the backslashes and the backtick in row 3
# literal.
QUOTES=(
  "The Dude abides."
  "Careful, man, there's a beverage here!"
  "New information has come to light, man."
  "This is a very complicated case. A lotta ins, a lotta outs."
  "Yeah, well, that's just, like, your opinion, man."
)

show_banner() {
  cyan=""; dim=""; off=""
  if [ -t 1 ]; then cyan=$'\033[1;36m'; dim=$'\033[2m'; off=$'\033[0m'; fi
  printf '%s' "$cyan"
  cat <<'EMB_BANNER'

  ___ __  __ ___ ___ ___  ___ ___ _  _  ___ _____ ___  _  _
 | __|  \/  | _ ) __|   \|   \_ _| \| |/ __|_   _/ _ \| \| |
 | _|| |\/| | _ \ _|| |) | |) | || .` | (_ | | || (_) | .` |
 |___|_|  |_|___/___|___/|___/___|_|\_|\___| |_| \___/|_|\_|
EMB_BANNER
  printf '%s\n' "$off"
  printf '  embeddington — the knowledge graph that ties the room together\n'
  # Rotate on the PID, exactly as the wizard does. The array is a non-empty literal, so
  # indexing it is safe under `set -u` on stock macOS bash 3.2.
  printf '  %s"%s"%s\n\n' "$dim" "${QUOTES[$(( $$ % ${#QUOTES[@]} ))]}" "$off"
}

show_banner

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
# Announced, because `git ls-remote` below can sit silently for a second or two on a slow
# link and dead air after a piped command reads as a hang.
say "  Checking prerequisites ..."
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

# --- Where an existing install lives --------------------------------------------
# Mirrors consumer/state_paths.resolve_state_dir. Two implementations of one ladder is
# a drift risk, so tests/test_install_sh.py runs both and compares.
state_dir() {
  if [ -n "${EMBEDDINGTON_HOME:-}" ]; then printf '%s' "$EMBEDDINGTON_HOME"; return; fi
  if [ -n "${XDG_DATA_HOME:-}" ]; then printf '%s/embeddington' "$XDG_DATA_HOME"; return; fi
  printf '%s/.local/share/embeddington' "$HOME"
}

# --- The bootstrap journal ------------------------------------------------------
# This script's output used to go to <clone>/install.log while everything after it went
# to the wizard's own <state dir>/run.log (installer/runlog.py). Two files, in two
# places, written in two languages: a re-clone destroyed the first, EMB-14 named only
# the first, and asking a user for "the log" meant asking twice and usually getting the
# wrong one. There is now one file, and it lives where the nightly job writes and where
# a re-clone cannot reach it.
#
# Must match installer/runlog.py's SESSION_MARKER — tests/test_install_sh.py pins the
# two against each other, exactly as it does for the state_dir ladder above.
JOURNAL_MARKER="=== embeddington run"
JOURNAL_MAX_BYTES=200000

# Strip credentials embedded in URLs.
#
# [CRITIC] run.log is documented as safe to share and users are told to send it when
# reporting a problem. pip echoes the index URL it was using when a resolve fails, and a
# corporate PIP_INDEX_URL / PIP_EXTRA_INDEX_URL routinely carries basic-auth — so this
# content cannot be appended raw. It has to happen here rather than in runlog.py: the
# wizard's Python does not exist yet at this point in the run. `sed -E` is the portable
# spelling across BSD (macOS) and GNU.
redact_secrets() {
  sed -E 's#([a-zA-Z][a-zA-Z0-9+.-]*://)[^/@[:space:]]+:[^/@[:space:]]+@#\1***REDACTED***@#g'
}

# journal_append <file> <label> — record a file's tail in the run log.
#
# Never fails the run. This script is `set -euo pipefail`, so an unwritable state dir
# would otherwise abort an install over a log nobody had asked for; losing the journal is
# acceptable, losing the install is not (installer/runlog.py holds the same rule).
journal_append() {
  _jdir="$(state_dir)"
  mkdir -p "$_jdir" 2>/dev/null || return 0
  {
    printf '\n%s %s ===\n--- %s ---\n' \
      "$JOURNAL_MARKER" "$(date +%Y-%m-%dT%H:%M:%S%z)" "$2"
    tail -c "$JOURNAL_MAX_BYTES" "$1" | redact_secrets
  } >> "$_jdir/run.log" 2>/dev/null || return 0
}

# The clone root of an existing install, or empty. Never fails the run.
recorded_install_dir() {
  pointer="$(state_dir)/install_path"
  if [ -r "$pointer" ]; then
    recorded="$(head -n 1 "$pointer" 2>/dev/null | tr -d '\r\n')"
    if [ -n "$recorded" ] && [ -d "$recorded/.git" ]; then printf '%s' "$recorded"; return; fi
  fi
  # Installs predating the pointer: the nightly job's own `cd <path>` knows where it is.
  cron_line="$(crontab -l 2>/dev/null \
    | grep -E 'embeddington-(setup|consume)' | head -n 1)"
  # Parameter expansion rather than sed: the scheduled line is `… cd <path> && …`, and a
  # path may contain spaces (an iCloud Drive install lives under "Mobile Documents"), so
  # the field cannot be cut at the first space. `#* cd ` trims to the FIRST " cd " and
  # `%% &&*` keeps everything before the FIRST " &&" — spaces in between survive.
  from_cron=""
  case "$cron_line" in
    *" cd "*)
      from_cron="${cron_line#* cd }"
      from_cron="${from_cron%% &&*}"
      ;;
  esac
  if [ -n "$from_cron" ] && [ -d "$from_cron/.git" ]; then printf '%s' "$from_cron"; return; fi
  printf ''
}

# macOS blocks background jobs (cron, launchd) from reading these folders without an
# explicit Full Disk Access grant, so a clone here updates by hand forever while the
# nightly job silently does nothing. Case-folded: the default macOS filesystem is
# case-insensitive, so ~/documents is the same folder as ~/Documents.
is_protected_macos_path() {
  [ "$(uname -s)" = "Darwin" ] || return 1
  _lower="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  _home="$(printf '%s' "$HOME" | tr '[:upper:]' '[:lower:]')"
  case "$_lower" in
    "$_home"/documents|"$_home"/documents/*) return 0 ;;
    "$_home"/desktop|"$_home"/desktop/*) return 0 ;;
    "$_home"/downloads|"$_home"/downloads/*) return 0 ;;
    "$_home"/library/mobile\ documents|"$_home"/library/mobile\ documents/*) return 0 ;;
  esac
  return 1
}

# --- Install location ----------------------------------------------------------
DEFAULT_NOTE=""
if [ -n "${EMBEDDINGTON_INSTALL_DIR:-}" ]; then
  DEFAULT_DIR="$EMBEDDINGTON_INSTALL_DIR"
else
  RECORDED="$(recorded_install_dir)"
  if [ -n "$RECORDED" ]; then
    DEFAULT_DIR="$RECORDED"
    DEFAULT_NOTE=" — your existing install"
  else
    DEFAULT_DIR="$HOME/embeddington"
  fi
fi

DIR="$DEFAULT_DIR"
if [ "$INTERACTIVE" -eq 1 ] && [ -z "$YES" ] && [ -z "${EMBEDDINGTON_INSTALL_DIR:-}" ]; then
  printf 'Where should embeddington live? [%s%s] ' "$DEFAULT_DIR" "$DEFAULT_NOTE" > /dev/tty
  read -r answer < /dev/tty || answer=""
  [ -n "$answer" ] && DIR="$answer"
fi
case "$DIR" in "~"*) DIR="$HOME${DIR#\~}";; esac

# --- macOS: warn before a clone lands somewhere background jobs can't reach -------
if is_protected_macos_path "$DIR"; then
  if [ -d "$DIR/.git" ]; then
    # Already installed here. Offering to clone elsewhere would make a SECOND install,
    # which is worse than the problem — so explain and let them decide.
    say ""
    say "  !  $DIR is a macOS-protected folder."
    say "     Background jobs can't read it, so the nightly auto-update never runs — you"
    say "     will need to re-run this installer by hand to get updates. To fix it: move"
    say "     the clone somewhere like \$HOME/embeddington, or grant Full Disk Access to"
    say "     the program that runs the update (System Settings -> Privacy & Security)."
    say ""
  elif [ "$INTERACTIVE" -eq 1 ] && [ -z "$YES" ]; then
    say ""
    say "  !  macOS blocks background jobs from reading $DIR, so nightly auto-updates"
    say "     would silently never run from there."
    printf 'Install into %s/embeddington instead? [Y/n] ' "$HOME" > /dev/tty
    read -r relocate < /dev/tty || relocate=""
    case "$relocate" in
      [Nn]*) say "     Keeping $DIR — updates will need this installer run by hand." ;;
      *) DIR="$HOME/embeddington"; say "     Using $DIR." ;;
    esac
    say ""
  else
    say "warning: $DIR is a macOS-protected folder — nightly auto-updates will not run"
    say "         from there. Re-run this installer by hand to update, or reinstall to"
    say "         \$HOME/embeddington."
  fi
fi

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
# TRUNCATED per run, deliberately. The old log was appended to across runs, and the
# ensurepip probe below grepped the whole thing — so a venv failure the user had already
# fixed kept matching, and any later pip error was misdiagnosed as a missing
# python3-venv forever after. Scoping the probe to this run's output is the fix.
# The template is required: bare `mktemp` is a usage error on BSD (macOS).
BOOTSTRAP_LOG="$(mktemp "${TMPDIR:-/tmp}/embeddington-bootstrap.XXXXXX")"
trap 'rm -f "$BOOTSTRAP_LOG"' EXIT
if ! { "$PY" -m venv .venv \
       && .venv/bin/pip install --quiet --upgrade pip \
       && .venv/bin/pip install --quiet -e ".[setup]"; } > "$BOOTSTRAP_LOG" 2>&1; then
  journal_append "$BOOTSTRAP_LOG" "python environment bootstrap (failed)"
  tail -n 20 "$BOOTSTRAP_LOG" | redact_secrets >&2
  # Debian/Ubuntu/WSL2 ship python without the venv module — name the real fix.
  if grep -qi "ensurepip" "$BOOTSTRAP_LOG"; then
    fail EMB-14 "Python can't create a venv here — the python3-venv package is missing." \
      "sudo apt install python3-venv   (or python3.12-venv), then re-run the installer."
  fi
  fail EMB-14 "Python environment setup failed (last lines above; full log: $(state_dir)/run.log)." \
    "Fix the pip error shown, then re-run the installer."
fi
journal_append /dev/null "python environment bootstrap: ok"

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
# The wizard prints the same banner when run on its own; it must not repeat it here.
# Exported only into the exec'd process — a piped install leaves nothing behind in the
# user's interactive shell.
export EMBEDDINGTON_BANNER_SHOWN=1
# exec REPLACES this process, so the EXIT trap never runs — clean up by hand or the
# bootstrap temp file leaks on every successful install.
rm -f "$BOOTSTRAP_LOG"
trap - EXIT
if [ -z "$YES" ] && [ "$INTERACTIVE" -eq 1 ]; then
  exec .venv/bin/embeddington-setup < /dev/tty
else
  exec .venv/bin/embeddington-setup ${YES:+--yes}
fi
