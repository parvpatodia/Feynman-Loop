"""Tests for the CLI's stdin block reader, the bug that auto-submitted an empty transfer answer."""

import io

from feynman_loop.cli import _read_block


def test_skips_leading_blanks_then_stops_on_blank_after_content():
    # leading blanks (e.g. a buffered newline) must NOT submit an empty block
    stream = io.StringIO("\n\nfirst line\nsecond line\n\nignored after blank")
    assert _read_block(stream) == "first line\nsecond line"


def test_handles_eof_without_trailing_blank():
    stream = io.StringIO("only line")
    assert _read_block(stream) == "only line"


def test_all_blank_input_returns_empty():
    stream = io.StringIO("\n\n\n")
    assert _read_block(stream) == ""
