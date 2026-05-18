"""Dual-path comparison, heading intelligence, and document structuring."""

import logging
import re
import unicodedata
from collections import Counter
from dataclasses import replace

from .. import (
    DATE_RE, DEFAULT_FENCE_LANG, SECTION_NUM_PATTERN, SECTION_NUM_PREFIX_RE,
    strip_format_chars,
)
from .types import (
    Block, Line, Span, Section, SectionKind, Confidence,
    SIMILARITY_THRESHOLD, TERMINAL_PUNCTUATION, FALLBACK_BODY_SIZE,
    MIN_UNCERTAIN_WORDS,
    SECTION_NUM_RE,
    BULLET_RE, NUMBERED_LIST_RE, KNOWN_SECTIONS, BULLET_CHARS,
)

_HEADING_SIZE_RATIO = 1.05
_TITLE_SIZE_RATIO = 1.2

# Minimum font-size drop (points) between a heading's first line and a
# subsequent line to trigger a heading/body split.  Calibrated from corpus:
# real heading+body merges show 2+ pt differences; same-level heading
# continuations show 0-1 pt jitter.
_HEADING_BODY_SPLIT_DELTA = 2.0

# Max words in a heading's first physical line. Beyond this the line is
# prose, not a title. Numbered spec clauses ("1 A fiber is a single flow
# of control...") and numeric arithmetic results that happen to start a
# continuation line both trigger SECTION_NUM_RE; this length cap rejects
# them downstream. Real WG21 section titles are 1-8 words.
_HEADING_MAX_WORDS = 12

# Characters that indicate code, not a heading.  Used to reject
# bold-only heading candidates that are really code fragments.
_CODE_CHARS = frozenset("{}();=<>[]")


def _line_color(line) -> int | None:
    """Dominant text color of a line (first non-whitespace span), or None."""
    for span in line.spans:
        if span.text.strip():
            return span.color
    return None


_TITLE_MAX_LENGTH = 120
# Max font-size deviation (fraction) for a subsequent LARGE section to
# be treated as a title continuation rather than a new heading.
# Calibrated from corpus: real continuations are 0-6.3%; non-continuations
# start at 20%+. The 10% threshold sits in the 14-point gap between the
# two populations.
_TITLE_CONT_FONT_TOL = 0.10
_TITLE_CONT_SKIP = frozenset({
    "revisions", "contents", "foreword", "agenda",
    "table of contents", "tony table",
})
_TITLE_CONT_DATE_RE = re.compile(
    r"^\s*(?:january|february|march|april|may|june|july|august|"
    r"september|october|november|december"
    r"|\d{4}[-/]\d{2}|\d{1,2}\s+\w+\s+\d{4})",
    re.IGNORECASE,
)
# Strip leading paper-ID prefix (e.g. "P4016R0 — ...", "Nicolai Josuttis: P3725R2: ...")
# from extracted titles. Only strips when meaningful text remains after removal.
_TITLE_PID_PREFIX_RE = re.compile(
    r"^(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s*:\s*)?"  # optional "Firstname Lastname: "
    r"[PNpn]\d{3,5}[Rr]\d+\s*[\-\u2014:,.]?\s*",
)
_BODY_MARGIN_FRACTION = 0.1
_BULLET_JOIN_MAX_CHARS = 3


_log = logging.getLogger(__name__)

# Intentionally narrower than qa.py's _STRUCTURAL_CODE_RE.
# Here the regex drives the rescue pass (promoting paragraphs to code
# blocks). Broader patterns (template<, namespace/class/struct/enum)
# cause false positives on flattened tables, so they are excluded.
_STRUCTURAL_CODE_RE = re.compile(
    r"^\s*[{}]|"               # standalone brace lines
    r";\s*$|"                  # trailing semicolons
    r"#include\s*<|"           # preprocessor includes
    r"#define\s+\w|"           # preprocessor defines
    r"\w+\s*\([^)]*\)\s*\{|"  # function_name(...) {
    r"\w+\s*\([^)]*\)\s*;|"   # declaration: name(...);
    r"^\s*static_assert\s*\(|" # static_assert(
    r"^\s*//[^/]",             # C++ line comments (not URLs with //)
    re.MULTILINE,
)

_RESCUE_MIN_CODE_LINES = 3


def _make_paragraph_section(block: Block) -> Section:
    """Construct a high-confidence PARAGRAPH Section from a Block."""
    return Section(
        kind=SectionKind.PARAGRAPH,
        text=block.text,
        confidence=Confidence.HIGH,
        lines=block.lines,
        page_num=block.page_num,
        font_size=block.font_size,
    )


def _block_words(blocks: list[Block]) -> list[str]:
    """Flatten blocks to a list of words for comparison."""
    words = []
    for block in blocks:
        for line in block.lines:
            words.extend(line.text.split())
    return words


def _word_similarity(words_a: list[str], words_b: list[str]) -> float:
    """Compute word-level similarity between two word lists."""
    if not words_a and not words_b:
        return 1.0
    if not words_a or not words_b:
        return 0.0
    set_a = Counter(words_a)
    set_b = Counter(words_b)
    intersection = sum((set_a & set_b).values())
    total = max(sum(set_a.values()), sum(set_b.values()))
    return intersection / total if total > 0 else 0.0


