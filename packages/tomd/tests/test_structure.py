"""Tests for lib.pdf.structure."""

from conftest import make_block, make_section, make_span
from tomd.lib.pdf.types import (
    Block, Line, Span, Section, SectionKind, Confidence,
)
from tomd.lib.pdf.structure import (
    compare_extractions, structure_sections,
    heading_confidence, _extract_metadata,
    _detect_body_size, _validate_nesting,
    _demote_repeated_low_confidence_numbers,
)


class TestHeadingConfidence:
    def test_number_font_bold(self):
        level, conf = heading_confidence(True, 2, 2, True, False)
        assert level == 2
        assert conf == Confidence.HIGH

    def test_number_font_no_bold(self):
        level, conf = heading_confidence(True, 2, 2, False, False)
        assert level == 2
        assert conf == Confidence.MEDIUM

    def test_number_font_disagree(self):
        level, conf = heading_confidence(True, 2, 3, False, False)
        assert level == 2
        assert conf == Confidence.MEDIUM

    def test_number_bold_no_font(self):
        level, conf = heading_confidence(True, 2, None, True, False)
        assert level == 2
        assert conf == Confidence.MEDIUM

    def test_number_alone(self):
        level, conf = heading_confidence(True, 2, None, False, False)
        assert level == 2
        assert conf == Confidence.LOW

    def test_font_known_bold(self):
        level, conf = heading_confidence(False, 0, 1, True, True)
        assert level == 2
        assert conf == Confidence.HIGH

    def test_font_known_no_bold(self):
        level, conf = heading_confidence(False, 0, 1, False, True)
        assert level == 2
        assert conf == Confidence.MEDIUM

    def test_font_bold(self):
        level, conf = heading_confidence(False, 0, 1, True, False)
        assert level == 2
        assert conf == Confidence.MEDIUM

    def test_font_alone(self):
        level, conf = heading_confidence(False, 0, 1, False, False)
        assert level == 2
        assert conf == Confidence.LOW

    def test_known_alone(self):
        level, conf = heading_confidence(False, 0, None, False, True)
        assert level == 2
        assert conf == Confidence.LOW

    def test_nothing(self):
        level, conf = heading_confidence(False, 0, None, False, False)
        assert level == 0
        assert conf == Confidence.UNCERTAIN


class TestExtractionSimilarity:
    def test_identical_text_confident(self):
        m = [make_block(["alpha beta gamma"], page_num=0)]
        s = [make_block(["alpha beta gamma"], page_num=0)]
        sections = compare_extractions(m, s)
        assert all(sec.kind != SectionKind.UNCERTAIN for sec in sections)

    def test_disjoint_text_uncertain(self):
        m = [make_block(["The quick brown fox jumps over the lazy dog and then some more"], page_num=0)]
        s = [make_block(["Completely unrelated text about different topics entirely and more words"], page_num=0)]
        sections = compare_extractions(m, s)
        assert any(sec.kind == SectionKind.UNCERTAIN for sec in sections)

    def test_high_overlap_confident(self):
        shared = "alpha beta gamma delta epsilon zeta eta theta"
        m = [make_block([shared + " iota"], page_num=0)]
        s = [make_block([shared + " kappa"], page_num=0)]
        sections = compare_extractions(m, s)
        assert all(sec.kind != SectionKind.UNCERTAIN for sec in sections)

    def test_both_empty_no_sections(self):
        sections = compare_extractions([], [])
        assert len(sections) == 0

    def test_one_side_empty_short_demoted(self):
        m = [make_block(["short"], page_num=0)]
        sections = compare_extractions(m, [])
        uncertain = [s for s in sections if s.kind == SectionKind.UNCERTAIN]
        assert len(uncertain) == 0


class TestCompareExtractions:
    def test_identical_blocks_confident(self):
        m = [make_block(["hello world"], page_num=0)]
        s = [make_block(["hello world"], page_num=0)]
        sections = compare_extractions(m, s)
        assert all(sec.kind != SectionKind.UNCERTAIN for sec in sections)

    def test_different_blocks_uncertain(self):
        m = [make_block(["The quick brown fox jumps over the lazy dog and then some more words"], page_num=0)]
        s = [make_block(["Completely unrelated text about different topics entirely with enough words here"], page_num=0)]
        sections = compare_extractions(m, s)
        assert any(sec.kind == SectionKind.UNCERTAIN for sec in sections)

    def test_tiny_uncertain_demoted(self):
        m = [make_block(["short"], page_num=0)]
        s = [make_block(["diff"], page_num=0)]
        sections = compare_extractions(m, s)
        uncertain = [s for s in sections if s.kind == SectionKind.UNCERTAIN]
        assert len(uncertain) == 0


