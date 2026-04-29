"""Tests for lib.pdf.qa (Markdown-based QA scoring)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tomd.lib.pdf.qa import compute_metrics


_GOOD_MD = """\
---
title: "Test Paper"
document: P1234R0
date: 2025-01-01
audience: LEWG
reply-to:
  - "Author Name <author@example.com>"
---

## 1 Introduction

Some introductory text about the paper.

## 2 Motivation

More text explaining motivation.

```cpp
void foo() {
    return;
}
```

- item one
- item two
- item three

## 3 Conclusion

Final paragraph.
"""


class TestPerfectScore:
    def test_good_markdown_scores_100(self):
        m = compute_metrics(_GOOD_MD, file="test.pdf")
        assert m.score == 100, f"expected 100, got {m.score}: {m.issues}"

    def test_heading_count(self):
        m = compute_metrics(_GOOD_MD)
        assert m.heading_count == 3

    def test_code_block_count(self):
        m = compute_metrics(_GOOD_MD)
        assert m.code_block_count == 1

    def test_front_matter_count(self):
        m = compute_metrics(_GOOD_MD)
        assert m.front_matter_count == 5


class TestEmptyOutput:
    def test_empty_string_scores_zero(self):
        m = compute_metrics("")
        assert m.score == 0
        assert "empty output" in m.issues

    def test_whitespace_only_scores_zero(self):
        m = compute_metrics("   \n\n  ")
        assert m.score == 0


class TestUncertainRegions:
    def test_uncertain_markers_penalized(self):
        md = "## Heading\n\n<!-- tomd:uncertain:L5-L10 -->\n\nSome text.\n"
        m = compute_metrics(md)
        assert m.uncertain_count == 1
        assert m.score < 100

    def test_many_uncertain_regions(self):
        markers = "\n".join(
            f"<!-- tomd:uncertain:L{i}-L{i+5} -->\ntext\n"
            for i in range(10)
        )
        md = f"## Heading\n\n{markers}\n"
        m = compute_metrics(md)
        assert m.uncertain_count == 10
        assert m.score <= 80, "uncertain penalty capped at 20"


class TestLossyTableCount:
    def test_lossy_table_marker_counted(self):
        md = "## Heading\n\n<!-- tomd:lossy-table -->\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n"
        m = compute_metrics(md)
        assert m.lossy_table_count == 1
        assert any("lossy" in i for i in m.issues)

    def test_no_marker_zero_count(self):
        md = "## Heading\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n"
        m = compute_metrics(md)
        assert m.lossy_table_count == 0

    def test_multiple_markers(self):
        md = (
            "## Heading\n\n"
            "<!-- tomd:lossy-table -->\n\n| A | B |\n| --- | --- |\n\n"
            "<!-- tomd:lossy-table -->\n\n| C | D |\n| --- | --- |\n"
        )
        m = compute_metrics(md)
        assert m.lossy_table_count == 2

    def test_lossy_table_no_score_penalty(self):
        """Lossy tables are informational, not penalized."""
        md = "## Heading\n\n<!-- tomd:lossy-table -->\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n"
        m = compute_metrics(md)
        assert m.score == 100


class TestNoHeadings:
    def test_no_headings_long_doc_penalized(self):
        """A document with 10+ paragraphs and zero headings is penalized."""
        paras = "\n\n".join(f"Paragraph number {i} with some content." for i in range(15))
        m = compute_metrics(paras)
        assert m.heading_count == 0
        assert m.score < 80
        assert any("no headings" in i for i in m.issues)

    def test_no_headings_short_doc_ok(self):
        """A short document (few paragraphs) with no headings is fine."""
        md = "Just a paragraph.\n\nAnother one.\n"
        m = compute_metrics(md)
        assert not any("no headings" in i for i in m.issues)


class TestUnfencedCode:
    def test_cpp_in_paragraphs_penalized(self):
        lines = [
            "## Heading\n",
            "template<class T> void foo();",
            "#include <vector>",
            "auto x = std::move(y);",
            "int main() {",
            "  return 0;",
            "}",
            "void bar();",
        ]
        md = "\n\n".join(lines) + "\n"
        m = compute_metrics(md)
        assert m.unfenced_code_lines > 5
        assert m.score < 100

    def test_inline_code_not_penalized(self):
        md = "## Heading\n\nUse `std::vector` and `nullptr` in your code.\n"
        m = compute_metrics(md)
        assert m.unfenced_code_lines == 0

    def test_unfenced_penalty_amplified_when_no_code_blocks(self):
        """Catastrophic failure (0 code blocks, 25+ unfenced) gets amplified penalty."""
        lines = [f"void func_{i}();" for i in range(30)]
        md = "## H\n\n" + "\n\n".join(lines) + "\n"
        m = compute_metrics(md)
        assert m.code_block_count == 0
        assert m.unfenced_code_lines > 20
        assert m.score <= 70, f"expected <=70 (amplified), got {m.score}"

    def test_type_declarations_detected(self):
        md = "## H\n\n" + "\n\n".join([
            "namespace std {",
            "class Widget {",
            "struct Point {",
            "enum Color {",
            "namespace detail {",
            "class Derived : public Base {",
        ]) + "\n"
        m = compute_metrics(md)
        assert m.unfenced_code_lines >= 6

    def test_prose_not_false_positive(self):
        """Common prose patterns must not be falsely detected as code."""
        md = "## H\n\n" + "\n\n".join([
            "The class should provide a stable interface.",
            "A static analysis of the codebase shows no issues.",
            "The auto industry has adopted new standards.",
            "This constant effort pays off over time.",
            "The struct is defined in the header.",
            "The namespace contains all utilities.",
        ]) + "\n"
        m = compute_metrics(md)
        assert m.unfenced_code_lines == 0

    def test_standardese_labels_not_false_positive(self):
        """ISO C++ normative labels ending with ; are spec wording, not code."""
        md = "## H\n\n" + "\n\n".join([
            "Returns: Equivalent to: return substr(0).compare(str);",
            "Effects: Equivalent to: return basic_string_view<charT>(data(), rlen) == x;",
            "Preconditions: pos <= size();",
            "Postconditions: size() == 0;",
            "Constraints: is_constructible_v<T, Args...>;",
            "Mandates: is_same_v<T, decay_t<T>>;",
            "Throws: Nothing;",
            "Complexity: Constant;",
            "Requires: first <= last;",
            "Synchronization: strongly happens before;",
            "Error conditions: errc::invalid_argument;",
        ]) + "\n"
        m = compute_metrics(md)
        assert m.unfenced_code_lines == 0, (
            f"standardese labels should not be detected as code, "
            f"got {m.unfenced_code_lines}"
        )

    def test_unfenced_penalty_not_amplified_when_code_blocks_exist(self):
        """Partial failure (some code blocks found) keeps normal penalty cap."""
        front = "---\ntitle: T\ndocument: P0001R0\ndate: 2025-01-01\n---\n\n"
        code_block = "```cpp\nint x = 1;\n```"
        lines = [f"void func_{i}();" for i in range(30)]
        md = front + "## H\n\n" + code_block + "\n\n" + "\n\n".join(lines) + "\n"
        m = compute_metrics(md)
        assert m.code_block_count >= 1
        assert m.unfenced_code_lines > 20
        assert m.score == 85, f"expected 85 (normal cap), got {m.score}"


class TestMetadata:
    def test_no_front_matter_long_doc_penalized(self):
        """Zero front matter on a long document is penalized."""
        paras = "\n\n".join(f"Paragraph {i} about something." for i in range(15))
        md = f"## Heading\n\n{paras}\n"
        m = compute_metrics(md)
        assert m.front_matter_count == 0
        assert any("no front matter" in i for i in m.issues)

    def test_no_front_matter_short_doc_ok(self):
        """Zero front matter on a short document is fine."""
        md = "## Heading\n\nSome text here.\n"
        m = compute_metrics(md)
        assert m.front_matter_count == 0
        assert not any("front matter" in i for i in m.issues)

    def test_partial_front_matter_ok(self):
        """Having any front matter fields is acceptable."""
        md = "---\ntitle: Test\n---\n\n## H\n\nText.\n"
        m = compute_metrics(md)
        assert m.front_matter_count >= 1
        assert not any("front matter" in i for i in m.issues)


class TestLowVariety:
    def test_long_doc_only_paragraphs_penalized(self):
        """10+ paragraphs with no headings/code/lists/tables is penalized."""
        paras = "\n\n".join(f"Paragraph {i}." for i in range(15))
        m = compute_metrics(paras)
        assert any("low variety" in i for i in m.issues)

    def test_short_doc_only_paragraphs_ok(self):
        """A short doc with only paragraphs is not penalized for variety."""
        md = "One.\n\nTwo.\n\nThree.\n"
        m = compute_metrics(md)
        assert not any("low variety" in i for i in m.issues)


class TestWordingExemption:
    def test_ins_del_paragraphs_not_counted_as_unfenced(self):
        """Paragraphs with <ins>/<del> tags should not trigger unfenced code."""
        md = (
            "## Wording\n\n"
            "<ins>constexpr void* memcpy(void* s1, const void* s2, size_t n);</ins>\n\n"
            "<ins>constexpr void* memmove(void* s1, const void* s2, size_t n);</ins>\n\n"
            "<del>void* memcpy(void* s1, const void* s2, size_t n);</del>\n\n"
            "<ins>constexpr int strcmp(const char* s1, const char* s2);</ins>\n\n"
            "<ins>constexpr size_t strlen(const char* s);</ins>\n\n"
            "<ins>constexpr void* memset(void* s, int c, size_t n);</ins>\n\n"
            "Some normal text.\n"
        )
        m = compute_metrics(md)
        assert m.unfenced_code_lines == 0

    def test_wording_div_directives_not_counted_as_code(self):
        """:::wording directives must not trigger unfenced code detection."""
        md = (
            "## Wording\n\n"
            ":::wording\n\n"
            ":::wording-add\n\n"
            ":::wording-del\n\n"
            ":::\n\n"
            ":::wording { .add }\n\n"
            ":::wording { .del }\n\n"
            ":::\n"
        )
        m = compute_metrics(md)
        assert m.unfenced_code_lines == 0

    def test_plain_code_still_counted(self):
        """Paragraphs without wording markup that look like code are counted."""
        md = (
            "## Section\n\n"
            "void foo(int x);\n\n"
            "int bar() {\n\n"
            "return 0;\n\n"
            "}\n\n"
            "#include <vector>\n\n"
            "void baz();\n\n"
            "int qux();\n"
        )
        m = compute_metrics(md)
        assert m.unfenced_code_lines > 5


class TestTableDetection:
    def test_pipe_tables_counted(self):
        md = "## Heading\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\nText.\n"
        m = compute_metrics(md)
        assert m.table_count == 1

    def test_no_tables(self):
        md = "## Heading\n\nJust text.\n"
        m = compute_metrics(md)
        assert m.table_count == 0


class TestWordingSectionCount:
    def test_wording_divs_counted(self):
        md = (
            "## Wording\n\n"
            ":::wording\n\nSome text.\n\n:::\n\n"
            ":::wording-add\n\nAdded text.\n\n:::\n"
        )
        m = compute_metrics(md)
        assert m.wording_section_count == 2

    def test_no_wording_divs(self):
        md = "## Heading\n\nJust text.\n"
        m = compute_metrics(md)
        assert m.wording_section_count == 0

    def test_p2583r3_has_wording_sections(self):
        md = (_FIXTURES_DIR / "p2583r3-symmetric-transfer.md").read_text(
            encoding="utf-8"
        )
        m = compute_metrics(md, file="p2583r3")
        assert m.wording_section_count == 10


class TestTableParseErrors:
    def test_consistent_table_no_error(self):
        md = "## H\n\n| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n"
        m = compute_metrics(md)
        assert m.table_parse_errors == 0

    def test_d4036_no_table_errors(self):
        md = (_FIXTURES_DIR / "d4036-why-not-span.md").read_text(encoding="utf-8")
        m = compute_metrics(md, file="d4036")
        assert m.table_parse_errors == 0



class TestMojibake:
    """Mojibake detection: broken encoding, not valid Unicode.

    Uses ftfy.badness() for detection. Research showed custom
    byte-pattern regex produces false positives on multi-byte
    Unicode (diacritics, math symbols, C++ templates).
    See plans/QA-001-extend-qa-scoring.md, Research Finding #1.
    """

    def test_replacement_char_detected(self):
        """U+FFFD always means bytes were irrecoverably lost."""
        md = "## Heading\n\nSome text with \ufffd corrupt char.\n"
        m = compute_metrics(md)
        assert m.mojibake_count >= 1
        assert m.score < 100

    def test_latin1_mojibake_detected(self):
        """UTF-8 decoded as Latin-1 produces sequences like Ã¤ for ä."""
        # "ä" (U+00E4) encoded as UTF-8 is bytes C3 A4.
        # Decoded as Latin-1, those bytes become "Ã¤" (U+00C3 U+00A4).
        mojibake_text = "Ã¤Ã¶Ã¼Ã\u009f"  # mojibake for äöüß
        md = f"## Heading\n\n{mojibake_text}\n"
        m = compute_metrics(md)
        assert m.mojibake_count >= 1
        assert m.score < 100

    def test_valid_umlauts_not_flagged(self):
        """Real ä, ö, ü are valid Unicode, not mojibake."""
        md = "## Heading\n\nBjarne Stroustrup diskutiert über Änderungen.\n"
        m = compute_metrics(md)
        assert m.mojibake_count == 0

    def test_valid_emoji_not_flagged(self):
        """Emoji like ✅ are valid Unicode, not mojibake."""
        md = "## Heading\n\n✅ Done ❌ Failed 🔴 Blocked\n"
        m = compute_metrics(md)
        assert m.mojibake_count == 0

    def test_math_symbols_not_flagged(self):
        """Math symbols (<=, >=, infinity, integral) must not trigger mojibake.

        ftfy classifies these as 'numeric' category which could
        interact with mojibake patterns on long documents.
        See plans/QA-001-extend-qa-scoring.md, Research Finding #5.
        """
        md = "## Heading\n\nFor all x ≤ y where y ≥ 0 and ∫f(x)dx → ∞\n"
        m = compute_metrics(md)
        assert m.mojibake_count == 0

    def test_cpp_template_syntax_not_flagged(self):
        """C++ template syntax like <T>, <string> must not trigger.

        Research showed generic Unicode heuristics false-positive
        on angle brackets in technical text.
        """
        md = (
            "## Heading\n\n"
            "Use `std::vector<T>` and `std::basic_string<char>` in your code.\n"
            "The `std::map<std::string, std::vector<int>>` container.\n"
        )
        m = compute_metrics(md)
        assert m.mojibake_count == 0

    def test_real_corpus_mojibake_pattern(self):
        """3-byte mojibake as seen in P3052R2: â + C1 controls instead of ✅.

        U+2705 (✅) = UTF-8 bytes E2 9C 85. Decoded as Latin-1/cp1252,
        E2 becomes â (U+00E2), 9C/85 become C1 control chars or œ/…
        Confirmed: https://codepoints.net/U+2705?lang=en
        """
        md = "## Heading\n\nTable \u00e2\u009c\u0085 Done \u00e2\u009d\u008c Failed\n"
        m = compute_metrics(md)
        assert m.mojibake_count >= 1
        assert m.score < 100

    def test_replacement_char_in_code_block_suppressed(self):
        """U+FFFD inside fenced code blocks is intentional content, not corruption."""
        md = (
            "## Heading\n\n"
            "```cpp\n"
            'auto s = std::format("{}", p); // s == "\ufffd"\n'
            "```\n"
        )
        m = compute_metrics(md)
        assert m.mojibake_count == 0

    def test_replacement_char_in_inline_code_suppressed(self):
        """U+FFFD inside inline code spans is intentional content, not corruption."""
        md = '## Heading\n\n`static_assert(U"\ufffd")`\n'
        m = compute_metrics(md)
        assert m.mojibake_count == 0

    def test_replacement_char_in_double_backtick_suppressed(self):
        """U+FFFD inside double-backtick inline code is also suppressed."""
        md = '## Heading\n\n``U"\ufffd" == replacement``\n'
        m = compute_metrics(md)
        assert m.mojibake_count == 0

    def test_replacement_char_in_prose_still_detected(self):
        """U+FFFD outside code blocks is still a conversion error."""
        md = "## Heading\n\nSome text with \ufffd in prose.\n"
        m = compute_metrics(md)
        assert m.mojibake_count >= 1

    def test_unicode_topic_paper_suppresses_fffd(self):
        """Papers about Unicode encoding use U+FFFD as subject matter."""
        md = (
            "---\ntitle: Unicode Transcoding\n---\n\n"
            "## Heading\n\n"
            "The replacement character \ufffd is used when decoding fails.\n"
            "Multiple \ufffd\ufffd\ufffd indicate a longer invalid sequence.\n"
        )
        m = compute_metrics(md)
        assert m.mojibake_count == 0, (
            "U+FFFD in Unicode-topic paper should be suppressed"
        )

    def test_non_unicode_topic_still_detects_fffd(self):
        """Papers NOT about Unicode still detect U+FFFD as mojibake."""
        md = (
            "---\ntitle: Coroutine Improvements\n---\n\n"
            "## Heading\n\n"
            "Some text with \ufffd in prose.\n"
        )
        m = compute_metrics(md)
        assert m.mojibake_count >= 1

    def test_unicode_topic_no_title_no_suppression(self):
        """Without front matter title, no topic suppression occurs."""
        md = "## Heading\n\nSome text with \ufffd in prose.\n"
        m = compute_metrics(md)
        assert m.mojibake_count >= 1

    def test_single_unusual_char_not_flagged(self):
        """A single unusual but valid Unicode char should not trigger.

        ftfy.badness() is a raw match counter. Score 1-2 on long text
        is noise from edge-case characters (§, °, œ). We require >= 3.
        Source: https://ftfy.readthedocs.org/en/latest/heuristic.html
        """
        md = "## Heading\n\nThe section sign § appears in legal text.\n"
        m = compute_metrics(md)
        assert m.mojibake_count == 0

    def test_penalty_capped_at_20(self):
        """Mojibake penalty is capped at 20 to avoid dominating the score.

        Penalty capping per category is standard rubric practice.
        Source: https://help.gradescope.com/article/5uxa8ht1a2
        Note: U+FFFD is in ftfy's 'bad' category, so FFFD chars
        inflate both our count and badness(). The cap prevents
        this correlated double-counting from zeroing the score.
        """
        md = "## Heading\n\n" + "\ufffd " * 50 + "\n"
        m = compute_metrics(md)
        assert m.mojibake_count >= 1
        assert m.score >= 80, f"penalty should cap at 20, got score {m.score}"

    def test_good_md_score_unchanged(self):
        """Existing _GOOD_MD fixture must still score 100."""
        m = compute_metrics(_GOOD_MD, file="test.pdf")
        assert m.mojibake_count == 0
        assert m.score == 100, f"expected 100, got {m.score}: {m.issues}"


class TestHeadingSkips:
    """Heading level skip detection, matching markdownlint MD001.

    Only ascending skips are flagged (H2 -> H4). Descending is
    always allowed (H4 -> H2 closes a subsection).
    See plans/QA-001-extend-qa-scoring.md, Research Finding #3.
    """

    def test_skip_detected(self):
        """## followed by #### (skipping ###) is a skip."""
        md = "## Introduction\n\nText.\n\n#### Deep Section\n\nMore text.\n"
        m = compute_metrics(md)
        assert m.heading_level_skips >= 1
        assert m.score < 100

    def test_no_skip_ok(self):
        """## followed by ### is correct, no penalty."""
        md = "## Introduction\n\nText.\n\n### Subsection\n\nMore text.\n"
        m = compute_metrics(md)
        assert m.heading_level_skips == 0

    def test_descending_levels_ok(self):
        """### followed by ## (going back up) is always allowed.

        W3C WAI explicitly permits skipping ranks when closing
        subsections.
        """
        md = "## Part 1\n\n### Detail\n\nText.\n\n## Part 2\n\nMore text.\n"
        m = compute_metrics(md)
        assert m.heading_level_skips == 0

    def test_multiple_skips_counted(self):
        """Each skip is counted independently."""
        md = (
            "## H2\n\nText.\n\n"
            "#### H4\n\nText.\n\n"
            "## H2 again\n\nText.\n\n"
            "##### H5\n\nText.\n"
        )
        m = compute_metrics(md)
        assert m.heading_level_skips == 2

    def test_penalty_capped_at_15(self):
        """Heading skip penalty is capped at 15.

        Note: our implementation does not treat YAML front matter
        title as implicit h1 (unlike markdownlint since fix #1617).
        For WG21 papers this is correct: they always start with ##.
        Source: https://github.com/DavidAnson/markdownlint/issues/1613

        Fixture includes front matter, a list, and a code block to
        avoid triggering unrelated penalties (no front matter, low variety).
        """
        skips = "\n\n".join(
            f"{'#' * (2 + (i % 2) * 2)} H{2 + (i % 2) * 2}\n\nText."
            for i in range(20)
        )
        md = (
            "---\ntitle: Test\ndocument: P0001R0\ndate: 2026-01-01\n"
            "audience: LEWG\n---\n\n"
            f"{skips}\n\n"
            "- item\n\n```cpp\nint x;\n```\n"
        )
        m = compute_metrics(md)
        assert m.heading_level_skips >= 1
        assert m.score >= 85, f"penalty should cap at 15, got score {m.score}"

    def test_good_md_score_unchanged(self):
        """Existing _GOOD_MD fixture must still score 100."""
        m = compute_metrics(_GOOD_MD, file="test.pdf")
        assert m.heading_level_skips == 0
        assert m.score == 100, f"expected 100, got {m.score}: {m.issues}"