def compare_extractions(mupdf_blocks: list[Block],
                        spatial_blocks: list[Block],
                        ) -> list[Section]:
    """Compare two extraction paths using per-page word-level multiset similarity.

    Groups blocks by page and computes a word-count overlap ratio for each
    page. High similarity = confident (PARAGRAPH). Low similarity = uncertain.
    Small uncertain regions below MIN_UNCERTAIN_WORDS are promoted to confident.
    """
    mupdf_by_page: dict[int, list[Block]] = {}
    spatial_by_page: dict[int, list[Block]] = {}
    for b in mupdf_blocks:
        mupdf_by_page.setdefault(b.page_num, []).append(b)
    for b in spatial_blocks:
        spatial_by_page.setdefault(b.page_num, []).append(b)

    all_pages = sorted(set(mupdf_by_page) | set(spatial_by_page))
    sections: list[Section] = []

    for pg in all_pages:
        m_blocks = mupdf_by_page.get(pg, [])
        s_blocks = spatial_by_page.get(pg, [])

        m_words = _block_words(m_blocks)
        s_words = _block_words(s_blocks)
        sim = _word_similarity(m_words, s_words)

        if sim < SIMILARITY_THRESHOLD:
            m_joined = unicodedata.normalize('NFC', " ".join(m_words))
            s_joined = unicodedata.normalize('NFC', " ".join(s_words))
            if m_joined == s_joined:
                sim = 1.0

        # When spatial has zero words (e.g. all blocks removed by table
        # exclusion) but mupdf has content, trust mupdf directly.  The
        # absence of spatial data is a pipeline artifact, not a real
        # extraction disagreement.
        if not s_words and m_words:
            sim = 1.0

        if sim >= SIMILARITY_THRESHOLD:
            for block in m_blocks:
                sections.append(_make_paragraph_section(block))
        else:
            _log.debug("Page %d: similarity %.2f < threshold, marking uncertain",
                        pg, sim)
            m_text = "\n\n".join(b.text for b in m_blocks)
            s_text = "\n\n".join(b.text for b in s_blocks)
            sections.append(Section(
                kind=SectionKind.UNCERTAIN,
                text=m_text,
                confidence=Confidence.UNCERTAIN,
                mupdf_text=m_text,
                spatial_text=s_text,
                page_num=pg,
                lines=[ln for b in m_blocks for ln in b.lines],
            ))

    all_pages_set = set(all_pages)
    uncertain_pages = [pg for pg in all_pages
                       if any(s.kind == SectionKind.UNCERTAIN and s.page_num == pg
                              for s in sections)]
    promoted: set[int] = set()
    for pg in uncertain_pages:
        if pg in promoted:
            continue
        next_pg = pg + 1
        if next_pg not in all_pages_set:
            continue
        m_words_2 = _block_words(mupdf_by_page.get(pg, []) +
                                 mupdf_by_page.get(next_pg, []))
        s_words_2 = _block_words(spatial_by_page.get(pg, []) +
                                 spatial_by_page.get(next_pg, []))
        if _word_similarity(m_words_2, s_words_2) >= SIMILARITY_THRESHOLD:
            promoted.add(pg)
            promoted.add(next_pg)

    if promoted:
        kept = [s for s in sections
                if not (s.kind == SectionKind.UNCERTAIN
                        and s.page_num in promoted)]
        # Only re-add blocks for pages that were actually uncertain.
        # Pages pulled into the promoted set as neighbors may already
        # have high-confidence sections; re-adding would duplicate them.
        already_covered = {s.page_num for s in kept
                           if s.page_num in promoted}
        for pg in sorted(promoted):
            if pg in already_covered:
                continue
            for block in mupdf_by_page.get(pg, []):
                kept.append(_make_paragraph_section(block))
        sections = kept

    still_uncertain = [pg for pg in all_pages
                       if any(s.kind == SectionKind.UNCERTAIN and s.page_num == pg
                              for s in sections)]
    if still_uncertain:
        m_words_all: list[str] = []
        s_words_all: list[str] = []
        for pg in still_uncertain:
            m_words_all.extend(_block_words(mupdf_by_page.get(pg, [])))
            s_words_all.extend(_block_words(spatial_by_page.get(pg, [])))
        m_all_nfc = unicodedata.normalize('NFC', " ".join(m_words_all))
        s_all_nfc = unicodedata.normalize('NFC', " ".join(s_words_all))
        doc_match = (m_all_nfc == s_all_nfc
                     or _word_similarity(m_words_all, s_words_all)
                     >= SIMILARITY_THRESHOLD)
        if doc_match:
            bulk_promoted = set(still_uncertain)
            kept = [s for s in sections
                    if not (s.kind == SectionKind.UNCERTAIN
                            and s.page_num in bulk_promoted)]
            already_covered = {s.page_num for s in kept
                               if s.page_num in bulk_promoted}
            for pg in sorted(bulk_promoted):
                if pg in already_covered:
                    continue
                for block in mupdf_by_page.get(pg, []):
                    kept.append(_make_paragraph_section(block))
            sections = kept

    sections.sort(key=lambda sec: sec.page_num)

    for sec in sections:
        if sec.kind != SectionKind.UNCERTAIN:
            continue
        m_len = len(sec.mupdf_text.split()) if sec.mupdf_text else 0
        s_len = len(sec.spatial_text.split()) if sec.spatial_text else 0
        if min(m_len, s_len) < MIN_UNCERTAIN_WORDS:
            sec.kind = SectionKind.PARAGRAPH
            sec.confidence = Confidence.LOW
            sec.mupdf_text = ""
            sec.spatial_text = ""

    return sections


_BODY_PROSE_MIN_CHARS = 500
_BODY_PROSE_MIN_FRACTION = 0.10


def _detect_body_size(sections: list[Section]) -> float:
    """Find the most common font size that represents body text.

    Prefers prose (non-monospace) spans so that code-heavy papers don't
    bias the body size toward the smaller code font. Falls back to the
    overall most common size when prose is insufficient (e.g. wording
    papers that are nearly entirely monospace specification text), since
    in those papers the spec font *is* the body font.
    """
    prose_sizes: Counter[float] = Counter()
    all_sizes: Counter[float] = Counter()
    for sec in sections:
        for line in sec.lines:
            for span in line.spans:
                if not span.text.strip():
                    continue
                all_sizes[span.font_size] += len(span.text)
                if not span.monospace:
                    prose_sizes[span.font_size] += len(span.text)

    prose_total = sum(prose_sizes.values())
    all_total = sum(all_sizes.values())
    prose_sufficient = (
        prose_total >= _BODY_PROSE_MIN_CHARS
        and (all_total == 0 or prose_total >= _BODY_PROSE_MIN_FRACTION * all_total)
    )
    source = prose_sizes if prose_sufficient else all_sizes
    if not source:
        return FALLBACK_BODY_SIZE
    return source.most_common(1)[0][0]


