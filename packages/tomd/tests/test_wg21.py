"""Tests for lib.pdf.wg21 and shared reply-to enrichment."""

import pytest
from tomd.lib.pdf.types import Block, Line, Span
from tomd.lib.pdf.wg21 import extract_metadata_from_blocks, REPLY_TO_CONTINUATION_CAP
from tomd.lib.shared import enrich_reply_to_names, normalize_date


def _meta_block(lines_text, page_num=0, font_size=9.0):
    lines = []
    for text in lines_text:
        span = Span(text=text, font_size=font_size)
        lines.append(Line(spans=[span], page_num=page_num))
    return Block(lines=lines, page_num=page_num)


def test_extracts_doc_number():
    b = _meta_block(["Document Number: P4003R0", "Date: 2026-01-01"])
    meta, consumed = extract_metadata_from_blocks([b])
    assert meta.get("document") == "P4003R0"


def test_extracts_date():
    b = _meta_block(["Document Number: P1234R0", "Date: 2026-03-15"])
    meta, consumed = extract_metadata_from_blocks([b])
    assert meta.get("date") == "2026-03-15"


def test_extracts_audience():
    b = _meta_block(["Document Number: P1234R0", "Audience: LEWG"])
    meta, consumed = extract_metadata_from_blocks([b])
    assert meta.get("audience") == "LEWG"


def test_extracts_reply_to():
    b = _meta_block(["Document Number: P1234R0",
                      "Reply-to: Alice <alice@x.com>"])
    meta, consumed = extract_metadata_from_blocks([b])
    assert "reply-to" in meta
    assert any("Alice" in a for a in meta["reply-to"])


def test_title_picks_largest_font():
    title_block = _meta_block(["My Paper Title"], font_size=16.0)
    label_block = _meta_block(["Subtitle Line"], font_size=9.0)
    meta_block = _meta_block(["Document Number: P1234R0"], font_size=9.0)
    meta, consumed = extract_metadata_from_blocks(
        [label_block, title_block, meta_block])
    assert meta.get("title") == "My Paper Title"


def test_pre_label_blocks_consumed():
    cat_block = _meta_block(["WG21 PROPOSAL"], font_size=9.0)
    title_block = _meta_block(["Real Title"], font_size=16.0)
    meta_block = _meta_block(["Document Number: P1234R0"], font_size=9.0)
    meta, consumed = extract_metadata_from_blocks(
        [cat_block, title_block, meta_block])
    assert 0 in consumed
    assert 1 in consumed


def test_title_prefers_darker_at_same_size():
    light_block = Block(
        lines=[Line(spans=[Span(text="Light Title", font_size=16.0)])],
        page_num=0, bbox=(10, 100, 200, 116))
    dark_block = Block(
        lines=[Line(spans=[Span(text="Dark Title", font_size=16.0)])],
        page_num=0, bbox=(10, 200, 200, 216))
    meta_block = _meta_block(["Document Number: P1234R0"], font_size=9.0)
    text_colors = {100.0: 0.42, 200.0: 0.17}
    meta, consumed = extract_metadata_from_blocks(
        [light_block, dark_block, meta_block], text_colors)
    assert meta.get("title") == "Dark Title"


def test_title_selected_with_empty_color_data():
    title_block = _meta_block(["My Paper Title"], font_size=16.0)
    meta_block = _meta_block(["Document Number: P1234R0"], font_size=9.0)
    meta, consumed = extract_metadata_from_blocks(
        [title_block, meta_block], {})
    assert meta.get("title") == "My Paper Title"


def test_reply_to_name_email_on_same_line():
    b = _meta_block(["Document Number: P1234R0",
                      "Reply-to: Alice Smith alice@example.com"])
    meta, consumed = extract_metadata_from_blocks([b])
    assert "reply-to" in meta
    assert any("Alice Smith" in a for a in meta["reply-to"])
    assert any("alice@example.com" in a for a in meta["reply-to"])