_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "reference"


class TestReferenceFixtures:
    """Gold-standard reference fixtures must score 100.

    If a fixture fails, the scorer has a bug, not the fixture.
    Exact metric values are pinned from verified compute_metrics runs.
    """

    def test_d4036_scores_100(self):
        """D4036 (info paper): front matter, headings, tables, code blocks."""
        md = (_FIXTURES_DIR / "d4036-why-not-span.md").read_text(encoding="utf-8")
        m = compute_metrics(md, file="d4036")
        assert m.score == 100, f"expected 100, got {m.score}: {m.issues}"
        assert m.heading_count == 27
        assert m.heading_level_skips == 0
        assert m.table_count == 5
        assert m.code_block_count == 4
        assert m.front_matter_count == 5
        assert m.has_doc_number is True
        assert m.unfenced_code_lines == 0
        assert m.mojibake_count == 0

    def test_p2583r3_scores_100(self):
        """P2583R3 (wording paper): :::wording divs, <ins>/<del>, code blocks."""
        md = (_FIXTURES_DIR / "p2583r3-symmetric-transfer.md").read_text(
            encoding="utf-8"
        )
        m = compute_metrics(md, file="p2583r3")
        assert m.score == 100, f"expected 100, got {m.score}: {m.issues}"
        assert m.unfenced_code_lines == 0, (
            "wording exemption must prevent C++ in <ins>/<del> from counting"
        )
        assert m.heading_count == 60
        assert m.heading_level_skips == 0
        assert m.code_block_count == 20
        assert m.front_matter_count == 5
        assert m.has_doc_number is True
        assert m.mojibake_count == 0