def _rank_font_sizes(sections: list[Section],
                     body_size: float) -> dict[float, int]:
    """Rank font sizes larger than body. Returns {size: heading_level}."""
    sizes = set()
    for sec in sections:
        for line in sec.lines:
            fs = line.font_size
            if fs > body_size * _HEADING_SIZE_RATIO:
                sizes.add(fs)
    ranked = sorted(sizes, reverse=True)
    return {sz: i + 1 for i, sz in enumerate(ranked)}


_ROMAN_RE = re.compile(r"^[IVXLCDM]+$")


def _heading_level_from_number(section_num: str) -> int:
    """Compute heading level from dotted decimal or Roman numeral: depth + 1.

    Roman numerals (I, II, IV, ...) are flat top-level sections = level 2.
    Arabic dotted numbers use dot-count + 2 (1 = 2, 1.1 = 3, 1.1.1 = 4).
    """
    if _ROMAN_RE.match(section_num):
        return 2
    parts = section_num.split(".")
    return len(parts) + 1


def _is_known_section(first_line: str) -> bool:
    """Check if *first_line* names a KNOWN_SECTIONS entry.

    Handles plain names ("Abstract"), numbered prefixes without trailing
    dot ("3.2 Motivation"), and numbered prefixes with trailing dot
    ("1. Introduction", "1.1. Alternatives") by stripping the section
    number before the lookup.
    """
    normalized = first_line.lower().rstrip(":")
    if normalized in KNOWN_SECTIONS:
        return True
    m = SECTION_NUM_RE.match(first_line)
    if m:
        rest = m.group(2).strip().lower().rstrip(":")
        return rest in KNOWN_SECTIONS
    m2 = SECTION_NUM_PREFIX_RE.match(first_line)
    if m2:
        rest = first_line[m2.end():].strip().lower().rstrip(":")
        return rest in KNOWN_SECTIONS
    return False


def heading_confidence(has_number: bool, number_level: int,
                        font_level: int | None, is_bold: bool,
                        is_known: bool) -> tuple[int, Confidence]:
    """Determine heading level and confidence from multiple signals.

    Bold alone (no section number, no enlarged font, not a known name)
    yields level 0 with LOW confidence.  The caller must assign a
    context-derived level (typically previous_heading_level + 1) before
    accepting the heading.  This keeps the function stateless while
    allowing bold-only sub-headings (common in WG21 papers) to enter
    the heading path.
    """
    if has_number:
        level = number_level
        if font_level is not None and font_level == level:
            if is_bold:
                return level, Confidence.HIGH
            return level, Confidence.MEDIUM
        if font_level is not None:
            return level, Confidence.MEDIUM
        if is_bold:
            return level, Confidence.MEDIUM
        return level, Confidence.LOW

    if font_level is not None:
        if is_known and is_bold:
            return 2, Confidence.HIGH
        if is_known:
            return 2, Confidence.MEDIUM
        if is_bold:
            return font_level + 1, Confidence.MEDIUM
        return font_level + 1, Confidence.LOW

    if is_known:
        return 2, Confidence.LOW

    if is_bold:
        return 0, Confidence.LOW

    return 0, Confidence.UNCERTAIN


# Canonical implementation lives in metadata_yaml.extract; this alias
# keeps the backward-compat structure_sections() wrapper working.
from ..metadata_yaml.extract import extract_metadata as _extract_metadata


def _split_heading_body(sec: Section,
                        body_size: float,
                        ) -> tuple[Section, Section | None]:
    """Split a multi-line section when the first line is a heading and
    the remaining lines are body text at a smaller font size.

    MuPDF sometimes merges a heading and its following paragraph into a
    single Block because they share a bounding region.  This function
    detects the pattern and returns (heading_section, body_section).
    When no split is needed, returns (sec, None).

    Split triggers when *either*:
    - first line font_size exceeds the second by >= _HEADING_BODY_SPLIT_DELTA, or
    - first line is bold and the second is not, AND the first line is
      either above body_size * _HEADING_SIZE_RATIO or short enough to
      be a heading (<= _HEADING_MAX_WORDS words).
    """
    if len(sec.lines) < 2:
        return sec, None

    line0 = sec.lines[0]
    line1 = sec.lines[1]
    fs0 = line0.font_size
    fs1 = line1.font_size

    size_drop = fs0 - fs1 >= _HEADING_BODY_SPLIT_DELTA
    bold_drop = (
        line0.is_bold
        and not line1.is_bold
        and (fs0 >= body_size * _HEADING_SIZE_RATIO
             or len(line0.text.split()) <= _HEADING_MAX_WORDS)
    )

    if not size_drop and not bold_drop:
        return sec, None

    body_lines = sec.lines[1:]
    body_text = "\n".join(ln.text for ln in body_lines).strip()
    if not body_text:
        head = replace(sec, lines=[line0], text=line0.text,
                        font_size=fs0)
        return head, None

    head = replace(sec, lines=[line0], text=line0.text,
                    font_size=fs0)
    body = replace(sec,
                   kind=SectionKind.PARAGRAPH,
                   text=body_text,
                   confidence=Confidence.HIGH,
                   lines=body_lines,
                   font_size=fs1)
    _log.debug("Split heading+body: heading=%r body=%r (fs %.0f->%.0f)",
               line0.text.strip()[:40], body_text[:40], fs0, fs1)
    return head, body