def test_reply_to_name_then_email_on_next_line():
    b = _meta_block(["Document Number: P1234R0",
                      "Reply-to: Bob Jones",
                      "bob@example.com"])
    meta, consumed = extract_metadata_from_blocks([b])
    assert "reply-to" in meta
    assert any("Bob Jones" in a for a in meta["reply-to"])


def test_reply_to_continuation_capped():
    """Reply-to loop must stop after REPLY_TO_CONTINUATION_CAP blocks,
    even if later blocks still contain emails."""
    reply_block = _meta_block(["Reply-to: Alice <alice@x.com>"])
    # Generate more continuation blocks than the cap allows
    extra_count = REPLY_TO_CONTINUATION_CAP + 5
    extras = [
        _meta_block([f"Person{n} <p{n}@x.com>"])
        for n in range(extra_count)
    ]
    blocks = [reply_block] + extras
    meta, consumed = extract_metadata_from_blocks(blocks)
    # The continuation blocks consumed must not exceed the cap
    # (block 0 is consumed as the reply-to label block itself)
    continuation_consumed = consumed - {0}
    assert len(continuation_consumed) == REPLY_TO_CONTINUATION_CAP
    # The block just past the cap must not be consumed
    past_cap_idx = 1 + REPLY_TO_CONTINUATION_CAP
    assert past_cap_idx not in consumed


def test_numeric_line_not_parsed_as_author():
    """A bare page number like '1' must not appear as an author entry."""
    b = _meta_block([
        "Reply-to: Alice Smith alice@example.com",
        "1",
    ])
    meta, consumed = extract_metadata_from_blocks([b])
    assert "reply-to" in meta
    for entry in meta["reply-to"]:
        assert entry.strip() != "1", f"Page number leaked into reply-to: {meta['reply-to']}"
    assert any("Alice Smith" in a for a in meta["reply-to"])


def test_numeric_lines_mixed_with_authors():
    """Multiple authors with stray numeric lines in between."""
    b = _meta_block([
        "Reply-to: Alice <alice@x.com>",
        "2",
        "Bob <bob@y.com>",
    ])
    meta, consumed = extract_metadata_from_blocks([b])
    authors = meta.get("reply-to", [])
    assert any("alice@x.com" in a for a in authors)
    for entry in authors:
        assert entry.strip() != "2"


# -- audience contamination tests (Ticket 2) ---------------------------------


def test_audience_stops_at_issues_label():
    """P3828R1: Issues: must not bleed into Audience value."""
    b = _meta_block([
        "Doc No: WG21 P3828R1",
        "Date: 2026-03-07",
        "Reply to: Nicolai Josuttis <nico@josuttis.de>",
        "Audience: LEWG, LWG",
        "Issues:",
        "Previous: P3828R0",
    ])
    meta, _ = extract_metadata_from_blocks([b])
    assert meta.get("audience") == "LEWG, LWG"


def test_audience_stops_at_follow_up():
    """P3952R0: Follow up to: must not bleed into Audience value."""
    b = _meta_block([
        "Document Number: P3952R0",
        "Audience: SG1, LEWG",
        "Follow up to: P3900R0",
        "Reply-to: Author <a@b.com>",
    ])
    meta, _ = extract_metadata_from_blocks([b])
    assert meta.get("audience") == "SG1, LEWG"


def test_target_does_not_overwrite_audience():
    """P3844R3: Target: must not overwrite an existing Audience value."""
    b = _meta_block([
        "Document Number: P3844R3",
        "Audience: LEWG",
        "Target: C++26",
        "Reply-to: Author <a@b.com>",
    ])
    meta, _ = extract_metadata_from_blocks([b])
    assert meta.get("audience") == "LEWG"


def test_target_used_as_fallback_when_no_audience():
    """Papers with only Target: (no Audience:) must still get audience."""
    b = _meta_block([
        "Document Number: P9999R0",
        "Target: SG7",
        "Reply-to: Test <t@t.com>",
    ])
    meta, _ = extract_metadata_from_blocks([b])
    assert meta.get("audience") == "SG7"


