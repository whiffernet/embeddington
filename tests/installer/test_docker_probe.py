"""Diagnosis of a docker that isn't answering: sensing the binary, naming the endpoint.

Everything here is pure — an injected `exists` for the filesystem, FakeRun for the two
read-only `docker context` calls. No real docker anywhere.
"""

import json

from installer import docker_probe
from installer.runner import RunResult
from tests.installer.conftest import FakeRun

CONTEXT_INSPECT = json.dumps(
    [
        {
            "Name": "desktop-linux",
            "Endpoints": {"docker": {"Host": "unix:///Users/u/.docker/run/docker.sock"}},
        }
    ]
)
CONTEXT_LS = "default\ndesktop-linux\norbstack\n"

DOWN = RunResult(1, "", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock.")


# --- find_docker_binary ---------------------------------------------------------


def only(*present):
    """An `exists` that reports exactly these paths (as strings) as present."""
    wanted = set(present)
    return lambda path: str(path) in wanted


def test_finds_orbstack_in_its_home_dir():
    found = docker_probe.find_docker_binary(
        exists=only("/Users/u/.orbstack/bin/docker"), home="/Users/u", env={}
    )
    assert str(found) == "/Users/u/.orbstack/bin/docker"


def test_finds_docker_desktop_and_homebrew_locations():
    for path in ("/usr/local/bin/docker", "/opt/homebrew/bin/docker"):
        found = docker_probe.find_docker_binary(exists=only(path), home="/Users/u", env={})
        assert str(found) == path


def test_nothing_on_disk_is_none_not_a_guess():
    """[CRITIC] The whole point: we report where docker IS, never where it might be."""
    assert docker_probe.find_docker_binary(exists=lambda p: False, home="/Users/u", env={}) is None


def test_env_override_wins_over_every_sensed_location():
    found = docker_probe.find_docker_binary(
        exists=only("/opt/weird/docker", "/usr/local/bin/docker"),
        home="/Users/u",
        env={"EMBEDDINGTON_DOCKER_BIN": "/opt/weird/docker"},
    )
    assert str(found) == "/opt/weird/docker"


def test_env_override_pointing_at_nothing_is_ignored():
    """A stale override must fall through to sensing, not dead-end the run."""
    found = docker_probe.find_docker_binary(
        exists=only("/usr/local/bin/docker"),
        home="/Users/u",
        env={"EMBEDDINGTON_DOCKER_BIN": "/gone/docker"},
    )
    assert str(found) == "/usr/local/bin/docker"


# --- diagnose -------------------------------------------------------------------


def test_diagnose_keeps_the_daemon_error_line():
    diag = docker_probe.diagnose(
        FakeRun([RunResult(1, CONTEXT_INSPECT, ""), RunResult(0, "", "")]), DOWN
    )
    assert "Cannot connect to the Docker daemon" in diag.stderr_tail


def test_diagnose_names_the_active_context_and_endpoint():
    run = FakeRun([RunResult(0, CONTEXT_INSPECT, ""), RunResult(0, CONTEXT_LS, "")])
    diag = docker_probe.diagnose(run, DOWN)
    assert diag.active_context == "desktop-linux"
    assert diag.endpoint == "unix:///Users/u/.docker/run/docker.sock"
    assert diag.other_contexts == ("default", "orbstack")


def test_diagnose_reports_a_docker_host_override():
    """DOCKER_HOST beats the context entirely — saying "context" would misdirect."""
    run = FakeRun([RunResult(0, CONTEXT_INSPECT, ""), RunResult(0, CONTEXT_LS, "")])
    diag = docker_probe.diagnose(run, DOWN, env={"DOCKER_HOST": "tcp://10.0.0.9:2375"})
    assert diag.docker_host == "tcp://10.0.0.9:2375"


def test_diagnose_never_probes_a_healthy_daemon():
    """No point spending two subprocess calls when docker info already succeeded."""
    run = FakeRun()
    docker_probe.diagnose(run, RunResult(0, "", ""))
    assert run.calls == []


def test_diagnose_never_probes_a_missing_binary():
    """rc 127 means there is nothing to ask; `docker context ls` would just fail again."""
    run = FakeRun()
    docker_probe.diagnose(run, RunResult(127, "", "command not found: docker"))
    assert run.calls == []


def test_diagnose_survives_unparseable_context_output():
    """A future CLI format change must degrade to "no detail", never crash the ladder."""
    run = FakeRun([RunResult(0, "not json at all", ""), RunResult(0, CONTEXT_LS, "")])
    diag = docker_probe.diagnose(run, DOWN)
    assert diag.active_context is None and diag.endpoint is None


def test_diagnose_survives_context_subcommand_failing():
    run = FakeRun([RunResult(1, "", "unknown command"), RunResult(1, "", "unknown command")])
    diag = docker_probe.diagnose(run, DOWN)
    assert diag.active_context is None
    assert "Cannot connect" in diag.stderr_tail


# --- docker_info: a timeout is its own answer -----------------------------------


def test_docker_info_timeout_is_a_result_not_an_exception():
    """[CRITIC] An unbounded `docker info` makes a cold-starting daemon look like a
    frozen installer. The timeout must come back as a distinguishable result."""
    import subprocess

    def hangs(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 20))

    result = docker_probe.docker_info(hangs, timeout=20)
    assert result.rc == docker_probe.RC_TIMEOUT
    assert "20" in result.err


def test_docker_info_passes_the_timeout_through():
    run = FakeRun()
    docker_probe.docker_info(run, timeout=7)
    assert run.calls[0]["cmd"] == ["docker", "info"]


def test_timed_out_diagnosis_says_so():
    diag = docker_probe.diagnose(FakeRun(), RunResult(docker_probe.RC_TIMEOUT, "", "timed out"))
    assert diag.timed_out is True


# --- rendering ------------------------------------------------------------------


def test_detail_line_carries_the_error_for_the_preflight_row():
    diag = docker_probe.diagnose(FakeRun([RunResult(1, "", ""), RunResult(1, "", "")]), DOWN)
    assert "Cannot connect to the Docker daemon" in docker_probe.short_detail(diag)


def test_detail_line_for_a_reachable_daemon_is_the_plain_ok_text():
    diag = docker_probe.diagnose(FakeRun(), RunResult(0, "", ""))
    assert docker_probe.short_detail(diag) == "daemon reachable"


def test_summary_lines_name_endpoint_context_and_alternatives():
    run = FakeRun([RunResult(0, CONTEXT_INSPECT, ""), RunResult(0, CONTEXT_LS, "")])
    text = "\n".join(docker_probe.summary_lines(docker_probe.diagnose(run, DOWN)))
    assert "unix:///Users/u/.docker/run/docker.sock" in text
    assert "desktop-linux" in text
    assert "orbstack" in text


def test_summary_lines_are_empty_when_there_is_nothing_to_say():
    diag = docker_probe.diagnose(FakeRun(), RunResult(0, "", ""))
    assert docker_probe.summary_lines(diag) == []


def test_long_daemon_errors_are_cut_at_a_word_for_the_table_row():
    """Verified shape from a real docker CLI: the connect error runs well past a row."""
    long_err = RunResult(
        1,
        "",
        "failed to connect to the docker API at unix:///tmp/nope.sock; check if the path "
        "is correct and if the daemon is running: dial unix /tmp/nope.sock: connect: no "
        "such file or directory",
    )
    detail = docker_probe.short_detail(docker_probe.diagnose(FakeRun(), long_err))
    assert detail.endswith(" ...") and len(detail) <= 114
    assert not detail.rstrip(" .").endswith("dae")