def structure_sections(sections: list[Section],
                       has_title: bool = False,
                       ) -> tuple[dict, list[Section], int]:
    """Apply heading intelligence, paragraph grouping, and list detection.

    If has_title is True, the title was already extracted from front matter
    and no title detection is performed.

    Returns (metadata_dict, structured_sections, nesting_corrections).

    Note: metadata extraction is done internally for backward compatibility.
    New callers should use structure_body() after separate metadata extraction.
    """
    metadata, sections = _extract_metadata(sections)
    return _structure_body_impl(metadata, sections, has_title)


def structure_body(sections: list[Section],
                   has_title: bool = False,
                   ) -> tuple[dict, list[Section], int]:
    """Body structuring only, without metadata extraction.

    Called after metadata extraction is complete.
    Returns (body_metadata, structured_sections, nesting_corrections).
    body_metadata may contain a 'title' if one was detected during structuring.
    """
    return _structure_body_impl({}, sections, has_title)


def _structure_body_impl(metadata: dict,
                         sections: list[Section],
                         has_title: bool = False,
                         ) -> tuple[dict, list[Section], int]:
    """Implementation of body structuring logic."""
    body_size = _detect_body_size(sections)
    font_ranks = _rank_font_sizes(sections, body_size)

    _log.debug("Body size: %.1f, font ranks: %s", body_size, font_ranks)

    title_found = has_title
    structured: list[Section] = []
    skip_indices: set[int] = set()
    last_heading_level = 0

    for idx, sec in enumerate(sections):
        if idx in skip_indices:
            continue

        if sec.kind in (SectionKind.UNCERTAIN, SectionKind.TABLE):
            structured.append(sec)
            continue

        all_lines = sec.text.split("\n")
        first_line = all_lines[0].strip()

        # Multi-line heading blocks: MuPDF sometimes places the section
        # number ("I.", "2.") on line 0 and the title on line 1.  Join
        # them so SECTION_NUM_RE and _is_known_section see the full heading.
        # Guard: require bold (heading font) to avoid joining numbered list items.
        _sec_is_bold = bool(sec.lines) and sec.lines[0].is_bold
        if (len(all_lines) >= 2
                and _sec_is_bold
                and _BARE_SECTION_NUM_RE.match(first_line)):
            second = all_lines[1].strip()
            if second and len(second.split()) <= _HEADING_MAX_WORDS:
                _log.debug("Multi-line heading join: %r + %r", first_line, second)
                first_line = first_line.rstrip(".").rstrip() + " " + second

        if not title_found:
            is_large = sec.font_size > body_size * _TITLE_SIZE_RATIO
            is_known = (_is_known_section(first_line)
                        or first_line.lower() in ("contents", "table of contents"))
            is_section_num = bool(SECTION_NUM_RE.match(first_line))
            has_email = "@" in first_line
            is_date = bool(DATE_RE.match(first_line))
            too_long = len(first_line) > _TITLE_MAX_LENGTH

            if is_large and (is_known or is_section_num):
                title_found = True
                # Fall through to heading classification below so the
                # section is classified as HEADING, not left as PARAGRAPH.

            if (is_large and not is_section_num and not is_known
                    and not has_email and not is_date and not too_long):
                title_parts = [" ".join(
                    ln.strip() for ln in sec.text.split("\n") if ln.strip()
                )]
                title_fs = sec.font_size
                large_thresh = body_size * _TITLE_SIZE_RATIO
                for j in range(idx + 1, len(sections)):
                    nxt = sections[j]
                    if nxt.font_size <= large_thresh:
                        break
                    fl = nxt.text.split("\n")[0].strip()
                    if abs(nxt.font_size - title_fs) / title_fs > _TITLE_CONT_FONT_TOL:
                        break
                    fl_low = fl.lower().rstrip(":")
                    if _is_known_section(fl) or fl_low in _TITLE_CONT_SKIP:
                        break
                    if SECTION_NUM_RE.match(fl):
                        break
                    if _TITLE_CONT_DATE_RE.match(fl):
                        break
                    if "@" in fl:
                        break
                    if not fl or fl.isspace():
                        break
                    if fl.strip().isdigit():
                        break
                    title_parts.append(fl)
                    skip_indices.add(j)
                raw_title = " ".join(title_parts)
                stripped = _TITLE_PID_PREFIX_RE.sub("", raw_title).strip()
                metadata["title"] = stripped if stripped else raw_title
                sec.kind = SectionKind.TITLE
                sec.heading_level = 1
                sec.confidence = Confidence.HIGH
                title_found = True
                structured.append(sec)
                last_heading_level = 1
                continue

        m = SECTION_NUM_RE.match(first_line)
        has_number = m is not None
        section_num = m.group(1) if m else ""

        line_fs = sec.font_size
        font_level = font_ranks.get(line_fs)
        # Fallback: if section's most-common font size isn't a heading font,
        # check the first line's font size (handles MuPDF heading+body merges).
        # Only triggers when there is a clear size drop from line 0 to line 1,
        # confirming the heading+body merge pattern.
        if font_level is None and len(sec.lines) >= 2:
            first_fs = sec.lines[0].font_size
            second_fs = sec.lines[1].font_size
            if first_fs - second_fs >= _HEADING_BODY_SPLIT_DELTA:
                first_level = font_ranks.get(first_fs)
                if first_level is not None:
                    font_level = first_level
                    line_fs = first_fs
        is_bold = bool(sec.lines) and sec.lines[0].is_bold
        is_known = _is_known_section(first_line)

        if has_number or font_level is not None or is_known or is_bold:
            number_level = _heading_level_from_number(section_num) if has_number else 0
            level, conf = heading_confidence(
                has_number, number_level, font_level, is_bold, is_known)

            # Color signal: if the first line's color differs from the
            # next body line, the visual distinction strengthens the case
            # for a heading.  Upgrade LOW -> MEDIUM so the prose-length
            # filter does not reject long but visually distinct headings.
            if conf == Confidence.LOW and len(sec.lines) >= 2:
                head_color = _line_color(sec.lines[0])
                body_color = _line_color(sec.lines[1])
                if head_color is not None and body_color is not None and head_color != body_color:
                    conf = Confidence.MEDIUM

            # Bold-only headings: heading_confidence returns level=0.
            # Infer level from the last heading seen (child of it),
            # clamped to at least 3 so bold-only never becomes H2.
            # Reject if the line contains code-like characters or is
            # monospace (avoids misclassifying code fragments as headings).
            if level == 0 and is_bold and conf == Confidence.LOW:
                has_code_chars = bool(_CODE_CHARS & set(first_line))
                is_mono = bool(sec.lines) and sec.lines[0].is_monospace
                if has_code_chars or is_mono:
                    level = 0  # keep as paragraph
                else:
                    level = max(last_heading_level + 1, 3)

            is_prose_length = len(first_line.split()) > _HEADING_MAX_WORDS
            prose_on_weak_signal = is_prose_length and conf == Confidence.LOW
            if level > 0 and not prose_on_weak_signal:
                head_sec, body_sec = _split_heading_body(sec, body_size)
                head_sec.kind = SectionKind.HEADING
                head_sec.heading_level = level
                head_sec.confidence = conf
                structured.append(head_sec)
                if body_sec is not None:
                    structured.append(body_sec)
                last_heading_level = level
                continue

        lines = sec.text.split("\n")
        is_list = all(
            BULLET_RE.match(ln) or NUMBERED_LIST_RE.match(ln)
            for ln in lines if ln.strip()
        )
        if is_list and any(ln.strip() for ln in lines):
            sec.kind = SectionKind.LIST
            structured.append(sec)
            continue

        sec.kind = SectionKind.PARAGRAPH
        structured.append(sec)

    structured = _merge_orphan_heading_numbers(structured)
    structured = _detect_lists_by_position(structured)
    structured = _merge_paragraphs(structured)
    structured = _detect_code_blocks(structured)
    structured = [s for s in structured if _detect_lang_label(s) is None]
    structured = _classify_wording_sections(structured)
    structured = _coalesce_code_paragraphs(structured)
    structured = _rescue_unfenced_code(structured)
    _demote_repeated_low_confidence_numbers(structured)
    nesting_corrections = _validate_nesting(structured)
    return metadata, structured, nesting_corrections


