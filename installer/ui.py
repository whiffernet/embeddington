"""Rich console helpers: the wizard's entire look-and-feel lives here.

Interactive input always flows through an injected input_fn (production: builtins.input
reading the /dev/tty that install.sh wired to stdin), so every prompt is unit-testable.
"""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from installer import errors

BANNER = r"""
  ___ __  __ ___ ___ ___  ___ ___ _  _  ___ _____ ___  _  _
 | __|  \/  | _ ) __|   \|   \_ _| \| |/ __|_   _/ _ \| \| |
 | _|| |\/| | _ \ _|| |) | |) | || .` | (_ |  | || (_) | .` |
 |___|_|  |_|___/___|___/|___/___|_|\_|\___|  |_| \___/|_|\_|
"""

QUOTES = [
    "The Dude abides.",
    "Careful, man, there's a beverage here!",
    "New information has come to light, man.",
    "This is a very complicated case. A lotta ins, a lotta outs.",
    "Yeah, well, that's just, like, your opinion, man.",
]


def make_console():
    """Production console (auto-detects terminal capabilities)."""
    return Console()


def show_banner(console):
    """Print the ASCII banner, a plain-text name line, and a per-process rotating quote."""
    import os

    console.print(f"[bold cyan]{BANNER}[/bold cyan]")
    console.print("  [bold]embeddington[/bold] — the knowledge graph that ties the room together")
    console.print(f'  [italic dim]"{QUOTES[os.getpid() % len(QUOTES)]}"[/italic dim]\n')


def rule(console, title):
    """Section divider."""
    console.rule(f"[bold]{title}[/bold]")


def show_error(console, err):
    """Render a SetupError as the standard three-layer panel (friendly / fix / code+URL)."""
    body = f"{err.friendly}\n\n[bold]Fix:[/bold] {err.fix}\n\n[dim]{errors.anchor(err.code)}[/dim]"
    console.print(Panel(body, title=f"[red]✗ {err.code}[/red]", border_style="red"))


def check_rows(console, rows):
    """Render preflight/doctor rows: (name, ok, detail)."""
    table = Table(show_header=False, box=None, pad_edge=False)
    for name, ok, detail in rows:
        mark = "[green]✓[/green]" if ok else "[red]✗[/red]"
        table.add_row(mark, name, f"[dim]{detail}[/dim]")
    console.print(table)


def confirm(console, prompt, *, default=False, assume_yes=False, input_fn=input):
    """y/N (or Y/n) confirmation. assume_yes returns the default without reading input."""
    if assume_yes:
        return default
    # Escaped: Rich would otherwise parse a bare [y/N] as a markup tag and swallow it.
    suffix = r"\[Y/n]" if default else r"\[y/N]"
    console.print(f"{prompt} {suffix} ", end="")
    answer = input_fn().strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def typed_confirm(console, prompt, *, word="delete", input_fn=input):
    """Destructive confirmation: only typing `word` exactly returns True.

    Deliberately has NO assume_yes parameter — unattended data deletion is gated by the
    --really-delete-data flag at the call site, never by a generic yes.
    """
    console.print(f"{prompt}\n  Type [bold red]{word}[/bold red] to confirm: ", end="")
    return input_fn().strip() == word


def choose(console, prompt, options, *, default_key, assume_yes=False, input_fn=input):
    """Single-keypress menu. options = [(key, label), ...]; returns the chosen key."""
    if assume_yes:
        return default_key
    keys = [k for k, _ in options]
    while True:
        console.print(prompt)
        for key, label in options:
            marker = " (default)" if key == default_key else ""
            console.print(f"  [bold]{key}[/bold]  {label}{marker}")
        console.print("> ", end="")
        answer = input_fn().strip().lower()
        if not answer:
            return default_key
        if answer in keys:
            return answer
        console.print("[yellow]Didn't catch that, man — pick one of the letters.[/yellow]")