class TestCompareExtractionsOrdering:
    """Regression: promoted pages must not break document order."""

    def test_promoted_pages_preserve_order(self):
        """Sections stay in page_num order even when promotions rewrite the list."""
        shared = "alpha beta gamma delta epsilon zeta eta theta iota kappa"
        p0_m = [make_block([shared], page_num=0)]
        p0_s = [make_block([shared], page_num=0)]

        # Page 1: mupdf/spatial have swapped halves so per-page similarity is low
        p1_m = [make_block(["aaa bbb ccc ddd eee fff ggg hhh iii jjj"], page_num=1)]
        p1_s = [make_block(["kkk lll mmm nnn ooo ppp qqq rrr sss ttt"], page_num=1)]

        # Page 2: carries the other half so combined p1+p2 similarity is high
        p2_m = [make_block(["kkk lll mmm nnn ooo ppp qqq rrr sss ttt"], page_num=2)]
        p2_s = [make_block(["aaa bbb ccc ddd eee fff ggg hhh iii jjj"], page_num=2)]

        p3_m = [make_block([shared], page_num=3)]
        p3_s = [make_block([shared], page_num=3)]

        mupdf = p0_m + p1_m + p2_m + p3_m
        spatial = p0_s + p1_s + p2_s + p3_s

        sections = compare_extractions(mupdf, spatial)
        page_nums = [s.page_num for s in sections]
        assert page_nums == sorted(page_nums), (
            f"Sections out of order after promotion: {page_nums}"
        )


class TestParagraphMerging:
    def test_merges_continuation(self):
        sections = [
            make_section("Some text without terminal"),
            make_section("continuation here"),
        ]
        _, result, _ = structure_sections(sections, has_title=True)
        paragraphs = [s for s in result if s.kind == SectionKind.PARAGRAPH]
        assert len(paragraphs) == 1
        assert "continuation" in paragraphs[0].text

    def test_no_merge_with_terminal(self):
        sections = [
            make_section("Some text with terminal."),
            make_section("Next paragraph."),
        ]
        _, result, _ = structure_sections(sections, has_title=True)
        paragraphs = [s for s in result if s.kind == SectionKind.PARAGRAPH]
        assert len(paragraphs) == 2

    def test_merge_preserves_original_input(self):
        s1 = make_section("Some text without terminal")
        s2 = make_section("continuation here")
        original_text = s1.text
        structure_sections([s1, s2], has_title=True)
        assert s1.text == original_text


class TestBodySizeDetection:
    def test_larger_font_detected_as_heading(self):
        sections = [
            make_section("body text", font_size=10.0),
            make_section("more body text", font_size=10.0),
            make_section("A Heading", font_size=14.0),
        ]
        _, result, _ = structure_sections(sections, has_title=True)
        headings = [s for s in result if s.kind == SectionKind.HEADING]
        assert len(headings) >= 1

    def test_uniform_font_no_headings(self):
        sections = [
            make_section("body text", font_size=10.0),
            make_section("more body text", font_size=10.0),
            make_section("still body text", font_size=10.0),
        ]
        _, result, _ = structure_sections(sections, has_title=True)
        headings = [s for s in result if s.kind == SectionKind.HEADING]
        assert len(headings) == 0


class TestExtractMetadataKey:
    def test_document_number_produces_document_key(self):
        """Regression: _extract_metadata must write 'document', not 'doc-number'."""
        sections = [
            make_section("Document Number: P1234R0"),
            make_section("Some body text here."),
        ]
        meta, _, _ = structure_sections(sections, has_title=True)
        assert "document" in meta or meta == {}
        assert "doc-number" not in meta

    def test_document_key_not_doc_number(self):
        """The merged front matter must never contain the legacy doc-number key."""
        sec = make_section("Document Number: P9999R2")
        meta, _, _ = structure_sections([sec], has_title=True)
        assert "doc-number" not in meta