# Bare section number with no heading text (e.g. "1", "2.3", "A").
# Some PDFs render the heading number in large font and the title
# text in a smaller font, producing separate blocks.
_BARE_SECTION_NUM_RE = re.compile(
    rf"^\s*(?:{SECTION_NUM_PATTERN}|[A-Z])\.?\s*$"
)
# Max words in the paragraph that follows an orphan heading number.
# Real heading titles are short; if the next paragraph is longer,
# it's body text, not a title.
_ORPHAN_HEADING_MAX_WORDS = 8


def _merge_orphan_heading_numbers(sections: list[Section]) -> list[Section]:
    """Merge bare-number headings with the following short paragraph.

    Some PDFs (e.g. P4012R0) render heading numbers in large font
    and heading titles in a different, smaller font. The structure
    pass classifies the number as a HEADING and the title as a
    PARAGRAPH. This pass detects that pattern and merges them:
    the heading text becomes "N TITLE" and the paragraph is consumed.
    """
    if len(sections) < 2:
        return sections

    result: list[Section] = []
    skip_next = False

    for i, sec in enumerate(sections):
        if skip_next:
            skip_next = False
            continue

        if (sec.kind == SectionKind.HEADING
                and i + 1 < len(sections)
                and _BARE_SECTION_NUM_RE.match(sec.text.strip())):
            nxt = sections[i + 1]
            nxt_text = nxt.text.strip()
            nxt_words = len(nxt_text.split())
            if (nxt.kind == SectionKind.PARAGRAPH
                    and 0 < nxt_words <= _ORPHAN_HEADING_MAX_WORDS
                    and nxt.page_num == sec.page_num):
                merged_text = f"{sec.text.strip()} {nxt_text}"
                merged = replace(
                    sec,
                    text=merged_text,
                    lines=sec.lines + nxt.lines,
                )
                _log.info("Merged orphan heading number %r + %r -> %r",
                           sec.text.strip(), nxt_text, merged_text)
                result.append(merged)
                skip_next = True
                continue

        result.append(sec)

    return result


_BULLET_SPLIT_RE = re.compile(
    r"(?=[" + "".join(BULLET_CHARS) + r"][\s\u200b])"
)

_INDENT_TOLERANCE = 5.0


def _get_body_margin(sections: list[Section]) -> float:
    """Find the leftmost frequent x-position (= body left margin).

    Uses the leftmost x that accounts for at least 10% of lines,
    rather than the most common x, to handle PDFs where indented
    content is more frequent than body text.
    """
    x_counts: Counter[float] = Counter()
    for sec in sections:
        for line in sec.lines:
            if line.text.strip() and line.spans:
                x = round(line.bbox[0] / _INDENT_TOLERANCE) * _INDENT_TOLERANCE
                x_counts[x] += 1
    if not x_counts:
        return 0.0
    total = sum(x_counts.values())
    threshold = total * _BODY_MARGIN_FRACTION
    for x in sorted(x_counts.keys()):
        if x_counts[x] >= threshold:
            return x
    return x_counts.most_common(1)[0][0]


def _line_starts_with_bullet(line) -> bool:
    """Check if a line starts with a bullet character."""
    text = line.text.strip()
    if not text:
        return False
    return text[0] in BULLET_CHARS


