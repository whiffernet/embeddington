"""Guards against a hardcoded KG collection/graph/search-view name creeping
back into reader code now that the schema is switched by
`config.kg_collections()` (spec §9.2, Track 2).

Scans only the package's top-level runtime modules -- never `tests/`. The
vendored embeddington-public/mcp/ copy also carries measurement fixtures and
gold files that name v2 collections on purpose; this scan set is identical
in the upstream and vendored locations, so a literal in a fixture never
trips it. `config.py` is excluded too -- it is the one place these names are
allowed to live, since it defines the v2/v3 schema table itself.
"""

import re
from pathlib import Path

import pytest

# Matches entities_v2/v3, relationships_v2/v3, their _search view suffix, and
# servicenow_graph_v2/v3 -- the literal collection/graph/view names that
# config.kg_collections() now owns exclusively.
_COLLECTION_LITERAL = re.compile(r"(entities|relationships)_v[23](_search)?|servicenow_graph_v[23]")

_RUNTIME_MODULES = sorted(
    p for p in Path(__file__).resolve().parents[1].glob("*.py") if p.name != "config.py"
)


@pytest.mark.parametrize("path", _RUNTIME_MODULES, ids=lambda p: p.name)
def test_no_hardcoded_collection_literal(path: Path) -> None:
    text = path.read_text()
    match = _COLLECTION_LITERAL.search(text)
    assert match is None, (
        f"{path.name} hardcodes a KG collection/graph/view literal "
        f"({match.group(0)!r}) -- use config.kg_collections() instead"
    )
