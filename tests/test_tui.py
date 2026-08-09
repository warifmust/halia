"""Tests for the REPL-TUI input experience (headless, via prompt_toolkit pipe input)."""

from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from halia.cli.tui import HALIA_BANNER, build_key_bindings, build_session


def _prompt(text: str) -> str:
    with create_pipe_input() as inp:
        inp.send_text(text)
        session = build_session(input=inp, output=DummyOutput())
        return session.prompt("› ")


def test_plain_line_submits() -> None:
    # Even in multiline mode, plain Enter (\r) submits.
    assert _prompt("hello world\r") == "hello world"


def test_option_enter_inserts_newline_then_enter_submits() -> None:
    # Option/Alt+Enter (Esc + Enter = \x1b\r) adds a newline; a later plain Enter submits.
    assert _prompt("first\x1b\rsecond\r") == "first\nsecond"


def test_ctrl_j_inserts_newline() -> None:
    # Ctrl+J (\n) — and the remapped Shift+Enter sequences resolve to it — inserts a newline.
    assert _prompt("a\nb\r") == "a\nb"


def test_shift_enter_sequence_inserts_newline() -> None:
    # A terminal that emits the modifyOtherKeys Shift+Enter sequence gets a newline,
    # not a submit (we remapped it away from ControlM).
    assert _prompt("one\x1b[27;2;13~two\r") == "one\ntwo"


def test_multiline_paste_returns_one_block() -> None:
    # Bracketed paste (ESC[200~ … ESC[201~) then Enter: three pasted lines come back
    # as ONE multi-line submission, not three separate inputs.
    pasted = "\x1b[200~line one\nline two\nline three\x1b[201~\r"
    assert _prompt(pasted) == "line one\nline two\nline three"


def test_option_left_is_word_navigation_not_jargon() -> None:
    # Type "foo bar", Option+Left (Esc + Left = \x1b\x1b[D) jumps to the start of "bar",
    # insert "X" → "foo Xbar". The escape sequence is consumed as a word-jump, never
    # leaked into the line as stray characters.
    assert _prompt("foo bar\x1b\x1b[DX\r") == "foo Xbar"


def test_banner_is_present() -> None:
    assert "█" in HALIA_BANNER  # block-letter art rendered


def test_word_nav_bindings_registered() -> None:
    kb = build_key_bindings()
    assert len(kb.bindings) >= 4  # esc+left/right and ctrl+left/right


def test_slash_completer_dropdown() -> None:
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from halia.cli.tui import _SlashCompleter

    comp = _SlashCompleter()
    ev = CompleteEvent()

    def texts(s: str) -> list[str]:
        return [c.text for c in comp.get_completions(Document(s), ev)]

    assert "/help" in texts("/") and "/exit" in texts("/")  # bare / lists all
    assert texts("/q") == ["/quit"]  # filters as you type
    assert set(texts("/c")) == {"/commands", "/clear", "/compact", "/cost"}
    assert texts("hello") == []  # normal chat → no dropdown
    assert texts("/local on") == []  # past the command word → no dropdown