def _detect_lists_by_position(sections: list[Section]) -> list[Section]:
    """Detect list structure using line x-positions and bullet characters.

    Uses indentation (x-coordinate) to identify list items. Lines
    indented from the body margin that start with a bullet character
    are list items. Lines at the body margin between bullet groups
    are parent list items. Also handles inline bullet splitting
    when position data is unavailable.
    """
    body_margin = _get_body_margin(sections)

    result = []
    for sec in sections:
        if sec.kind != SectionKind.PARAGRAPH:
            result.append(sec)
            continue

        if sec.lines:
            items = _split_section_by_position(sec, body_margin)
            result.extend(items)
        else:
            items = _split_inline_bullets_text(sec)
            result.extend(items)

    return result


def _join_bullet_marker_lines(lines: list) -> list:
    """Join bullet marker lines with their following text lines.

    Some PDFs render the dash/bullet on one line (x=90) and the text
    on the next line (x=108). Combine them into a single line.
    """
    if len(lines) < 2:
        return lines
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        text = line.text.strip()
        if not line.spans:
            result.append(line)
            i += 1
            continue
        if (text and text[0] in BULLET_CHARS and len(text) <= _BULLET_JOIN_MAX_CHARS
                and i + 1 < len(lines)):
            next_line = lines[i + 1]
            bullet = strip_format_chars(text).rstrip()
            bullet_span = Span(
                text=bullet + " ",
                font_name=line.spans[0].font_name if line.spans else "",
                font_size=line.spans[0].font_size if line.spans else 0,
                bold=line.spans[0].bold if line.spans else False,
                italic=line.spans[0].italic if line.spans else False,
                monospace=line.spans[0].monospace if line.spans else False,
                bbox=line.spans[0].bbox if line.spans else (0, 0, 0, 0),
                origin=line.spans[0].origin if line.spans else (0, 0),
            )
            combined_spans = [bullet_span] + list(next_line.spans)
            result.append(Line(
                spans=combined_spans,
                bbox=(line.bbox[0], line.bbox[1], next_line.bbox[2], next_line.bbox[3]),
                page_num=line.page_num,
            ))
            i += 2
        else:
            result.append(line)
            i += 1
    return result


def _split_section_by_position(sec: Section, body_margin: float) -> list[Section]:
    """Split a section into list items using line x-positions."""
    lines = _join_bullet_marker_lines(sec.lines)

    indented_bullets = []
    for line in lines:
        if not line.text.strip() or not line.spans:
            continue
        x = line.bbox[0]
        if x > body_margin + _INDENT_TOLERANCE and _line_starts_with_bullet(line):
            indented_bullets.append(line)

    if len(indented_bullets) < 1:
        return [sec]

    items: list[Section] = []
    current_lines: list = []
    current_indent = 0
    current_is_bullet = False

    for line in lines:
        if not line.text.strip():
            if current_lines:
                current_lines.append(line)
            continue

        x = line.bbox[0]
        is_bullet = _line_starts_with_bullet(line)
        indent = 0
        if x > body_margin + _INDENT_TOLERANCE:
            indent = 1
        if x > body_margin + _INDENT_TOLERANCE * 3:
            indent = 2

        if is_bullet and indent > 0:
            if current_lines:
                text = "\n".join(ln.text for ln in current_lines if ln.text.strip())
                items.append(Section(
                    kind=SectionKind.LIST if current_is_bullet else SectionKind.PARAGRAPH,
                    text=text,
                    confidence=sec.confidence,
                    lines=list(current_lines),
                    page_num=sec.page_num,
                    font_size=sec.font_size,
                    indent_level=current_indent,
                ))
            current_lines = [line]
            current_indent = indent
            current_is_bullet = True
        elif indent == 0 and current_is_bullet:
            if current_lines:
                text = "\n".join(ln.text for ln in current_lines if ln.text.strip())
                items.append(Section(
                    kind=SectionKind.LIST,
                    text=text,
                    confidence=sec.confidence,
                    lines=list(current_lines),
                    page_num=sec.page_num,
                    font_size=sec.font_size,
                    indent_level=current_indent,
                ))
            current_lines = [line]
            current_indent = 0
            current_is_bullet = False
        else:
            current_lines.append(line)

    if current_lines:
        text = "\n".join(ln.text for ln in current_lines if ln.text.strip())
        if text.strip():
            items.append(Section(
                kind=SectionKind.LIST if current_is_bullet else SectionKind.PARAGRAPH,
                text=text,
                confidence=sec.confidence,
                lines=list(current_lines),
                page_num=sec.page_num,
                font_size=sec.font_size,
                indent_level=current_indent,
            ))

    if not items:
        return [sec]

    bullet_count = sum(1 for it in items if it.kind == SectionKind.LIST)
    if bullet_count < 1:
        return [sec]

    return items


def _split_inline_bullets_text(sec: Section) -> list[Section]:
    """Fallback: split paragraphs at Unicode bullet characters in text."""
    text = sec.text
    parts = _BULLET_SPLIT_RE.split(text)
    if len(parts) <= 1:
        return [sec]

    result = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        starts_with_bullet = part[0] in BULLET_CHARS
        result.append(Section(
            kind=SectionKind.LIST if starts_with_bullet else SectionKind.PARAGRAPH,
            text=part,
            confidence=sec.confidence,
            lines=[],
            page_num=sec.page_num,
            font_size=sec.font_size,
        ))
    return result if result else [sec]


def _merge_paragraphs(sections: list[Section]) -> list[Section]:
    """Merge consecutive sections that are continuations.

    When a section ends without terminal punctuation and the next
    paragraph starts with a lowercase letter, they are the same
    logical paragraph split by PDF line wrapping. Works for
    PARAGRAPH+PARAGRAPH and LIST+PARAGRAPH (bullet continuation).
    """
    if len(sections) < 2:
        return sections

    mergeable = frozenset({SectionKind.PARAGRAPH, SectionKind.LIST})

    result = [replace(sections[0], lines=list(sections[0].lines))]
    for sec in sections[1:]:
        prev = result[-1]
        if (prev.kind in mergeable
                and sec.kind == SectionKind.PARAGRAPH
                and prev.text.rstrip()
                and sec.text.lstrip()):
            prev_end = prev.text.rstrip()[-1]
            cur_start = sec.text.lstrip()[0]
            if prev_end not in TERMINAL_PUNCTUATION and cur_start.islower():
                prev.text = prev.text.rstrip() + " " + sec.text.lstrip()
                prev.lines.extend(sec.lines)
                if prev.lines:
                    prev.font_size = prev.lines[0].font_size
                continue
        result.append(replace(sec, lines=list(sec.lines)))
    return result