# -- enrich_reply_to_names tests ------------------------------------------

class TestEnrichReplyToNames:

    def test_pairs_bare_email_via_domain(self):
        """P3970R0 scenario: daveed@vandevoorde.com + David Vandevoorde."""
        reply_to = ["<daveed@vandevoorde.com>"]
        authors = ["David Vandevoorde"]
        result = enrich_reply_to_names(reply_to, authors)
        assert result == ["David Vandevoorde <daveed@vandevoorde.com>"]

    def test_pairs_bare_email_via_local_part(self):
        reply_to = ["<krzemienski@gmail.com>"]
        authors = ["Andrzej Krzemienski"]
        result = enrich_reply_to_names(reply_to, authors)
        assert result == ["Andrzej Krzemienski <krzemienski@gmail.com>"]

    def test_multiple_authors_one_match(self):
        """7 authors, 1 email, 1 matching last name."""
        reply_to = ["<daveed@vandevoorde.com>"]
        authors = [
            "Jeff Garland", "Paul E. McKenney", "Roger Orr",
            "Bjarne Stroustrup", "David Vandevoorde", "Michael Wong",
        ]
        result = enrich_reply_to_names(reply_to, authors)
        assert result == ["David Vandevoorde <daveed@vandevoorde.com>"]

    def test_ambiguous_leaves_bare(self):
        """Two authors whose last names both appear in domain."""
        reply_to = ["<contact@smith-jones.org>"]
        authors = ["Alice Smith", "Bob Jones"]
        result = enrich_reply_to_names(reply_to, authors)
        assert result == ["<contact@smith-jones.org>"]

    def test_no_match_leaves_bare(self):
        reply_to = ["<fraggamuffin@gmail.com>"]
        authors = ["Jeff Garland", "Paul McKenney"]
        result = enrich_reply_to_names(reply_to, authors)
        assert result == ["<fraggamuffin@gmail.com>"]

    def test_already_has_name_untouched(self):
        reply_to = ["Alice Smith <alice@example.com>"]
        authors = ["Alice Smith"]
        result = enrich_reply_to_names(reply_to, authors)
        assert result == ["Alice Smith <alice@example.com>"]

    def test_mixed_entries(self):
        """Mix of bare emails and named entries."""
        reply_to = [
            "Alice Smith <alice@example.com>",
            "<daveed@vandevoorde.com>",
        ]
        authors = ["Alice Smith", "David Vandevoorde"]
        result = enrich_reply_to_names(reply_to, authors)
        assert result[0] == "Alice Smith <alice@example.com>"
        assert result[1] == "David Vandevoorde <daveed@vandevoorde.com>"

    def test_empty_inputs(self):
        assert enrich_reply_to_names([], ["Name"]) == []
        assert enrich_reply_to_names(["<x@y.com>"], []) == ["<x@y.com>"]

    def test_short_last_name_skipped(self):
        """Last names shorter than _MIN_LAST_NAME_LEN (4) are not matched."""
        reply_to = ["<li@example.com>"]
        authors = ["Bo Li"]
        result = enrich_reply_to_names(reply_to, authors)
        assert result == ["<li@example.com>"]

    def test_non_person_name_rejected(self):
        """Metadata fragments like titles must not be treated as author names."""
        reply_to = ["<paulmckrcu@fb.com>"]
        authors = ["Structures: Read-Copy-Update RCU"]
        result = enrich_reply_to_names(reply_to, authors)
        assert result == ["<paulmckrcu@fb.com>"]

    def test_single_word_name_rejected(self):
        """Single-word entries are not person names."""
        reply_to = ["<test@example.com>"]
        authors = ["Example"]
        result = enrich_reply_to_names(reply_to, authors)
        assert result == ["<test@example.com>"]

    @pytest.mark.parametrize("entry", [
        "<daveed@vandevoorde.com>",
        "daveed@vandevoorde.com",
    ])
    def test_bare_email_formats(self, entry):
        """Both <email> and bare email should be enrichable."""
        result = enrich_reply_to_names(
            [entry], ["David Vandevoorde"]
        )
        assert "David Vandevoorde" in result[0]
        assert "daveed@vandevoorde.com" in result[0]

    def test_does_not_mutate_input(self):
        original = ["<daveed@vandevoorde.com>"]
        authors = ["David Vandevoorde"]
        result = enrich_reply_to_names(original, authors)
        assert original == ["<daveed@vandevoorde.com>"]
        assert result != original


