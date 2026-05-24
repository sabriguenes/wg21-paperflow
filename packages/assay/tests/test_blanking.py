#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

from __future__ import annotations

from unittest.mock import patch

from assay.blanking import blank_paper


def _kept(result: str) -> set[str]:
    """Return the set of non-blank lines from blanked output."""
    return {line.strip() for line in result.splitlines() if line.strip()}


# ---------------------------------------------------------------------------
# YAML frontmatter
# ---------------------------------------------------------------------------


class TestYamlFrontmatter:
    def test_standard_yaml_blanked(self):
        source = (
            "---\n"
            "title: Test\n"
            "---\n"
            "\n"
            "## Introduction\n"
            "\n"
            "Body text.\n"
        )
        result = blank_paper(source)
        kept = _kept(result)
        assert "title: Test" not in kept
        assert "---" not in kept
        assert "## Introduction" in kept
        assert "Body text." in kept

    def test_no_yaml_frontmatter(self):
        source = (
            "## Introduction\n"
            "\n"
            "Body text.\n"
        )
        result = blank_paper(source)
        kept = _kept(result)
        assert "## Introduction" in kept
        assert "Body text." in kept

    def test_content_before_fence(self):
        source = (
            "Some leading text.\n"
            "---\n"
            "title: Test\n"
            "---\n"
            "\n"
            "## Introduction\n"
            "\n"
            "Body text.\n"
        )
        result = blank_paper(source)
        kept = _kept(result)
        assert "Some leading text." in kept
        assert "title: Test" in kept


# ---------------------------------------------------------------------------
# Revision history heading patterns (corpus study opener_counts)
# ---------------------------------------------------------------------------


class TestRevisionHistoryHeadings:
    def _paper_with_revhist(self, heading: str) -> str:
        return (
            f"{heading}\n"
            "\n"
            "- Changed foo to bar.\n"
            "\n"
            "## Design\n"
            "\n"
            "Real content.\n"
        )

    def test_heading_revision_history(self):
        result = blank_paper(self._paper_with_revhist("## Revision History"))
        kept = _kept(result)
        assert "## Revision History" not in kept
        assert "- Changed foo to bar." not in kept
        assert "## Design" in kept
        assert "Real content." in kept

    def test_heading_changelog(self):
        result = blank_paper(self._paper_with_revhist("## Changelog"))
        kept = _kept(result)
        assert "## Changelog" not in kept
        assert "- Changed foo to bar." not in kept
        assert "Real content." in kept

    def test_heading_changes_since(self):
        result = blank_paper(self._paper_with_revhist(
            "## Changes since R3"))
        kept = _kept(result)
        assert "## Changes since R3" not in kept
        assert "Real content." in kept

    def test_heading_rN_entry(self):
        result = blank_paper(self._paper_with_revhist(
            "### R3: May 2026"))
        kept = _kept(result)
        assert "### R3: May 2026" not in kept
        assert "Real content." in kept

    def test_heading_document_history(self):
        result = blank_paper(self._paper_with_revhist(
            "## Document History"))
        kept = _kept(result)
        assert "## Document History" not in kept
        assert "Real content." in kept

    def test_heading_revision_N(self):
        result = blank_paper(self._paper_with_revhist("## Revision 3"))
        kept = _kept(result)
        assert "## Revision 3" not in kept
        assert "Real content." in kept

    def test_heading_changes_in_rN(self):
        result = blank_paper(self._paper_with_revhist(
            "## Changes in R3"))
        kept = _kept(result)
        assert "## Changes in R3" not in kept
        assert "Real content." in kept

    def test_heading_changes_this_revision(self):
        result = blank_paper(self._paper_with_revhist(
            "## Changes in this revision"))
        kept = _kept(result)
        assert "## Changes in this revision" not in kept
        assert "Real content." in kept

    def test_heading_changes_from_previous(self):
        result = blank_paper(self._paper_with_revhist(
            "## Changes from the previous revision"))
        kept = _kept(result)
        assert "## Changes from the previous revision" not in kept
        assert "Real content." in kept

    def test_heading_changes_since_paper_id(self):
        result = blank_paper(self._paper_with_revhist(
            "## Changes since P0432R0"))
        kept = _kept(result)
        assert "## Changes since P0432R0" not in kept
        assert "Real content." in kept

    def test_heading_changes_in_revision_N(self):
        result = blank_paper(self._paper_with_revhist(
            "## Changes in revision 2"))
        kept = _kept(result)
        assert "## Changes in revision 2" not in kept
        assert "Real content." in kept

    def test_heading_changes_in_this_paper(self):
        result = blank_paper(self._paper_with_revhist(
            "## Changes in this paper"))
        kept = _kept(result)
        assert "## Changes in this paper" not in kept
        assert "Real content." in kept


