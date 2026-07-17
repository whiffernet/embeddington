"""UI helper tests — Rich console writes to a buffer; input is injected."""

import io

from installer import errors, ui
from rich.console import Console


def buffer_console():
    return Console(file=io.StringIO(), force_terminal=False, width=100)


def out(console):
    return console.file.getvalue()


def test_banner_prints_the_name_and_a_quote():
    # The figlet art alone contains no greppable literal name — show_banner also
    # prints a plain-text tagline line, and that is what this pins.
    console = buffer_console()
    ui.show_banner(console)
    text = out(console)
    assert "embeddington" in text.lower()
    assert any(q in text for q in ui.QUOTES)


def test_show_error_prints_code_fix_and_anchor():
    console = buffer_console()
    ui.show_error(console, errors.SetupError("EMB-21", "Daemon down.", "colima start"))
    text = out(console)
    assert "EMB-21" in text
    assert "colima start" in text
    assert "#emb-21" in text


def test_confirm_default_no_on_enter():
    console = buffer_console()
    assert ui.confirm(console, "Delete?", input_fn=lambda: "") is False


def test_confirm_renders_the_choice_hint():
    # Rich parses a bare "[y/N]" as a markup tag and silently swallows it (verified
    # against rich 15) — the suffix must be escaped so the user actually sees it.
    console = buffer_console()
    ui.confirm(console, "Delete?", input_fn=lambda: "")
    assert "[y/N]" in out(console)


def test_confirm_yes_variants():
    console = buffer_console()
    for answer in ("y", "Y", "yes"):
        assert ui.confirm(console, "Go?", input_fn=lambda a=answer: a) is True


def test_confirm_assume_yes_never_reads_input():
    console = buffer_console()

    def explode():
        raise AssertionError("input read under assume_yes")

    assert ui.confirm(console, "Go?", assume_yes=True, default=True, input_fn=explode) is True


def test_typed_confirm_rejects_y_and_requires_the_word():
    console = buffer_console()
    assert ui.typed_confirm(console, "Really?", input_fn=lambda: "y") is False
    assert ui.typed_confirm(console, "Really?", input_fn=lambda: "delete") is True


def test_typed_confirm_has_no_assume_yes_bypass():
    import inspect

    assert "assume_yes" not in inspect.signature(ui.typed_confirm).parameters


def test_choose_returns_default_on_enter_and_key_on_input():
    console = buffer_console()
    options = [("o", "OrbStack"), ("c", "Colima"), ("n", "None")]
    assert ui.choose(console, "Runtime?", options, default_key="o", input_fn=lambda: "") == "o"
    assert ui.choose(console, "Runtime?", options, default_key="o", input_fn=lambda: "c") == "c"


def test_choose_reprompts_on_garbage():
    console = buffer_console()
    answers = iter(["zzz", "c"])
    got = ui.choose(
        console,
        "Runtime?",
        [("o", "O"), ("c", "C")],
        default_key="o",
        input_fn=lambda: next(answers),
    )
    assert got == "c"
