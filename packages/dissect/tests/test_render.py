#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Tests for rendering functions."""

from __future__ import annotations

from dissect.models import (
    Claim,
    ClaimVerdict,
    Evidence,
    PipelineState,
    SentenceSpan,
    SentenceTag,
    SourceLoc,
    TaggedSentence,
)
from paperstore.backend import PaperRow
from dissect.render import (
    render_debug_tag_sentences,
    render_report,
    render_trace,
)
from pipeline import sanitize_md


def _loc(line=1, start=0, end=10):
    return SourceLoc(line=line, start_char=start, end_char=end)


def test_sanitize_md_balanced_code_span_preserved():
    assert sanitize_md("`std::vector`") == "`std::vector`"


def test_sanitize_md_bare_angle_brackets_escaped():
    assert sanitize_md("vector<int>") == r"vector\<int\>"


def test_sanitize_md_pipes_escaped():
    assert sanitize_md("a | b") == r"a \| b"


def test_sanitize_md_leading_hash_escaped():
    assert sanitize_md("# heading") == r"\# heading"


def test_sanitize_md_unbalanced_asterisk_escaped():
    assert sanitize_md("one * here") == r"one \* here"


def test_sanitize_md_balanced_bold_preserved():
    assert sanitize_md("**bold**") == "**bold**"


def test_sanitize_md_mixed_code_span_and_prose():
    result = sanitize_md("use `std::vector<int>` for this")
    assert "`std::vector<int>`" in result
    assert r"\<" not in result.split("`")[2]


def test_render_report_unsupported():
    state = PipelineState(
        normative_claims=[
            Claim(uid=1, loc=_loc(1), text="X is fast", original_quotes=["X is fast"],
                  section="3", question="How fast?", depends_on=[]),
        ],
        verdicts=[
            ClaimVerdict(claim_uid=1, status="unproven"),
        ],
    )
    report = render_report(state, "P0001R0", "Test Paper")
    assert "Unsupported Claims" in report
    assert "How fast?" in report


def test_render_report_supported():
    state = PipelineState(
        normative_claims=[
            Claim(uid=1, loc=_loc(1), text="X is fast", original_quotes=["X is fast"],
                  section="3", question="How fast?", depends_on=[]),
        ],
        deduped_evidence=[
            Evidence(uid=2, loc=_loc(2), text="measured 5ns", original_quotes=["measured 5ns"],
                     section="4", supports=["X is fast"], quantitative=True,
                     cited=False, verifiable=True, normative=False),
        ],
        verdicts=[
            ClaimVerdict(claim_uid=1, related_uid=2, status="proven"),
        ],
    )
    report = render_report(state, "P0001R0", "Test Paper")
    assert "Supported Claims" in report
    assert "How fast?" in report
    assert "measured 5ns" in report


def test_render_report_empty():
    state = PipelineState(normative_claims=[], verdicts=[])
    report = render_report(state, "P0001R0", "Test")
    assert "None identified" in report


def test_render_trace_step0():
    state = PipelineState(
        chunks=[],
        citations=[],
    )
    trace = render_trace(state, PaperRow(title="T", paper_id="P0001R0"), 0)
    assert "0. Read" in trace


def _tagged(line: int, tag: SentenceTag, t: float = 0.9, s: float = 0.05) -> TaggedSentence:
    return TaggedSentence(
        span=SentenceSpan(text=f"sent at {line}", line=line, start_char=0, end_char=20),
        tag=tag,
        target_score=t,
        skip_score=s,
    )


def test_render_trace_step1_summary_when_tagged_sentences_present():
    """Trace renders a counts summary at stop_step >= 1, never per-sentence."""
    state = PipelineState(
        chunks=[],
        citations=[],
        tagged_sentences=[
            _tagged(1, SentenceTag.TARGET),
            _tagged(2, SentenceTag.TARGET),
            _tagged(3, SentenceTag.CONTEXT),
            _tagged(4, SentenceTag.SKIP),
        ],
    )
    trace = render_trace(state, PaperRow(title="T", paper_id="P0001R0"), 1)
    assert "## 1. Tag Sentences" in trace
    assert "4 sentences classified" in trace
    assert "2 target" in trace
    assert "1 context" in trace
    assert "1 skip" in trace
    # The summary mentions the asymmetric decision rule (target / skip margins).
    assert "TARGET" in trace and "SKIP" in trace
    assert "CONTEXT" in trace
    # Trace must NOT dump individual sentence text.
    assert "sent at 1" not in trace
    assert "sent at 2" not in trace


def test_render_trace_step1_omitted_when_tagged_sentences_none():
    """If Step 1 was skipped (no classifier), no Tag Sentences block."""
    state = PipelineState(chunks=[], citations=[], tagged_sentences=None)
    trace = render_trace(state, PaperRow(title="T", paper_id="P0001R0"), 1)
    assert "## 1. Tag Sentences" not in trace


def test_render_debug_tag_sentences_header_and_table():
    tagged = [
        _tagged(42, SentenceTag.TARGET, t=0.832, s=0.041),
        _tagged(43, SentenceTag.SKIP, t=0.024, s=0.881),
    ]
    out = render_debug_tag_sentences(
        tagged,
        classifier_name="zeroshot-large",
        classifier_model="MoritzLaurer/deberta-v3-large-zeroshot-v2.0",
        device="cpu",
        target_label="This text argues...",
        skip_label="This text is filler",
        target_margin=0.05,
        skip_margin=0.40,
        multi_label=True,
    )
    assert "## 1. Tag Sentences" in out
    assert "zeroshot-large" in out
    assert "deberta-v3-large" in out
    assert "multi_label=True" in out
    assert "0.05" in out
    assert "0.4" in out
    assert "TARGET" in out
    assert "SKIP" in out
    # Table row content present.
    assert "| 42 | TARGET |" in out
    assert "| 43 | SKIP |" in out
    assert "0.832" in out


def test_render_debug_tag_sentences_escapes_pipes_in_text():
    tagged = [TaggedSentence(
        span=SentenceSpan(text="a | b is a thing", line=1, start_char=0, end_char=15),
        tag=SentenceTag.TARGET,
        target_score=0.9,
        skip_score=0.1,
    )]
    out = render_debug_tag_sentences(
        tagged, classifier_name="x", classifier_model="x", device="cpu",
        target_label="t", skip_label="s",
        target_margin=0.05, skip_margin=0.40, multi_label=True,
    )
    assert "a \\| b is a thing" in out


def test_render_trace_step7():
    """Steps 1-16 are the renumbered pipeline (was Steps 1-15 before
    the Tag Sentences step was inserted)."""
    state = PipelineState(
        chunks=[],
        citations=[],
        raw_claims=[],
        normative_claims=[],
        raw_evidence=[],
        raw_factual=[],
        deduped_evidence=[],
        verdicts=[],
    )
    trace = render_trace(state, PaperRow(title="T", paper_id="P0001R0"), 7)
    assert "0. Read" in trace
    assert "2. Extract Claims" in trace
    assert "4. Extract Evidence" in trace
    assert "6. Extract Factual" in trace
    assert "7. Dedup Factual" in trace
