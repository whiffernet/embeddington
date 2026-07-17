"""Guard the README's third-party licence table against drift.

That table is legally load-bearing: it tells a reader that ArangoDB 3.12.4 is
BUSL-1.1 (not open source, no third-party DBaaS) while Qdrant v1.16.3 is
Apache-2.0. Nothing otherwise stops someone bumping an image tag in
docker-compose.yml and leaving the table asserting a licence for a version the
user no longer pulls.

These tests are cheap insurance, not a licence audit. They prove the table names
the versions we actually ship. A human still has to re-read the upstream LICENSE
when a pin moves -- which is exactly the moment these tests fail and force it.
"""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_COMPOSE = _ROOT / "consumer" / "docker-compose.yml"

# image name -> the bolded component label used in the README table
_COMPONENTS = {
    "qdrant/qdrant": "Qdrant",
    "arangodb/arangodb": "ArangoDB",
}

# The licence each pinned engine ships under, verified against the LICENSE file
# at that tag. If a pin moves, re-read the upstream LICENSE before touching this.
_LICENCES = {
    "Qdrant": "Apache 2.0",
    "ArangoDB": "BUSL 1.1",
}


def _readme_text():
    for name in ("README.md", "readme.md"):
        p = _ROOT / name
        if p.exists():
            return p.read_text(encoding="utf-8")
    raise AssertionError("no README found at the repo root")


def _compose_pins():
    """Return {image_name: tag} for every pinned image in the compose file.

    Returns:
        Mapping of image name (e.g. ``qdrant/qdrant``) to its tag.
    """
    pins = {}
    for line in _COMPOSE.read_text(encoding="utf-8").splitlines():
        m = re.search(r"^\s*image:\s*([^\s:]+):(\S+)\s*$", line)
        if m:
            pins[m.group(1)] = m.group(2)
    return pins


def test_compose_pins_every_engine_image():
    """An unpinned image (`:latest`) would make the licence table meaningless."""
    pins = _compose_pins()
    for image in _COMPONENTS:
        assert image in pins, f"{image} is not pinned in docker-compose.yml"
        assert pins[image] != "latest", f"{image} must be pinned to a version, not :latest"


@pytest.mark.parametrize("image,component", sorted(_COMPONENTS.items()))
def test_readme_table_names_the_pinned_version(image, component):
    """The version in the README table must be the one docker actually pulls."""
    tag = _compose_pins()[image]
    row = _component_row(component)
    assert f"`{tag}`" in row, (
        f"README third-party table says {component} is some version other than "
        f"the pinned {tag!r}. Bump the table -- and re-read the upstream LICENSE "
        f"before you do, because the licence may have changed with the version."
    )


@pytest.mark.parametrize("component,licence", sorted(_LICENCES.items()))
def test_readme_table_states_the_licence(component, licence):
    assert licence in _component_row(component), (
        f"README third-party table no longer states {licence} for {component}"
    )


def _component_row(component):
    """Return the README table row for ``component``.

    Args:
        component: The bolded label, e.g. ``Qdrant``.

    Returns:
        The row's text.

    Raises:
        AssertionError: If no such row exists.
    """
    for line in _readme_text().splitlines():
        if line.startswith("|") and f"**{component}**" in line:
            return line
    raise AssertionError(f"no third-party table row for {component} in the README")


def test_every_emb_code_has_a_readme_troubleshooting_heading():
    """Every registered installer error code must have its README anchor.

    show_error() prints github.com/whiffernet/embeddington#emb-nn; that anchor only
    exists if the README carries a `#### EMB-nn` heading.
    """
    from installer import errors

    readme = _readme_text()
    missing = [code for code in errors.CODES if f"#### {code}" not in readme]
    assert not missing, f"EMB codes without a README troubleshooting heading: {missing}"