def _line_is_monospace(line) -> bool:
    """Check if all non-whitespace spans in a line are monospace."""
    text_spans = [s for s in line.spans if s.text.strip()]
    return bool(text_spans) and all(s.monospace for s in text_spans)


def _section_is_all_monospace(sec: Section) -> bool:
    """Check if every non-whitespace line in a section is monospace."""
    content_lines = [ln for ln in sec.lines if ln.text.strip()]
    return bool(content_lines) and all(_line_is_monospace(ln) for ln in content_lines)


def _section_is_empty(sec: Section) -> bool:
    """Check if a section has no visible text content."""
    return not sec.text.strip()


_LANG_LABELS = {
    "c/c++": "cpp",
    "c++": "cpp",
    "cpp": "cpp",
    "c": "c",
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "java": "java",
    "rust": "rust",
    "go": "go",
    "bash": "bash",
    "shell": "bash",
    "sql": "sql",
    "json": "json",
    "yaml": "yaml",
    "xml": "xml",
    "html": "html",
    "css": "css",
}


def _detect_lang_label(sec: Section) -> str | None:
    """Check if a section is a code block language label."""
    text = strip_format_chars(sec.text).strip().lower()
    return _LANG_LABELS.get(text)


def _detect_code_blocks(sections: list[Section]) -> list[Section]:
    """Detect runs of consecutive monospace sections and merge into CODE.

    Bridges empty sections (blank lines in code) between monospace runs.
    Detects language labels (e.g. "C/C++") immediately before code blocks
    and uses them for the fence language.
    """
    result: list[Section] = []
    mono_run: list[Section] = []
    fence_lang = DEFAULT_FENCE_LANG
    pending_label_idx = -1

    def flush_mono():
        nonlocal fence_lang, pending_label_idx
        if not mono_run:
            return
        all_lines = []
        for s in mono_run:
            if _section_is_empty(s):
                all_lines.append(Line(spans=[], bbox=(0, 0, 0, 0)))
            else:
                all_lines.extend(s.lines)
        code_text = "\n".join(ln.text for ln in all_lines)
        result.append(Section(
            kind=SectionKind.CODE,
            text=code_text,
            confidence=Confidence.HIGH,
            lines=all_lines,
            page_num=mono_run[0].page_num,
            fence_lang=fence_lang,
        ))
        if pending_label_idx >= 0 and pending_label_idx < len(result) - 1:
            del result[pending_label_idx]
        mono_run.clear()
        fence_lang = DEFAULT_FENCE_LANG
        pending_label_idx = -1

    for i, sec in enumerate(sections):
        if sec.kind in (SectionKind.PARAGRAPH, SectionKind.LIST):
            if _section_is_all_monospace(sec):
                mono_run.append(sec)
                continue

            if _section_is_empty(sec) and mono_run:
                mono_run.append(sec)
                continue

        if sec.kind == SectionKind.UNCERTAIN and mono_run:
            if _section_is_all_monospace(sec):
                mono_run.append(sec)
                continue

        if mono_run:
            flush_mono()

        lang = _detect_lang_label(sec)
        if lang is not None:
            result.append(sec)
            fence_lang = lang
            pending_label_idx = len(result) - 1
        else:
            result.append(sec)
            if pending_label_idx >= 0:
                fence_lang = DEFAULT_FENCE_LANG
                pending_label_idx = -1

    flush_mono()
    return result


def _classify_wording_sections(sections: list[Section]) -> list[Section]:
    """Reclassify sections containing wording-marked spans."""
    for sec in sections:
        if sec.kind in (SectionKind.HEADING, SectionKind.TITLE,
                        SectionKind.UNCERTAIN, SectionKind.TABLE):
            continue
        wording_spans = [s for ln in sec.lines for s in ln.spans
                         if s.wording_role and s.text.strip()]
        if not wording_spans:
            continue
        roles = {s.wording_role for s in wording_spans}
        non_context = roles - {"context"}
        if non_context == {"ins"}:
            sec.kind = SectionKind.WORDING_ADD
        elif non_context == {"del"}:
            sec.kind = SectionKind.WORDING_REMOVE
        elif non_context:
            sec.kind = SectionKind.WORDING
        elif "context" in roles:
            sec.kind = SectionKind.WORDING
    return sections


_WORDING_KINDS = frozenset({
    SectionKind.WORDING,
    SectionKind.WORDING_ADD,
    SectionKind.WORDING_REMOVE,
})


_COALESCE_MAX_LINES = 4

_COALESCE_CODE_RE = re.compile(
    r"^\s*[{}]|"               # standalone brace lines
    r"#include\s*<|"           # preprocessor includes
    r"#define\s+\w|"           # preprocessor defines
    r"\w+\s*\([^)]*\)\s*\{|"  # function_name(...) {
    r"\w+\s*\([^)]*\)\s*;|"   # declaration: name(...);
    r"^\s*static_assert\s*\(|"
    r"^\s*//[^/]",
    re.MULTILINE,
)


