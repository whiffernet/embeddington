"""Work out WHY docker isn't answering, so the ladder can say it instead of guessing.

The ladder's two failure branches used to report a guess: a docker that isn't on PATH
became "no container runtime found" (and an offer to install the runtime the user
already had), and any unreachable daemon became "start your daemon" — even when the
daemon was running fine and the client was dialing a socket left behind by an
uninstalled Docker Desktop.

Everything here is pure diagnosis: it senses, it reads, it never installs, starts,
or reconfigures anything. Two rules hold throughout:

  * We report where docker IS, never where it might be. A location is named only after
    it has been stat'd; nothing is ever suggested on the strength of being a likely spot.
  * Every probe degrades to "no detail" rather than raising. A diagnosis that crashes the
    installer is worse than the vague message it replaced ([CRITIC]: the `docker context`
    output format is not a stable contract, so it is parsed defensively on purpose).
"""

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# `timeout(1)`'s convention, reused so a timed-out probe is distinguishable from both
# success and the rc 127 that runner.run returns for a missing binary.
RC_TIMEOUT = 124

DEFAULT_INFO_TIMEOUT = 20

# Where a docker CLI lives when it is installed but not on this shell's PATH. Ordered
# most- to least-specific; "~" is resolved against the caller's home. This list only
# decides what to STAT — a path that isn't on disk is never mentioned to the user.
CANDIDATE_PATHS = (
    "~/.orbstack/bin/docker",  # OrbStack (PATH comes from a shell-init edit)
    "~/.rd/bin/docker",  # Rancher Desktop
    "~/.docker/bin/docker",  # recent Docker Desktop
    "/usr/local/bin/docker",  # Docker Desktop's symlink, Colima on Intel
    "/opt/homebrew/bin/docker",  # Homebrew on Apple silicon (Colima's client)
    "/Applications/Docker.app/Contents/Resources/bin/docker",  # Desktop, symlinks declined
    "/usr/bin/docker",  # distro packages
)

BIN_ENV_VAR = "EMBEDDINGTON_DOCKER_BIN"


@dataclass(frozen=True)
class DockerDiagnosis:
    """What could be established about a docker that did or didn't answer.

    Every field except `reachable` is best-effort: absent detail reads as None/empty,
    which the renderers below simply omit.
    """

    reachable: bool
    stderr_tail: str = ""
    timed_out: bool = False
    docker_host: str | None = None  # a DOCKER_HOST override, which beats the context
    active_context: str | None = None
    endpoint: str | None = None  # the socket/host the client actually dialed
    other_contexts: tuple[str, ...] = field(default_factory=tuple)


def stderr_tail(text, lines=3):
    """The last few non-blank lines — the part that names the actual failure."""
    kept = [line for line in (text or "").splitlines() if line.strip()]
    return "\n".join(kept[-lines:]).strip()


def find_docker_binary(*, exists, home, env):
    """Locate a docker CLI on disk, for when `shutil.which` came back empty.

    Args:
        exists: callable(Path) -> bool (production: Path.exists).
        home: the user's home directory, used to resolve the "~" candidates.
        env: environment mapping; an explicit BIN_ENV_VAR override is honored first.

    Returns:
        Path to a docker binary that exists, or None. None means "we found nothing",
        which callers must render as a question, never as a suggested location.
    """
    home = Path(home)
    override = (env or {}).get(BIN_ENV_VAR, "").strip()
    candidates = ([override] if override else []) + list(CANDIDATE_PATHS)
    for raw in candidates:
        # A stale override falls through to sensing rather than dead-ending the run.
        path = home / raw[2:] if raw.startswith("~/") else Path(raw)
        if exists(path):
            return path
    return None


def docker_info(run, *, timeout=DEFAULT_INFO_TIMEOUT):
    """`docker info`, bounded.

    [CRITIC] Unbounded, this is the call that makes a cold-starting or wedged daemon
    look like a frozen installer rather than a failing one. A timeout comes back as
    RC_TIMEOUT so the ladder can say "didn't answer in time" instead of misreporting
    it as a runtime that isn't installed.

    Args:
        run: runner.run-compatible callable.
        timeout: seconds to wait for the daemon.

    Returns:
        RunResult; rc is RC_TIMEOUT when the call timed out.
    """
    from installer.runner import RunResult

    try:
        return run(["docker", "info"], timeout=timeout)
    except subprocess.TimeoutExpired:
        return RunResult(RC_TIMEOUT, "", f"`docker info` did not answer within {timeout}s")