class TestExtractMetadataMutation:
    def test_does_not_mutate_input_sections(self):
        """Regression: _extract_metadata must not mutate its input Sections.

        Callers rely on helpers in this module producing new objects,
        consistent with _merge_paragraphs.
        """
        sec = make_section(
            "Document Number: P1234R0\nSome leftover\nBody content",
            kind=SectionKind.PARAGRAPH,
        )
        original_text = sec.text
        _extract_metadata([sec])
        assert sec.text == original_text

    def test_returns_stripped_section_copy(self):
        """The returned section has the metadata lines removed."""
        sec = make_section(
            "Document Number: P1234R0\nSome leftover",
            kind=SectionKind.PARAGRAPH,
        )
        meta, remaining = _extract_metadata([sec])
        assert meta.get("document") == "P1234R0"
        assert len(remaining) == 1
        assert "Document Number" not in remaining[0].text
        assert "Some leftover" in remaining[0].text


class TestCodeBlockUncertainMerge:
    """Uncertain sections that are all-monospace bridge consecutive code runs."""

    def _mono_section(self, text: str, kind=SectionKind.PARAGRAPH) -> Section:
        span = Span(text=text, monospace=True)
        line = Line(spans=[span])
        return Section(kind=kind, text=text, lines=[line],
                       confidence=Confidence.HIGH)

    def test_uncertain_mono_between_code_sections_merged(self):
        """An all-monospace UNCERTAIN section between two code runs is absorbed."""
        top = self._mono_section("void f() {")
        mid = self._mono_section("    return 0;", kind=SectionKind.UNCERTAIN)
        mid.confidence = Confidence.UNCERTAIN
        bot = self._mono_section("}")

        _, sections, _ = structure_sections([top, mid, bot], has_title=False)
        code = [s for s in sections if s.kind == SectionKind.CODE]
        assert len(code) == 1, "expected one merged code block"
        assert "void f()" in code[0].text
        assert "return 0" in code[0].text
        assert "}" in code[0].text

    def test_uncertain_non_mono_between_code_sections_not_merged(self):
        """An UNCERTAIN section with mixed content breaks the code run."""
        top = self._mono_section("void f() {")
        mid_span = Span(text="prose text", monospace=False)
        mid = Section(kind=SectionKind.UNCERTAIN, text="prose text",
                      lines=[Line(spans=[mid_span])],
                      confidence=Confidence.UNCERTAIN)
        bot = self._mono_section("}")

        _, sections, _ = structure_sections([top, mid, bot], has_title=False)
        code = [s for s in sections if s.kind == SectionKind.CODE]
        assert len(code) == 2, "mixed uncertain should not be absorbed"


class TestBlockFontSize:
    def test_line_count_voting(self):
        """Block.font_size uses line-count voting, not character weighting."""
        from conftest import make_span
        block = Block(lines=[
            Line(spans=[make_span(
                "word word word word word word word word", font_size=11.0)]),
            Line(spans=[make_span("short", font_size=14.0)]),
            Line(spans=[make_span("short", font_size=14.0)]),
        ])
        # Lines: two at 14, one at 11 -> 14 wins by line count.
        # Character count would favor 11.
        assert block.font_size == 14.0


# ---------------------------------------------------------------------------
# Regression coverage for PR #9 fixes.
# ---------------------------------------------------------------------------


def _mk_section(text, *, font_size=10.0, monospace=False, bold=False,
                kind=SectionKind.PARAGRAPH,
                confidence=Confidence.HIGH, heading_level=0):
    """Build a Section whose single line carries explicit span attributes."""
    span = make_span(text, font_size=font_size,
                      monospace=monospace, bold=bold)
    line = Line(spans=[span])
    return Section(kind=kind, text=text, confidence=confidence,
                   heading_level=heading_level, lines=[line],
                   font_size=font_size)


