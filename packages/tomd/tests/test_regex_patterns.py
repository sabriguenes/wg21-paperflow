"""Tests for the shared document- and section-number regex patterns.

After the consolidation in issue 08, `DOC_NUM_PATTERN` and
`SECTION_NUM_PATTERN` live in `lib/__init__.py`; the PDF-specific
labeled variants in `lib/pdf/types.py` are built on top of them.
These tests lock down the behavior each call site depends on.
"""

from tomd.lib import DOC_NUM_RE, SECTION_NUM_PREFIX_RE
from tomd.lib.pdf.types import DOC_FIELD_RE, SECTION_NUM_RE, Confidence
from tomd.lib.pdf.structure import _heading_level_from_number, heading_confidence


def test_doc_num_matches_all_wg21_forms():
    for s in ("P1234", "P1234R0", "P12345R9", "D0042R3", "N5012", "SD-9"):
        assert DOC_NUM_RE.search(s), f"failed to match {s!r}"


def test_doc_num_group_zero_returns_full_number():
    # Call sites depend on m.group(0) returning the matched number.
    m = DOC_NUM_RE.search("see P1234R0 for details")
    assert m is not None
    assert m.group(0).upper() == "P1234R0"


def test_doc_num_rejects_too_short_prefix():
    # WG21 doc numbers have at least 3 digits; shorter must not match.
    assert DOC_NUM_RE.search("P12") is None
    assert DOC_NUM_RE.search("N42") is None


def test_doc_field_matches_labeled_forms():
    m = DOC_FIELD_RE.search("Document Number: P1234R0")
    assert m and m.group(1).upper() == "P1234R0"
    m = DOC_FIELD_RE.search("Document #: N5012")
    assert m and m.group(1).upper() == "N5012"


def test_doc_field_now_supports_sd_form():
    # Regression: after consolidation DOC_FIELD_RE inherits SD-N support
    # from the shared DOC_NUM_PATTERN.
    m = DOC_FIELD_RE.search("Document Number: SD-1")
    assert m and m.group(1).upper() == "SD-1"


def test_section_num_prefix_strips_leading_number():
    assert SECTION_NUM_PREFIX_RE.sub("", "2.1.3 Details") == "Details"
    assert SECTION_NUM_PREFIX_RE.sub("", "1. Introduction") == "Introduction"
    # Non-matching input passes through unchanged.
    assert SECTION_NUM_PREFIX_RE.sub("", "Introduction") == "Introduction"


def test_section_num_re_captures_number_and_title():
    m = SECTION_NUM_RE.match("2.1.3 Details of the feature")
    assert m is not None
    assert m.group(1) == "2.1.3"
    assert m.group(2) == "Details of the feature"
    assert SECTION_NUM_RE.match("Abstract") is None


def test_section_num_re_matches_roman_numerals():
    m = SECTION_NUM_RE.match("I INTRODUCTION")
    assert m is not None
    assert m.group(1) == "I"
    assert m.group(2) == "INTRODUCTION"

    m = SECTION_NUM_RE.match("IV Primary Behaviour")
    assert m is not None
    assert m.group(1) == "IV"
    assert m.group(2) == "Primary Behaviour"

    m = SECTION_NUM_RE.match("XII Conclusion")
    assert m is not None
    assert m.group(1) == "XII"
    assert m.group(2) == "Conclusion"


def test_section_num_prefix_strips_roman_numerals():
    assert SECTION_NUM_PREFIX_RE.sub("", "I. Introduction") == "Introduction"
    assert SECTION_NUM_PREFIX_RE.sub("", "IV Behaviour") == "Behaviour"


def test_roman_numeral_no_false_match_on_standalone():
    """Standalone Roman-like words must not match SECTION_NUM_RE (no trailing content)."""
    assert SECTION_NUM_RE.match("I") is None
    assert SECTION_NUM_RE.match("IV") is None


def test_heading_level_from_number_arabic():
    assert _heading_level_from_number("1") == 2
    assert _heading_level_from_number("2.1") == 3
    assert _heading_level_from_number("3.1.2") == 4


def test_heading_level_from_number_roman():
    assert _heading_level_from_number("I") == 2
    assert _heading_level_from_number("IV") == 2
    assert _heading_level_from_number("XII") == 2


def test_heading_confidence_bold_only_short_text():
    """Bold-only (no number, no font size, no known section) returns LOW.

    Level 0 signals the caller must assign a context-derived level.
    """
    level, conf = heading_confidence(
        has_number=False, number_level=0,
        font_level=None, is_bold=True, is_known=False)
    assert level == 0
    assert conf == Confidence.LOW


def test_heading_confidence_not_bold_not_known():
    """No signals at all returns UNCERTAIN non-heading."""
    level, conf = heading_confidence(
        has_number=False, number_level=0,
        font_level=None, is_bold=False, is_known=False)
    assert level == 0
    assert conf == Confidence.UNCERTAIN
