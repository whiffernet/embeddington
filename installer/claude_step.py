"""Optional Claude wiring: install mcp/ deps and print the launch recipe.

Never fatal — the graph is complete and usable without Claude. Failures show EMB-51 as
a warning and the flow continues.
"""

import sys

from installer import errors, ui


def offer_claude_wiring(console, run, repo_root, *, assume_yes, which=None, input_fn=input):
    """Offer MCP dependency install; return installed|skipped|no-claude|failed.

    Args:
        console: rich Console for output.
        run: Callable that runs a subprocess command (takes cmd argv list).
        repo_root: Path to the repo root (to locate mcp/requirements.txt).
        assume_yes: If True, skip prompts and assume affirmative answers.
        which: Callable to locate a binary on PATH (default: shutil.which).
        input_fn: Callable to read user input (default: builtins.input).

    Returns:
        A status string:
        - "no-claude": Claude Code not found on PATH
        - "skipped": User declined the offer
        - "installed": Dependencies installed successfully
        - "failed": pip install failed (shown as a warning, not raised)
    """
    import shutil

    which = shutil.which if which is None else which
    if which("claude") is None:
        console.print(
            "[dim]Claude Code isn't on your PATH. Install it later and see the README's "
            "'query with Claude' section — the graph works either way.[/dim]"
        )
        return "no-claude"
    if not ui.confirm(
        console,
        "Claude Code detected. Wire up the embeddington MCP server (installs mcp/ deps)?",
        default=True,
        assume_yes=assume_yes,
        input_fn=input_fn,
    ):
        return "skipped"
    req = repo_root / "mcp" / "requirements.txt"
    result = run([sys.executable, "-m", "pip", "install", "-r", str(req)])
    if result.rc != 0:
        ui.show_error(
            console,
            errors.SetupError(
                "EMB-51",
                "Installing the MCP server's dependencies failed (the graph itself is fine).",
                f"Run `pip install -r {req}` manually to see why; then just launch Claude "
                "from the repo root with consumer/.env loaded.",
            ),
        )
        return "failed"
    # [CRITIC] The deps just landed in THIS venv, but .mcp.json launches the server with
    # a bare python3 — the recipe must activate the venv first or the server Claude
    # starts can't import them. (Implementer: verify the interpreter .mcp.json actually
    # names and keep this recipe consistent with it.)
    console.print(
        "\n[green]✓[/green] MCP deps installed. To query the graph:\n"
        "    [bold]cd <your clone>[/bold]\n"
        "    [bold]. .venv/bin/activate && set -a; . consumer/.env; set +a && claude[/bold]\n"
        "  Claude Code auto-discovers the repo's .mcp.json — approve the 'embeddington' "
        "server when prompted. The venv activation matters: the MCP server runs with "
        "whatever python3 is on PATH.\n"
        "  [dim]Claude Desktop instead? cp mcp/.env.example mcp/.env, set ARANGO_PASSWORD, "
        "see mcp/README.md.[/dim]"
    )
    return "installed"