class TestDetectBodySizeProsePreference:
    """`_detect_body_size` prefers prose over monospace on code-heavy papers."""

    def test_prose_wins_over_monospace_majority(self):
        """Prose beats a more frequent monospace size when it clears the floor."""
        prose = [_mk_section("prose line " + ("x" * 60), font_size=11.0)
                 for _ in range(10)]
        code = [_mk_section("code line " + ("y" * 60), font_size=9.0,
                             monospace=True) for _ in range(30)]
        body = _detect_body_size(prose + code)
        assert body == 11.0, (
            "prose font should be picked as body even when monospace spans "
            "hold more characters overall"
        )

    def test_fallback_to_all_when_prose_too_small(self):
        """When prose is scarce, the overall most-common size wins (wording papers)."""
        tiny_prose = [_mk_section("hi", font_size=11.0)]  # <<500 chars
        code = [_mk_section("code " + ("y" * 200), font_size=9.0,
                             monospace=True) for _ in range(10)]
        body = _detect_body_size(tiny_prose + code)
        assert body == 9.0, (
            "with insufficient prose, body falls back to the most common size"
        )

    def test_empty_sections_fall_back(self):
        """No data at all returns the FALLBACK_BODY_SIZE."""
        from tomd.lib.pdf.types import FALLBACK_BODY_SIZE
        assert _detect_body_size([]) == FALLBACK_BODY_SIZE


class TestHeadingProseLengthRejection:
    """Long numbered first lines don't become headings at LOW confidence."""

    def test_long_numbered_line_demoted(self):
        """A numbered prose line with >12 words and no font/bold signal is a paragraph."""
        long_line = ("1 A fiber is a single flow of control with a "
                     "private stack and an associated execution context.")
        sec = _mk_section(long_line, font_size=10.0)
        # Flank it with body text at the same size so `_detect_body_size`
        # agrees that 10.0 is body and no font-level signal fires.
        body_fill = [_mk_section("ordinary body " + ("x" * 80), font_size=10.0)
                     for _ in range(10)]
        _, result, _ = structure_sections(body_fill + [sec], has_title=True)
        demoted = [s for s in result if long_line in s.text]
        assert demoted, "expected the long numbered line to appear in output"
        assert all(s.kind != SectionKind.HEADING for s in demoted), (
            "prose-length first line should not become a heading at LOW conf"
        )

    def test_long_numbered_line_kept_with_font_signal(self):
        """A long numbered line at a heading font size is preserved as a heading."""
        long_title = ("1 A fiber is a single flow of control with a "
                      "private stack and an associated execution context")
        heading = _mk_section(long_title, font_size=14.0)
        body_fill = [_mk_section("plain body " + ("x" * 80), font_size=10.0)
                     for _ in range(10)]
        _, result, _ = structure_sections(body_fill + [heading], has_title=True)
        matches = [s for s in result if long_title in s.text]
        assert matches and any(s.kind == SectionKind.HEADING for s in matches), (
            "long numbered line at heading font size must stay a HEADING "
            "(MEDIUM or HIGH confidence survives the length cap)"
        )