def _coalesce_code_paragraphs(sections: list[Section]) -> list[Section]:
    """Merge adjacent short, code-like PARAGRAPH sections before rescue.

    Multi-line code examples often get split into separate one- or
    two-line paragraphs by the block breaker.  Individually, each
    paragraph falls below _RESCUE_MIN_CODE_LINES and is never rescued.
    This pass scans for runs of consecutive short PARAGRAPH sections
    (at most _COALESCE_MAX_LINES each) where every section matches a
    strict code regex, and merges those runs into a single PARAGRAPH
    whose combined line count is high enough for the rescue pass to
    promote.

    Uses _COALESCE_CODE_RE (excludes trailing-semicolon pattern) to
    avoid merging prose paragraphs whose lines happen to end with ";".
    """
    result: list[Section] = []
    i = 0
    while i < len(sections):
        sec = sections[i]
        lines = sec.text.splitlines()
        if (sec.kind != SectionKind.PARAGRAPH
                or len(lines) > _COALESCE_MAX_LINES
                or not _COALESCE_CODE_RE.search(sec.text)):
            result.append(sec)
            i += 1
            continue
        run = [sec]
        j = i + 1
        while j < len(sections):
            nxt = sections[j]
            nxt_lines = nxt.text.splitlines()
            if (nxt.kind != SectionKind.PARAGRAPH
                    or len(nxt_lines) > _COALESCE_MAX_LINES
                    or not _COALESCE_CODE_RE.search(nxt.text)):
                break
            run.append(nxt)
            j += 1
        if len(run) >= 2:
            merged_text = "\n".join(s.text for s in run)
            merged = replace(run[0], text=merged_text)
            result.append(merged)
            _log.info("Coalesced %d code-like paragraphs (%d chars)",
                       len(run), len(merged_text))
        else:
            result.append(sec)
        i = j
    return result


def _rescue_unfenced_code(sections: list[Section]) -> list[Section]:
    """Content-based rescue pass: promote paragraph sections to CODE.

    Runs AFTER _classify_wording_sections so wording-classified sections
    are skipped. Scans lines within each section (not across sections)
    because _merge_paragraphs may have merged code lines into a single
    section when they lack terminal punctuation in TERMINAL_PUNCTUATION.

    A section is promoted when _RESCUE_MIN_CODE_LINES or more of its
    lines match _STRUCTURAL_CODE_RE.
    """
    for sec in sections:
        if sec.kind in _WORDING_KINDS:
            continue
        if sec.kind != SectionKind.PARAGRAPH:
            continue
        text = sec.text
        lines = text.splitlines()
        code_count = sum(1 for ln in lines if _STRUCTURAL_CODE_RE.search(ln))
        if code_count >= _RESCUE_MIN_CODE_LINES:
            sec.kind = SectionKind.CODE
            sec.confidence = Confidence.MEDIUM
    return sections


_PARAGRAPH_NUM_MIN_REPEATS = 3


def _demote_repeated_low_confidence_numbers(sections: list[Section]) -> None:
    """Demote LOW-confidence numbered headings when their section_num
    repeats often enough to indicate paragraph numbering.

    Real section numbers are unique, but many papers duplicate each one
    between a TOC and the body (count 2), and a real section that ends up
    at LOW confidence can also collide with paragraph numbering elsewhere.
    Paragraph numbering resets per clause ("1 Constraints:", "2 Mandates:",
    then later "1 Preconditions:", ...) so the same number typically shows
    up 5 or more times. Requiring count >= 3 keeps TOC/body pairs and
    section/paragraph 1-on-1 collisions intact while catching the
    paragraph-numbering pattern.

    Only LOW-confidence headings are considered, so a real section title
    with font-size or bold confirmation is preserved regardless.
    """
    counts: Counter[str] = Counter()
    nums_by_index: dict[int, str] = {}
    for i, sec in enumerate(sections):
        if (sec.kind != SectionKind.HEADING
                or sec.confidence != Confidence.LOW):
            continue
        first_line = sec.text.split("\n")[0].strip()
        m = SECTION_NUM_RE.match(first_line)
        if not m:
            continue
        num = m.group(1)
        nums_by_index[i] = num
        counts[num] += 1

    repeated = {num for num, c in counts.items()
                if c >= _PARAGRAPH_NUM_MIN_REPEATS}
    if not repeated:
        return

    for i, num in nums_by_index.items():
        if num in repeated:
            sec = sections[i]
            _log.info("Demote repeated low-conf number %r: %r",
                       num, sec.text[:40])
            sec.kind = SectionKind.PARAGRAPH
            sec.heading_level = 0


_SIBLING_FONT_TOL = 0.1


def _validate_nesting(sections: list[Section]) -> int:
    """Ensure heading levels don't skip more than one level deeper.

    Mutates headings that skip levels: adjusts heading_level and
    downgrades confidence from HIGH to MEDIUM when corrected.

    Also consolidates runs of same-styled headings as siblings. When
    consecutive headings share a font size, they're at the same logical
    level; without this check, a run of similar entries (e.g. a dozen
    "Changes since P0876RN" items) would each get prev_clamped + 1,
    cascading to ever-deeper levels.

    Returns the number of corrections applied.
    """
    prev_level = 0
    prev_font_size: float | None = None
    corrections = 0
    for sec in sections:
        if sec.kind != SectionKind.HEADING:
            continue
        is_sibling = (
            prev_font_size is not None
            and abs(sec.font_size - prev_font_size) <= _SIBLING_FONT_TOL
        )
        if is_sibling and prev_level > 0 and sec.heading_level > prev_level:
            _log.info("Nesting sibling: h%d -> h%d for %r",
                       sec.heading_level, prev_level, sec.text[:40])
            sec.heading_level = prev_level
            if sec.confidence == Confidence.HIGH:
                sec.confidence = Confidence.MEDIUM
            corrections += 1
        elif prev_level > 0 and sec.heading_level > prev_level + 1:
            corrected = prev_level + 1
            _log.info("Nesting fix: h%d -> h%d for %r",
                       sec.heading_level, corrected,
                       sec.text[:40])
            sec.heading_level = corrected
            if sec.confidence == Confidence.HIGH:
                sec.confidence = Confidence.MEDIUM
            corrections += 1
        prev_level = sec.heading_level
        prev_font_size = sec.font_size
    return corrections