# ---------------------------------------------------------------------------
# Bold revision headings (non-markdown-heading openers)
# ---------------------------------------------------------------------------


class TestBoldRevisionHeadings:
    def _paper_with_bold(self, bold_line: str) -> str:
        return (
            "## Introduction\n"
            "\n"
            "Intro text.\n"
            "\n"
            f"{bold_line}\n"
            "\n"
            "- Changed A to B.\n"
            "\n"
            "## Design\n"
            "\n"
            "Design text.\n"
        )

    def test_bold_revision_history(self):
        result = blank_paper(self._paper_with_bold("**Revision History**"))
        kept = _kept(result)
        assert "**Revision History**" not in kept
        assert "- Changed A to B." not in kept
        assert "## Introduction" in kept
        assert "## Design" in kept

    def test_bold_changelog(self):
        result = blank_paper(self._paper_with_bold("**Changelog**"))
        kept = _kept(result)
        assert "**Changelog**" not in kept
        assert "- Changed A to B." not in kept
        assert "## Design" in kept

    def test_bold_document_history(self):
        result = blank_paper(self._paper_with_bold("**Document history**"))
        kept = _kept(result)
        assert "**Document history**" not in kept
        assert "- Changed A to B." not in kept
        assert "## Design" in kept


# ---------------------------------------------------------------------------
# Revision history section mechanics
# ---------------------------------------------------------------------------


class TestRevisionHistoryBlanking:
    def test_sub_entries_blanked(self):
        source = (
            "## Revision History\n"
            "\n"
            "### R3: May 2026\n"
            "\n"
            "- Added feature X.\n"
            "\n"
            "### R2: April 2026\n"
            "\n"
            "- Initial draft.\n"
            "\n"
            "## Design\n"
            "\n"
            "Real content.\n"
        )
        result = blank_paper(source)
        kept = _kept(result)
        assert "## Revision History" not in kept
        assert "### R3: May 2026" not in kept
        assert "### R2: April 2026" not in kept
        assert "- Added feature X." not in kept
        assert "- Initial draft." not in kept
        assert "## Design" in kept
        assert "Real content." in kept

    def test_at_eof(self):
        source = (
            "## Design\n"
            "\n"
            "Content.\n"
            "\n"
            "## Revision History\n"
            "\n"
            "- Changed things.\n"
        )
        result = blank_paper(source)
        kept = _kept(result)
        assert "## Design" in kept
        assert "Content." in kept
        assert "## Revision History" not in kept
        assert "- Changed things." not in kept

    def test_no_revision_history(self):
        source = (
            "## Abstract\n"
            "\n"
            "Summary.\n"
            "\n"
            "## Design\n"
            "\n"
            "Details.\n"
        )
        result = blank_paper(source)
        kept = _kept(result)
        assert "## Abstract" in kept
        assert "Summary." in kept
        assert "## Design" in kept
        assert "Details." in kept

    def test_numbered_section_prefix(self):
        source = (
            "## 2. Document History\n"
            "\n"
            "- Old change.\n"
            "\n"
            "## 3. Proposal\n"
            "\n"
            "Content.\n"
        )
        result = blank_paper(source)
        kept = _kept(result)
        assert "## 2. Document History" not in kept
        assert "- Old change." not in kept
        assert "## 3. Proposal" in kept

    def test_case_insensitive(self):
        source = (
            "## revision history\n"
            "\n"
            "- Stuff.\n"
            "\n"
            "## Design\n"
            "\n"
            "Content.\n"
        )
        result = blank_paper(source)
        kept = _kept(result)
        assert "## revision history" not in kept
        assert "- Stuff." not in kept
        assert "## Design" in kept

    def test_rN_negative_lookahead(self):
        source = (
            "## R4. Design Rationale\n"
            "\n"
            "This should survive.\n"
        )
        result = blank_paper(source)
        kept = _kept(result)
        assert "## R4. Design Rationale" in kept
        assert "This should survive." in kept

    def test_rN_plain_entry(self):
        source = (
            "## R4\n"
            "\n"
            "This should be blanked.\n"
            "\n"
            "## Conclusion\n"
            "\n"
            "Survives.\n"
        )
        result = blank_paper(source)
        kept = _kept(result)
        assert "## R4" not in kept
        assert "This should be blanked." not in kept
        assert "## Conclusion" in kept
        assert "Survives." in kept