class TestGlyphBulletPrefixLine:
    """Sections whose first line is a bullet character plus an injected
    glyph placeholder (canonical N5007 page 11 Profiles case) must stay
    candidates for list-item merging, not become headings, even when
    the synthetic sentinel span's bbox-derived font_size triggers
    ``heading_confidence``'s font-rank branch.
    """

    @staticmethod
    def _bullet_then_body_section(*, body_size=10.5, sentinel_size=12.75):
        """Two-line section: line 1 is "●" + sentinel "�", line 2 is body text.

        Mirrors N5007 page 11's Profiles bullets, where the PDF
        renders ``●`` as text and overlays a small raster emoji that
        the glyph pass replaces with ``UNKNOWN_GLYPH``. The sentinel
        carries a slightly larger ``font_size`` than body, which is
        what previously promoted the line to a heading.
        """
        from tomd.lib.pdf.glyphs import GLYPH_FONT_SENTINEL, UNKNOWN_GLYPH

        bullet_line = Line(spans=[
            make_span("●", font_size=body_size),
            make_span(UNKNOWN_GLYPH, font_size=sentinel_size,
                      font_name=GLYPH_FONT_SENTINEL),
        ])
        body_line = Line(spans=[
            make_span(" P3589 — C++ Profiles: The Framework",
                      font_size=body_size),
        ])
        text = "●" + UNKNOWN_GLYPH + "\n P3589 — C++ Profiles: The Framework"
        # font_size on Section is taken from the sentinel-bearing line in
        # the real pipeline (`_compute_section_font_size` uses span max);
        # match that here so the heading-detection font_level branch is
        # exercised.
        return Section(
            kind=SectionKind.PARAGRAPH, text=text,
            confidence=Confidence.HIGH, heading_level=0,
            lines=[bullet_line, body_line], font_size=sentinel_size,
        )

    def test_bullet_plus_sentinel_first_line_not_a_heading(self):
        """A bullet-then-sentinel first line keeps the section as a
        PARAGRAPH so downstream list detection can merge bullet + body.
        Without this guard, the synthetic span's larger font_size would
        trip heading_confidence and promote the section to a heading -
        the canonical N5007 Profiles regression.
        """
        # Body context big enough to compute body size and font ranks.
        body_fill = [_mk_section("ordinary body " + ("x" * 80), font_size=10.5)
                     for _ in range(10)]
        sec = self._bullet_then_body_section()
        _, result, _ = structure_sections(body_fill + [sec], has_title=True)
        # Find the section whose text starts with the bullet character.
        matches = [s for s in result if s.text.startswith("●")
                   or "P3589" in s.text]
        assert matches, "expected the bullet+body section in the output"
        assert all(s.kind != SectionKind.HEADING for s in matches), (
            "bullet+sentinel first line must not promote section to HEADING"
        )

    def test_bullet_plus_sentinel_keeps_body_text(self):
        """The paper-title body line must survive the structural pass,
        not be orphaned by a mis-classified heading dropping its tail.
        """
        body_fill = [_mk_section("ordinary body " + ("x" * 80), font_size=10.5)
                     for _ in range(10)]
        sec = self._bullet_then_body_section()
        _, result, _ = structure_sections(body_fill + [sec], has_title=True)
        joined = "\n".join(s.text for s in result)
        assert "P3589" in joined, (
            "paper-title body line must survive; was lost when bullet "
            "line was mis-classified as a heading"
        )


class TestDemoteRepeatedLowConfidenceNumbers:
    """Paragraph-number resets collapse to PARAGRAPH; TOC/body pairs do not."""

    def _heading(self, num, text, *, confidence=Confidence.LOW):
        return _mk_section(f"{num} {text}", font_size=10.0,
                            kind=SectionKind.HEADING,
                            confidence=confidence, heading_level=2)

    def test_three_repeats_demoted(self):
        """section_num repeating >=3 times at LOW confidence becomes PARAGRAPH."""
        sections = [
            self._heading("1", "Constraints: first"),
            self._heading("2", "Mandates: one"),
            self._heading("1", "Preconditions: second"),
            self._heading("1", "Effects: third"),
        ]
        _demote_repeated_low_confidence_numbers(sections)
        ones = [s for s in sections if s.text.startswith("1 ")]
        assert all(s.kind == SectionKind.PARAGRAPH for s in ones), (
            "three or more occurrences of number '1' at LOW conf should demote"
        )
        assert all(s.heading_level == 0 for s in ones)

    def test_two_repeats_preserved(self):
        """A TOC/body pair (count == 2) is NOT demoted."""
        sections = [
            self._heading("1", "Introduction"),
            self._heading("1", "Introduction"),  # second copy (body)
        ]
        _demote_repeated_low_confidence_numbers(sections)
        assert all(s.kind == SectionKind.HEADING for s in sections), (
            "pair-count (TOC + body) should be left alone"
        )

    def test_medium_confidence_not_demoted(self):
        """MEDIUM/HIGH confidence headings are never touched, even if they repeat."""
        sections = [
            self._heading("1", "a", confidence=Confidence.MEDIUM),
            self._heading("1", "b", confidence=Confidence.MEDIUM),
            self._heading("1", "c", confidence=Confidence.MEDIUM),
        ]
        _demote_repeated_low_confidence_numbers(sections)
        assert all(s.kind == SectionKind.HEADING for s in sections)

    def test_demoted_confidence_left_low(self):
        """Pins current behavior: demoted sections keep Confidence.LOW.

        Not currently observed as a problem; documented in
        issues/pr9-review.md. Any change to the demotion path should
        consciously revisit this pin.
        """
        sections = [
            self._heading("1", "first"),
            self._heading("1", "second"),
            self._heading("1", "third"),
        ]
        _demote_repeated_low_confidence_numbers(sections)
        assert all(s.confidence == Confidence.LOW for s in sections), (
            "demoted paragraphs currently retain their heading's LOW "
            "confidence; update issues/pr9-review.md if this changes"
        )


