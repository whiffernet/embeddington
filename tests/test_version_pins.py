"""The Qdrant server image and the Python client have to move together.

`consumer/docker-compose.yml` pins `qdrant/qdrant` to an exact patch, with the reason
written beside it: snapshot restore is version-sensitive, so the image must match the
version that produced the shared snapshot. The client had no upper bound at all, so a
fresh install silently resolved several minors ahead and warned about it on every run —
the client's own rule is that the minor difference must not exceed 1.

These tests make "someone bumps the image and forgets the client" a red build instead of a
warning nobody reads. The policy they encode is the stricter of the two: the client stays
on the server's minor, mirroring the image pin's intent rather than leaning on the client's
±1 tolerance, which is the client's leniency about protocol compatibility and not a promise
about the snapshot format.
"""

import re
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import Version

_ROOT = Path(__file__).resolve().parents[1]
_COMPOSE = _ROOT / "consumer" / "docker-compose.yml"
_PYPROJECT = _ROOT / "pyproject.toml"


def _pinned_server_version():
    """The exact Qdrant version the compose file pins, e.g. Version("1.16.3")."""
    match = re.search(r"image:\s*qdrant/qdrant:v(\d+\.\d+\.\d+)", _COMPOSE.read_text())
    assert match, "no pinned qdrant/qdrant image tag found in the compose file"
    return Version(match.group(1))


def _client_specifier():
    """The qdrant-client requirement from pyproject, as a SpecifierSet."""
    match = re.search(r'"qdrant-client([^"]*)"', _PYPROJECT.read_text())
    assert match, "no qdrant-client requirement found in pyproject.toml"
    return SpecifierSet(match.group(1))


def test_the_client_is_bounded_at_all():
    """An unbounded requirement is the actual defect: it lets the client drift arbitrarily
    far from a server that is pinned for a correctness reason."""
    assert any(s.operator in ("<", "<=", "==", "~=") for s in _client_specifier()), (
        "qdrant-client needs an upper bound tied to the pinned server"
    )


def test_the_bound_admits_the_pinned_server_version():
    server = _pinned_server_version()
    spec = _client_specifier()
    assert spec.contains(f"{server.major}.{server.minor}.0"), (
        f"client bound {spec} excludes the pinned server's own minor {server.major}.{server.minor}"
    )


@pytest.mark.parametrize("offset", [-1, 1])
def test_the_bound_excludes_neighbouring_minors(offset):
    """Mirrors the image pin: the client tracks the server's minor, so moving one without
    the other fails here rather than surfacing as a runtime warning on a user's install."""
    server = _pinned_server_version()
    neighbour = f"{server.major}.{server.minor + offset}.0"
    assert not _client_specifier().contains(neighbour), (
        f"client bound admits {neighbour}, a different minor from the pinned server"
    )


def test_the_clients_own_compatibility_rule_would_be_satisfied():
    """Belt and braces: whatever bound is chosen, every version it admits must also satisfy
    the client's published rule — same major, minor difference at most 1 — since violating
    that is what printed a warning on every install."""
    server = _pinned_server_version()
    spec = _client_specifier()
    for minor in range(0, server.minor + 4):
        candidate = f"{server.major}.{minor}.0"
        if spec.contains(candidate):
            assert abs(minor - server.minor) <= 1, (
                f"bound admits {candidate}, which the client itself calls incompatible "
                f"with server {server}"
            )
