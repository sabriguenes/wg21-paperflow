# Copyright (c) 2026 C++ Alliance, Inc. (https://cppalliance.org)
# Distributed under the Boost Software License, Version 1.0.
# https://www.boost.org/LICENSE_1_0.txt

"""Unit tests for _decode_dingbats in lib.pdf.emit."""

import pytest
from tomd.lib.pdf.types import Span
from tomd.lib.pdf.emit import _decode_dingbats


def _dingbat_span(text: str) -> Span:
    return Span(text=text, font_name="ZapfDingbats", font_size=10.0)


def _normal_span(text: str) -> Span:
    return Span(text=text, font_name="TimesNewRoman", font_size=10.0)


class TestDecodeDingbats:
    """Tests for the \x14 -> checkmark / \x18 -> cross mapping."""

    def test_checkmark_decoded(self):
        result = _decode_dingbats(_dingbat_span("\x14"))
        assert result.text == "✓"

    def test_cross_decoded(self):
        result = _decode_dingbats(_dingbat_span("\x18"))
        assert result.text == "✗"

    def test_mixed_dingbats_and_text(self):
        result = _decode_dingbats(_dingbat_span("A\x14B\x18C"))
        assert result.text == "A✓B✗C"

    def test_non_dingbats_font_unchanged(self):
        span = _normal_span("\x14\x18")
        result = _decode_dingbats(span)
        assert result is span
        assert result.text == "\x14\x18"

    def test_no_dingbats_chars_returns_same_span(self):
        span = _dingbat_span("normal text")
        result = _decode_dingbats(span)
        assert result is span

    def test_empty_text(self):
        span = _dingbat_span("")
        result = _decode_dingbats(span)
        assert result is span

    def test_none_font_name_unchanged(self):
        span = Span(text="\x14", font_name=None, font_size=10.0)
        result = _decode_dingbats(span)
        assert result is span

    def test_preserves_span_properties(self):
        span = Span(
            text="\x14",
            font_name="ZapfDingbats",
            font_size=12.0,
            bold=True,
            italic=True,
            monospace=False,
            bbox=(10, 20, 30, 40),
            origin=(15, 25),
            color=0xFF0000,
        )
        result = _decode_dingbats(span)
        assert result.text == "✓"
        assert result.font_name == "ZapfDingbats"
        assert result.font_size == 12.0
        assert result.bold is True
        assert result.italic is True
        assert result.bbox == (10, 20, 30, 40)
        assert result.color == 0xFF0000

    def test_only_checkmark_no_cross(self):
        result = _decode_dingbats(_dingbat_span("\x14\x14\x14"))
        assert result.text == "✓✓✓"

    def test_only_cross_no_checkmark(self):
        result = _decode_dingbats(_dingbat_span("\x18\x18"))
        assert result.text == "✗✗"

    @pytest.mark.parametrize("byte_val", [0x01, 0x02, 0x10, 0x15, 0x19, 0x7F])
    def test_unmapped_control_chars_pass_through(self, byte_val):
        """Control chars not in _DINGBATS_MAP should pass through unchanged."""
        ch = chr(byte_val)
        result = _decode_dingbats(_dingbat_span(ch))
        assert result.text == ch

    def test_transposed_bytes_not_equivalent(self):
        """Verify \x14 and \x18 produce different symbols (catch transposition bug)."""
        check = _decode_dingbats(_dingbat_span("\x14"))
        cross = _decode_dingbats(_dingbat_span("\x18"))
        assert check.text != cross.text
        assert check.text == "✓"
        assert cross.text == "✗"