# ---------------------------------------------------------------------------
# Paper-specific overrides
# ---------------------------------------------------------------------------


class TestPaperOverrides:
    SOURCE = (
        "## Legacy Notes\n"
        "\n"
        "- Ancient change.\n"
        "\n"
        "## Design\n"
        "\n"
        "Content.\n"
    )

    @patch.dict("assay.blanking._PAPER_OVERRIDES", {"P9999": {"legacy notes"}})
    def test_override_blanks_with_matching_paper_id(self):
        result = blank_paper(self.SOURCE, paper_id="P9999R1")
        kept = _kept(result)
        assert "## Legacy Notes" not in kept
        assert "- Ancient change." not in kept
        assert "## Design" in kept

    @patch.dict("assay.blanking._PAPER_OVERRIDES", {"P9999": {"legacy notes"}})
    def test_override_skipped_without_paper_id(self):
        result = blank_paper(self.SOURCE)
        kept = _kept(result)
        assert "## Legacy Notes" in kept
        assert "- Ancient change." in kept

    @patch.dict("assay.blanking._PAPER_OVERRIDES", {"P9999": {"legacy notes"}})
    def test_override_skipped_with_different_paper_id(self):
        result = blank_paper(self.SOURCE, paper_id="P1234R0")
        kept = _kept(result)
        assert "## Legacy Notes" in kept
        assert "- Ancient change." in kept


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


class TestReferenceBlanking:
    def _paper_with_refs(self, heading: str, *, at_eof: bool = True) -> str:
        base = (
            "## Design\n"
            "\n"
            "Content.\n"
            "\n"
            f"{heading}\n"
            "\n"
            "- [P1234] Some paper.\n"
        )
        if at_eof:
            return base
        return base + "\n## Appendix\n\nExtra.\n"

    def test_references(self):
        result = blank_paper(self._paper_with_refs("## References"))
        kept = _kept(result)
        assert "## References" not in kept
        assert "- [P1234] Some paper." not in kept
        assert "## Design" in kept

    def test_bibliography(self):
        result = blank_paper(self._paper_with_refs("## Bibliography"))
        kept = _kept(result)
        assert "## Bibliography" not in kept
        assert "- [P1234] Some paper." not in kept

    def test_informative_references(self):
        result = blank_paper(self._paper_with_refs(
            "## Informative References"))
        kept = _kept(result)
        assert "## Informative References" not in kept

    def test_normative_references(self):
        result = blank_paper(self._paper_with_refs(
            "## Normative References"))
        kept = _kept(result)
        assert "## Normative References" not in kept

    def test_roman_numeral_prefix(self):
        result = blank_paper(self._paper_with_refs(
            "## iv. References"))
        kept = _kept(result)
        assert "## iv. References" not in kept

    def test_at_eof(self):
        result = blank_paper(self._paper_with_refs(
            "## References", at_eof=True))
        kept = _kept(result)
        assert "- [P1234] Some paper." not in kept

    def test_with_exit_heading(self):
        result = blank_paper(self._paper_with_refs(
            "## References", at_eof=False))
        kept = _kept(result)
        assert "## References" not in kept
        assert "- [P1234] Some paper." not in kept
        assert "## Appendix" in kept
        assert "Extra." in kept


# ---------------------------------------------------------------------------
# Acknowledgments
# ---------------------------------------------------------------------------


class TestAcknowledgmentBlanking:
    def _paper_with_ack(self, heading: str, *, at_eof: bool = True) -> str:
        base = (
            "## Design\n"
            "\n"
            "Content.\n"
            "\n"
            f"{heading}\n"
            "\n"
            "Thanks to everyone.\n"
        )
        if at_eof:
            return base
        return base + "\n## References\n\n- [P1234] Ref.\n"

    def test_british_plural(self):
        result = blank_paper(self._paper_with_ack("## Acknowledgements"))
        kept = _kept(result)
        assert "## Acknowledgements" not in kept
        assert "Thanks to everyone." not in kept
        assert "## Design" in kept

    def test_american_plural(self):
        result = blank_paper(self._paper_with_ack("## Acknowledgments"))
        kept = _kept(result)
        assert "## Acknowledgments" not in kept
        assert "Thanks to everyone." not in kept

    def test_singular(self):
        result = blank_paper(self._paper_with_ack("## Acknowledgement"))
        kept = _kept(result)
        assert "## Acknowledgement" not in kept
        assert "Thanks to everyone." not in kept

    def test_roman_numeral_prefix(self):
        result = blank_paper(self._paper_with_ack(
            "## iii. Acknowledgements"))
        kept = _kept(result)
        assert "## iii. Acknowledgements" not in kept

    def test_bold_markers(self):
        result = blank_paper(self._paper_with_ack(
            "## *Acknowledgements*"))
        kept = _kept(result)
        assert "## *Acknowledgements*" not in kept

    def test_at_eof(self):
        result = blank_paper(self._paper_with_ack(
            "## Acknowledgements", at_eof=True))
        kept = _kept(result)
        assert "Thanks to everyone." not in kept

    def test_followed_by_section(self):
        result = blank_paper(self._paper_with_ack(
            "## Acknowledgements", at_eof=False))
        kept = _kept(result)
        assert "## Acknowledgements" not in kept
        assert "Thanks to everyone." not in kept
        assert "## References" not in kept
        assert "- [P1234] Ref." not in kept