def _active_context(run):
    """(name, endpoint) from `docker context inspect`, or (None, None).

    Client-side, so it still answers when the daemon does not — which is the only
    situation this is called in.
    """
    res = run(["docker", "context", "inspect"])
    if res.rc != 0:
        return None, None
    try:
        entry = json.loads(res.out)[0]
        return entry.get("Name"), entry.get("Endpoints", {}).get("docker", {}).get("Host")
    except (ValueError, KeyError, IndexError, TypeError):
        return None, None


def _other_contexts(run, active):
    """The other configured context names, in the order docker lists them."""
    res = run(["docker", "context", "ls", "--format", "{{.Name}}"])
    if res.rc != 0:
        return ()
    return tuple(n for n in res.out.split() if n != active)


def diagnose(run, info, *, env=None):
    """Explain an info result, probing the docker client only when that can help.

    Args:
        run: runner.run-compatible callable.
        info: the RunResult from docker_info().
        env: environment mapping (a DOCKER_HOST override is reported when set).

    Returns:
        DockerDiagnosis. A reachable daemon or an absent binary (rc 127) short-circuits:
        there is nothing the context probes could add, and they would only spend two
        subprocess calls to fail.
    """
    if info.rc == 0:
        return DockerDiagnosis(reachable=True)
    tail = stderr_tail(info.err) or stderr_tail(info.out)
    if info.rc == 127:
        return DockerDiagnosis(reachable=False, stderr_tail=tail)

    host = ((env or {}).get("DOCKER_HOST") or "").strip() or None
    active, endpoint = _active_context(run)
    return DockerDiagnosis(
        reachable=False,
        stderr_tail=tail,
        timed_out=info.rc == RC_TIMEOUT,
        docker_host=host,
        active_context=active,
        endpoint=endpoint,
        other_contexts=_other_contexts(run, active),
    )


def short_detail(diag):
    """One line for the preflight/doctor `docker` row."""
    if diag.reachable:
        return "daemon reachable"
    if diag.timed_out:
        return "no answer in time"
    first = diag.stderr_tail.splitlines()[0] if diag.stderr_tail else ""
    if not first:
        return "not reachable"
    # Real daemon errors run long ("...check if the path is correct and if the daemon is
    # running: dial unix ..."); the row is one line in a table, so cut at a word.
    return first if len(first) <= 110 else first[:110].rsplit(" ", 1)[0] + " ..."


def summary_lines(diag):
    """The indented detail block shown under a docker failure.

    Only facts that were actually established appear; a probe that came back empty
    contributes no line at all, so the block never pads itself with unknowns.
    """
    if diag.reachable:
        return []
    lines = []
    if diag.timed_out:
        lines.append("no answer within the timeout — the daemon may be starting, or wedged")
    if diag.docker_host:
        lines.append(f"DOCKER_HOST: {diag.docker_host}  (overrides the docker context)")
    if diag.endpoint:
        lines.append(f"dialed: {diag.endpoint}")
    if diag.active_context:
        lines.append(f"context: {diag.active_context} (active)")
    if diag.other_contexts:
        lines.append(f"also configured: {', '.join(diag.other_contexts)}")
    if diag.stderr_tail and not diag.timed_out:
        lines.append(f"docker said: {diag.stderr_tail}")
    return lines


def context_hint(diag):
    """A concrete next step when another configured context might be the live one.

    Returns "" unless there IS an alternative to name — the suggestion is only made
    because `docker context ls` listed the name, never because it seemed likely.
    """
    if diag.reachable or diag.docker_host or not diag.other_contexts:
        return ""
    names = ", ".join(f"`docker context use {n}`" for n in diag.other_contexts)
    return f"If the runtime you're running owns one of the other contexts, switch to it: {names}."
