"""Preflight checks — the "rug check" table shown before anything is touched.

Each check returns a CheckResult; nothing here mutates state or raises. The CLI decides
what is fatal (disk < 3 GB → EMB-15, foreign port → EMB-24, docker → the ladder).
"""

import socket
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    warn: bool = False


# port -> service. All four ports consumer/docker-compose.yml publishes.
PORT_SERVICES = {
    6333: "qdrant",
    6334: "qdrant-grpc",
    8529: "arango",
    8100: "embed",
}

DISK_FAIL_GB = 3
DISK_WARN_GB = 12


def _default_connect(port):
    """True when something is listening on localhost:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _classify_port(port, service, http_get):
    """A listening port is "ours" if it answers like the embeddington service."""
    try:
        if service == "qdrant":
            status, body = http_get(f"http://localhost:{port}/collections")
            return status == 200 and '"result"' in body
        if service == "qdrant-grpc":
            # gRPC isn't classifiable over HTTP: ours iff the neighboring REST port is.
            return _classify_port(6333, "qdrant", http_get)
        if service == "arango":
            status, _ = http_get(f"http://localhost:{port}/_api/version")
            return status in (200, 401)
        status, _ = http_get(f"http://localhost:{port}/")
        return True  # embed: any HTTP answer on 8100 is treated as ours
    except OSError:
        return False  # listening but not speaking HTTP -> foreign


def run_preflight(run, http_get, *, disk_path, version_info=None, disk_usage=None, connect=None):
    """Run every check; return the full list (the caller renders and judges).

    Args:
        run: runner.run-compatible callable (docker info).
        http_get: runner.http_get-compatible callable (port classification).
        disk_path: filesystem path whose volume is measured for free space.
        version_info: (major, minor, micro); defaults to sys.version_info.
        disk_usage: shutil.disk_usage-compatible; injected in tests.
        connect: callable(port) -> bool (something listening?); injected in tests.
    """
    import shutil

    version_info = tuple(sys.version_info[:3]) if version_info is None else version_info
    disk_usage = shutil.disk_usage if disk_usage is None else disk_usage
    connect = _default_connect if connect is None else connect

    results = []

    py_ok = version_info >= (3, 12)
    results.append(
        CheckResult("python", py_ok, f"{version_info[0]}.{version_info[1]} (need 3.12+)")
    )

    free_gb = disk_usage(disk_path).free / 2**30
    disk_ok = free_gb >= DISK_FAIL_GB
    results.append(
        CheckResult(
            "disk",
            disk_ok,
            f"{free_gb:.0f} GB free (want {DISK_WARN_GB}+ for comfort)",
            warn=disk_ok and free_gb < DISK_WARN_GB,
        )
    )

    for port, service in PORT_SERVICES.items():
        if not connect(port):
            results.append(CheckResult(f"port {port}", True, f"free ({service})"))
        elif _classify_port(port, service, http_get):
            results.append(CheckResult(f"port {port}", True, f"already ours ({service})"))
        else:
            results.append(
                CheckResult(
                    f"port {port}",
                    False,
                    f"taken by something that isn't {service}",
                )
            )

    docker = run(["docker", "info"])
    results.append(
        CheckResult(
            "docker",
            docker.rc == 0,
            "daemon reachable" if docker.rc == 0 else "not reachable",
        )
    )
    return results