class TestValidateNestingSiblingClamp:
    """Same-font-size consecutive headings are treated as siblings."""

    def _h(self, text, *, level, fs):
        return _mk_section(text, font_size=fs,
                            kind=SectionKind.HEADING,
                            confidence=Confidence.HIGH,
                            heading_level=level)

    def test_sibling_run_stays_flat(self):
        """A long run of same-font revision headings doesn't cascade."""
        sections = [
            self._h("## Changes", level=2, fs=14.0),
            self._h("### Changes since P21", level=3, fs=12.0),
            # Each of the following originally got level = prev_clamped + 1.
            # Sibling logic pins them all to level 3.
            self._h("#### Changes since P20", level=4, fs=12.0),
            self._h("##### Changes since P19", level=5, fs=12.0),
            self._h("###### Changes since P18", level=6, fs=12.0),
        ]
        _validate_nesting(sections)
        levels = [s.heading_level for s in sections]
        assert levels == [2, 3, 3, 3, 3], (
            f"expected runs of same-font siblings at level 3; got {levels}"
        )

    def test_sibling_downgrades_high_to_medium(self):
        """When the sibling rule fires, HIGH confidence drops to MEDIUM."""
        sections = [
            self._h("## Root", level=2, fs=14.0),
            self._h("### Child", level=3, fs=12.0),
            self._h("#### Grandchild", level=4, fs=12.0),
        ]
        _validate_nesting(sections)
        gc = sections[-1]
        assert gc.heading_level == 3
        assert gc.confidence == Confidence.MEDIUM

    def test_different_font_allows_nesting(self):
        """Truly different font sizes still pass through the skip-level rule."""
        sections = [
            self._h("## Root", level=2, fs=14.0),
            self._h("### Child", level=3, fs=12.0),
            self._h("#### Grandchild", level=4, fs=10.0),
        ]
        _validate_nesting(sections)
        assert [s.heading_level for s in sections] == [2, 3, 4]

    def test_current_behavior_flattens_legit_nesting_same_font(self):
        """Pins the risk documented in issues/pr9-review.md.

        Papers that express depth through section numbering while using
        one font size for all sub-levels will have legitimate h4s clamped
        to h3. This test encodes the CURRENT behavior so that any future
        change (e.g. letting section-number depth veto the sibling rule)
        is noticed in review.
        """
        sections = [
            self._h("## 2 Motivation", level=2, fs=14.0),
            self._h("### 2.1 Background", level=3, fs=12.0),
            self._h("#### 2.1.1 History", level=4, fs=12.0),
        ]
        _validate_nesting(sections)
        assert sections[-1].heading_level == 3, (
            "currently flattens 2.1.1 to h3; update issues/pr9-review.md "
            "if section-number depth is made to veto the sibling rule"
        )

    def test_tight_font_tolerance_misses_fractional_variance(self):
        """Pins the risk documented in issues/pr9-review.md.

        `_SIBLING_FONT_TOL = 0.1` rejects sibling status between 11.7 and
        12.0 (diff 0.3), which is within the fractional variance common
        in LaTeX PDFs. Encodes CURRENT behavior.
        """
        sections = [
            self._h("## Root", level=2, fs=14.0),
            self._h("### Sibling A", level=3, fs=12.0),
            # fs = 11.7 is > 0.1 away from 12.0, so NOT a sibling;
            # skip-level rule clamps level 5 -> 4 (prev + 1).
            self._h("##### Sibling B", level=5, fs=11.7),
        ]
        _validate_nesting(sections)
        assert sections[-1].heading_level == 4, (
            "current absolute 0.1 tolerance treats 11.7 and 12.0 as "
            "different tiers; widening the tolerance would change this "
            "assertion — see issues/pr9-review.md"
        )

    def test_close_fractional_is_sibling(self):
        """Within the 0.1 tolerance, sizes like 11.95 vs 12.0 are siblings."""
        sections = [
            self._h("## Root", level=2, fs=14.0),
            self._h("### Sibling A", level=3, fs=12.0),
            self._h("#### Sibling B", level=4, fs=11.95),
        ]
        _validate_nesting(sections)
        assert sections[-1].heading_level == 3
