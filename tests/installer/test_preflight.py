"""Preflight: every check individually, then the aggregate."""

from installer import preflight
from installer.runner import RunResult
from tests.installer.conftest import FakeHttp, FakeRun


def usage(free_gb):
    class U:
        free = free_gb * 2**30
        total = 100 * 2**30
        used = total - free

    return lambda path: U()


def closed_ports(*open_ports):
    """connect(port) -> True if something is listening."""
    return lambda port: port in open_ports


def run_all(run=None, http=None, *, disk=50, py=(3, 12, 4), ports=()):
    return preflight.run_preflight(
        run or FakeRun(),
        http or FakeHttp(),
        disk_path="/",
        version_info=py,
        disk_usage=usage(disk),
        connect=closed_ports(*ports),
    )


def by_name(results, name):
    return next(r for r in results if r.name == name)


def test_python_ok_and_too_old():
    assert by_name(run_all(py=(3, 12, 4)), "python").ok
    assert not by_name(run_all(py=(3, 11, 9)), "python").ok


def test_disk_fatal_warn_and_ok():
    assert not by_name(run_all(disk=2), "disk").ok
    low = by_name(run_all(disk=8), "disk")
    assert low.ok and low.warn  # between 3 and 12 GB: proceed with a warning
    fine = by_name(run_all(disk=50), "disk")
    assert fine.ok and not fine.warn


def test_free_ports_are_ok():
    results = run_all(ports=())
    for port in preflight.PORT_SERVICES:
        assert by_name(results, f"port {port}").ok


def test_our_qdrant_on_6333_is_ok():
    http = FakeHttp({":6333/collections": (200, '{"result": {"collections": []}}')})
    assert by_name(run_all(http=http, ports=(6333,)), "port 6333").ok


def test_6334_is_ours_only_when_qdrant_rest_is_ours():
    # 6334 is gRPC (not classifiable over HTTP): it counts as ours iff 6333 does.
    ours = FakeHttp({":6333/collections": (200, '{"result": {"collections": []}}')})
    assert by_name(run_all(http=ours, ports=(6333, 6334)), "port 6334").ok
    assert not by_name(run_all(ports=(6334,)), "port 6334").ok  # 6333 free, 6334 held


def test_foreign_service_on_6333_is_not_ok():
    http = FakeHttp({":6333/collections": (200, "<html>hi from my other app</html>")})
    assert not by_name(run_all(http=http, ports=(6333,)), "port 6333").ok


def test_arango_401_counts_as_ours():
    http = FakeHttp({":8529/_api/version": (401, "")})
    assert by_name(run_all(http=http, ports=(8529,)), "port 8529").ok


def test_docker_check_reflects_docker_info_rc():
    ok_run = FakeRun([RunResult(0, "", "")])
    bad_run = FakeRun([RunResult(1, "", "Cannot connect to the Docker daemon")])
    assert by_name(run_all(run=ok_run), "docker").ok
    assert not by_name(run_all(run=bad_run), "docker").ok
