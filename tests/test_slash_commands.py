"""Pins on the shipped slash commands (.claude/commands/).

These are a product surface: a user types `/emb-ask` and gets whatever this directory
says. Two things can rot silently — a command can name a tool the server does not have
(the MCP tool set has been renamed before), and a command can lose the caveat that was
the whole reason it exists. Both are cheap to pin and expensive to notice in the field.
"""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_COMMANDS = _ROOT / ".claude" / "commands"
_SERVER = _ROOT / "mcp" / "server.py"

EXPECTED = {
    "emb-ask",
    "emb-search",
    "emb-entity",
    "emb-path",
    "emb-schema",
    "emb-doctor",
    "emb-update",
}

# Anything shaped like one of this server's tool names, wherever it appears in a command.
_TOOL_SHAPED = re.compile(r"\b(enrich|vector_search|kg_[a-z_]+)\b")


def _command_files():
    return sorted(_COMMANDS.glob("*.md"))


def _frontmatter(path):
    """The YAML block between the leading --- fences, as a dict of raw strings."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path.name} has no frontmatter"
    block = text.split("---\n", 2)[1]
    out = {}
    for line in block.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            out[key.strip()] = value.strip().strip('"')
    return out


def _server_tools():
    """Every function decorated with @mcp.tool in the server."""
    lines = _SERVER.read_text(encoding="utf-8").splitlines()
    tools = set()
    for i, line in enumerate(lines):
        if line.strip() == "@mcp.tool":
            for follow in lines[i + 1 : i + 4]:
                match = re.match(r"async def (\w+)\(", follow)
                if match:
                    tools.add(match.group(1))
                    break
    return tools


def test_the_expected_command_set_is_present():
    assert {p.stem for p in _command_files()} == EXPECTED


@pytest.mark.parametrize("path", _command_files(), ids=lambda p: p.stem)
def test_every_command_has_a_description(path):
    """The description is what the user reads in /help — an unnamed command is unusable."""
    assert _frontmatter(path).get("description")


@pytest.mark.parametrize("path", _command_files(), ids=lambda p: p.stem)
def test_commands_only_reference_tools_the_server_actually_exposes(path):
    """A command naming a tool that does not exist fails at the worst possible moment:
    in front of a user, as a confusing model error rather than a clear one."""
    referenced = set(_TOOL_SHAPED.findall(path.read_text(encoding="utf-8")))
    unknown = referenced - _server_tools()
    assert not unknown, f"{path.name} references non-existent tool(s): {sorted(unknown)}"


def test_commands_that_shell_out_declare_bash():
    """The two maintenance commands run the wizard; the query commands must not need it."""
    for stem in ("emb-doctor", "emb-update"):
        assert "Bash" in _frontmatter(_COMMANDS / f"{stem}.md").get("allowed-tools", "")


def _prose(path):
    """File text with wrapping collapsed — these are prose files, so a phrase that must be
    present will not sit conveniently on one line."""
    return " ".join(path.read_text(encoding="utf-8").split())


def test_ask_carries_the_do_not_fabricate_contract():
    """enrich's own docstring instructs callers to say what was NOT found on a weak or
    absent grounding tier. A command that drops that instruction quietly re-enables the
    confident-fabrication failure the grounding signal was built to catch."""
    body = _prose(_COMMANDS / "emb-ask.md")
    assert "grounding.tier" in body
    assert '"weak"' in body and '"none"' in body
    assert "not in the returned content" in body


def test_path_carries_the_hub_caveat():
    """Measured on this graph: most paths between arbitrary entities route through a few
    hubs, and for most such pairs no hub-free route exists at all. A path command without
    that caveat invites narrating a relationship that isn't there."""
    body = _prose(_COMMANDS / "emb-path.md")
    assert "hub" in body.lower()
    assert "no_path" in body


def test_worktrees_are_ignored_so_committing_commands_is_safe():
    """.claude/ is now a tracked directory; a working clone also grows .claude/worktrees/."""
    assert ".claude/worktrees/" in (_ROOT / ".gitignore").read_text(encoding="utf-8")
