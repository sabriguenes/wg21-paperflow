#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

from __future__ import annotations

from assay.models import ChunkEntry, FrontMatter
from assay.triage import should_analyze


def _make_chunks(headings: list[str], char_counts: list[int] | None = None) -> list[ChunkEntry]:
    if char_counts is None:
        char_counts = [500] * len(headings)
    return [
        ChunkEntry(
            index=i, heading=h, start_line=i * 10 + 1,
            end_line=(i + 1) * 10, char_count=cc,
        )
        for i, (h, cc) in enumerate(zip(headings, char_counts))
    ]


class TestProposalDetection:
    def test_small_paper_with_abstract(self):
        chunks = _make_chunks(["Abstract", "Design", "Conclusion"])
        result = should_analyze(chunks, None, "x" * 5000)
        assert result.analyze is True
        assert result.paper_type == "proposal"

    def test_intent_ask_always_analyzes(self):
        chunks = _make_chunks(["Introduction"])
        front = FrontMatter(intent="ask", audience=["LEWG"])
        result = should_analyze(chunks, front, "x" * 500_000)
        assert result.analyze is True

    def test_has_motivation_section(self):
        chunks = _make_chunks(["Motivation", "Proposed Changes"])
        result = should_analyze(chunks, None, "x" * 400_000)
        assert result.analyze is True

    def test_has_design_section(self):
        chunks = _make_chunks(["Overview", "Design Principles", "Wording"])
        result = should_analyze(chunks, None, "x" * 400_000)
        assert result.analyze is True

    def test_has_poll_section(self):
        chunks = _make_chunks(["Introduction", "Straw Poll"])
        result = should_analyze(chunks, None, "x" * 400_000)
        assert result.analyze is True

    def test_small_paper_no_headings(self):
        chunks = _make_chunks(["(untitled)"])
        result = should_analyze(chunks, None, "x" * 5000)
        assert result.analyze is True

    def test_p2900r14_like(self):
        headings = ["2.1", "2.2", "3.1 Design Principles", "3.2 Syntax",
                    "3.4 Semantics", "4 Proposed Wording [basic.pre]",
                    "11 Classes [class]", "15 Preprocessing [cpp]"]
        chunks = _make_chunks(headings)
        result = should_analyze(chunks, FrontMatter(audience=["CWG", "LWG"]), "x" * 294_000)
        assert result.analyze is True
        assert result.paper_type == "proposal"


class TestReferenceDocumentSkip:
    def test_large_no_structure(self):
        chunks = _make_chunks(["Section 1", "Section 2", "Section 3"])
        result = should_analyze(chunks, None, "x" * 400_000)
        assert result.analyze is False
        assert result.paper_type == "reference_document"

    def test_large_only_numbered_sections(self):
        chunks = _make_chunks(["6.1 General", "6.2 Scope", "6.3 Definitions"])
        result = should_analyze(chunks, None, "x" * 350_000)
        assert result.analyze is False
        assert result.paper_type == "reference_document"

    def test_just_under_threshold_passes(self):
        chunks = _make_chunks(["Section 1", "Section 2"])
        result = should_analyze(chunks, None, "x" * 299_000)
        assert result.analyze is True


class TestWordingDominantSkip:
    def test_majority_clause_headings(self):
        headings = [
            "Introduction",
            "Header synopsis [exec.syn]",
            "Schedulers [exec.sched]",
            "Senders [exec.snd]",
            "Receivers [exec.rcv]",
            "Operation states [exec.op]",
            "Algorithms [exec.algo]",
            "Utilities [exec.util]",
            "Run loop [exec.run.loop]",
            "Coroutines [exec.coro]",
        ]
        chunks = _make_chunks(headings)
        result = should_analyze(chunks, None, "x" * 400_000)
        assert result.analyze is False
        assert result.paper_type == "wording_dominant"

    def test_wording_dominant_but_has_abstract(self):
        headings = [
            "Abstract",
            "Header synopsis [exec.syn]",
            "Schedulers [exec.sched]",
            "Senders [exec.snd]",
            "Receivers [exec.rcv]",
            "Operation states [exec.op]",
        ]
        chunks = _make_chunks(headings)
        result = should_analyze(chunks, None, "x" * 400_000)
        assert result.analyze is True


class TestStats:
    def test_stats_populated(self):
        chunks = _make_chunks(["Abstract", "Design"])
        result = should_analyze(chunks, FrontMatter(audience=["LEWG"]), "x" * 10_000)
        assert result.stats["total_chars"] == 10_000
        assert result.stats["chunk_count"] == 2
        assert result.stats["audience"] == "LEWG"

    def test_wording_ratio_computed(self):
        headings = ["Intro", "Wording [basic.scope]", "More [expr.prim]"]
        chunks = _make_chunks(headings)
        result = should_analyze(chunks, None, "x" * 5000)
        assert result.stats["wording_ratio"] > 0.5
        assert result.stats["clause_headings"] == 2