# ---------------------------------------------------------------------------
# Multi-section interaction
# ---------------------------------------------------------------------------


class TestMultiSectionInteraction:
    def test_typical_order(self):
        source = (
            "## Abstract\n"
            "\n"
            "Summary.\n"
            "\n"
            "## Revision History\n"
            "\n"
            "- Changes.\n"
            "\n"
            "## Design\n"
            "\n"
            "Content.\n"
            "\n"
            "## Acknowledgements\n"
            "\n"
            "Thanks.\n"
            "\n"
            "## References\n"
            "\n"
            "- [P1] Ref.\n"
        )
        result = blank_paper(source)
        kept = _kept(result)
        assert "## Abstract" in kept
        assert "Summary." in kept
        assert "## Revision History" not in kept
        assert "- Changes." not in kept
        assert "## Design" in kept
        assert "Content." in kept
        assert "## Acknowledgements" not in kept
        assert "Thanks." not in kept
        assert "## References" not in kept
        assert "- [P1] Ref." not in kept

    def test_reversed_order(self):
        source = (
            "## Abstract\n"
            "\n"
            "Summary.\n"
            "\n"
            "## References\n"
            "\n"
            "- [P1] Ref.\n"
            "\n"
            "## Acknowledgements\n"
            "\n"
            "Thanks.\n"
            "\n"
            "## Revision History\n"
            "\n"
            "- Changes.\n"
        )
        result = blank_paper(source)
        kept = _kept(result)
        assert "## Abstract" in kept
        assert "Summary." in kept
        assert "## References" not in kept
        assert "## Acknowledgements" not in kept
        assert "## Revision History" not in kept

    def test_split_revision_history(self):
        source = (
            "## Changelog\n"
            "\n"
            "### R2: Latest\n"
            "\n"
            "- Change A.\n"
            "\n"
            "## Acknowledgements\n"
            "\n"
            "Thanks.\n"
            "\n"
            "## Changes since R1\n"
            "\n"
            "- Change B.\n"
            "\n"
            "## Design\n"
            "\n"
            "Content.\n"
        )
        result = blank_paper(source)
        kept = _kept(result)
        assert "## Changelog" not in kept
        assert "### R2: Latest" not in kept
        assert "- Change A." not in kept
        assert "## Acknowledgements" not in kept
        assert "Thanks." not in kept
        assert "## Changes since R1" not in kept
        assert "- Change B." not in kept
        assert "## Design" in kept
        assert "Content." in kept


# ---------------------------------------------------------------------------
# Line preservation invariants
# ---------------------------------------------------------------------------


class TestLinePreservation:
    SOURCE = (
        "---\n"
        "title: Test\n"
        "---\n"
        "\n"
        "## Abstract\n"
        "\n"
        "Summary.\n"
        "\n"
        "## Revision History\n"
        "\n"
        "- Changed X.\n"
        "\n"
        "## Design\n"
        "\n"
        "Content.\n"
        "\n"
        "## Acknowledgements\n"
        "\n"
        "Thanks.\n"
        "\n"
        "## References\n"
        "\n"
        "- [P1] Ref.\n"
    )

    def test_line_count_preserved(self):
        result = blank_paper(self.SOURCE)
        assert len(result.splitlines()) == len(self.SOURCE.splitlines())

    def test_blanked_lines_are_newlines(self):
        result = blank_paper(self.SOURCE)
        for line in result.splitlines(keepends=True):
            if line.strip() == "":
                assert line == "\n"

    def test_surviving_lines_identical(self):
        result = blank_paper(self.SOURCE)
        source_lines = self.SOURCE.splitlines(keepends=True)
        result_lines = result.splitlines(keepends=True)
        for src, res in zip(source_lines, result_lines):
            if res.strip():
                assert res == src
