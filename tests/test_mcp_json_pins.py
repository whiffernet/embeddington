"""Pins on the shipped .mcp.json — the config Claude Code auto-discovers.

The interpreter is the sharp edge (issue #85): the wizard installs mcp/requirements.txt
with its OWN interpreter (the clone's venv), so a config that launches a different
`python3` off PATH starts a server that cannot import its dependencies. These pins keep
the two ends pointed at the same interpreter.
"""

import json
from pathlib import Path

MCP_JSON = Path(__file__).resolve().parent.parent / ".mcp.json"


def _server():
    return json.loads(MCP_JSON.read_text())["mcpServers"]["embeddington"]


def test_interpreter_is_the_clone_venv_not_bare_python3():
    """A bare `python3` off PATH is the wrong interpreter on every machine."""
    assert _server()["command"] == ".venv/bin/python"


def test_paths_stay_relative_to_the_project_root():
    """Relative resolves against the client's working directory, which for a
    project-scoped .mcp.json is the clone. Absolute paths would have to be generated
    into this committed file, dirtying the tree against `git pull --ff-only`."""
    server = _server()
    assert server["args"] == ["mcp/server.py"]
    assert not server["command"].startswith("/")


def test_no_variable_expansion_anywhere():
    """An UNSET variable is passed through literally, not as empty and not omitted
    (measured against the client). A literal `${ARANGO_ROOT_PASSWORD}` is non-empty, so
    it passes server.py's emptiness guard AND — because process env beats a .env file
    loaded with override=False — permanently shadows the password the wizard writes to
    mcp/.env. Expansion in this file is therefore banned outright."""
    assert "${" not in MCP_JSON.read_text()


def test_no_env_block_shadowing_mcp_env():
    """config.py's defaults already equal the values this file used to set, and any
    entry here wins over mcp/.env — silently reverting a user's own customization."""
    assert "env" not in _server()