# --- normalize_date tests ---


class TestNormalizeDate:
    """Unit tests for normalize_date()."""

    @pytest.mark.parametrize("input_text,expected", [
        ("2026-02-22", "2026-02-22"),
        ("Date: 2026-03-15", "2026-03-15"),
        ("some text 2024-01-01 more", "2024-01-01"),
    ])
    def test_iso_format(self, input_text, expected):
        assert normalize_date(input_text) == expected

    @pytest.mark.parametrize("input_text,expected", [
        ("February 22, 2026", "2026-02-22"),
        ("March 26, 2026", "2026-03-26"),
        ("January 1, 2024", "2024-01-01"),
        ("December 31, 2025", "2025-12-31"),
        ("Feb 22, 2026", "2026-02-22"),
        ("Mar 26, 2026", "2026-03-26"),
        ("Sep. 5, 2025", "2025-09-05"),
        ("Sept 15, 2024", "2024-09-15"),
    ])
    def test_natural_month_day_year(self, input_text, expected):
        assert normalize_date(input_text) == expected

    @pytest.mark.parametrize("input_text,expected", [
        ("22 February 2026", "2026-02-22"),
        ("26 March 2026", "2026-03-26"),
        ("1 January 2024", "2024-01-01"),
        ("5 Sep 2025", "2025-09-05"),
    ])
    def test_euro_day_month_year(self, input_text, expected):
        assert normalize_date(input_text) == expected

    @pytest.mark.parametrize("input_text", [
        "",
        "no date here",
        "P3181R1",
        "2026",
        "March 2026",
    ])
    def test_returns_none_for_invalid(self, input_text):
        assert normalize_date(input_text) is None

    def test_none_input(self):
        assert normalize_date("") is None

    def test_iso_takes_precedence(self):
        """When both ISO and natural are present, ISO wins (leftmost)."""
        assert normalize_date("2026-02-22 aka February 22, 2026") == "2026-02-22"


# --- Integration: natural-language date in PDF block metadata ---


def test_extracts_natural_language_date():
    """PDF with 'Date: February 22, 2026' should yield 2026-02-22."""
    b = _meta_block(["Document Number: P3181R1", "Date: February 22, 2026"])
    meta, _ = extract_metadata_from_blocks([b])
    assert meta.get("date") == "2026-02-22"


def test_extracts_natural_date_march():
    """PDF with 'Date: March 26, 2026' should yield 2026-03-26."""
    b = _meta_block(["Document Number: P3692R4", "Date: March 26, 2026"])
    meta, _ = extract_metadata_from_blocks([b])
    assert meta.get("date") == "2026-03-26"


def test_extracts_abbreviated_date():
    """PDF with 'Date: Feb 22, 2026' should yield 2026-02-22."""
    b = _meta_block(["Document Number: P3181R1", "Date: Feb 22, 2026"])
    meta, _ = extract_metadata_from_blocks([b])
    assert meta.get("date") == "2026-02-22"


def test_extracts_euro_date():
    """PDF with 'Date: 22 February 2026' should yield 2026-02-22."""
    b = _meta_block(["Document Number: P3181R1", "Date: 22 February 2026"])
    meta, _ = extract_metadata_from_blocks([b])
    assert meta.get("date") == "2026-02-22"


def test_iso_date_still_works():
    """Regression: ISO dates must still be extracted correctly."""
    b = _meta_block(["Document Number: P4003R0", "Date: 2026-01-15"])
    meta, _ = extract_metadata_from_blocks([b])
    assert meta.get("date") == "2026-01-15"
