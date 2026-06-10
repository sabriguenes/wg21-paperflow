# Copyright 2026 The C++ Alliance, Inc.
# SPDX-License-Identifier: BSL-1.0
"""Body cleanup: abstract dedup and metadata-echo removal from UNCERTAIN sections."""

import logging
import re
from dataclasses import replace

from tomd.lib.pdf.types import Confidence, KNOWN_SECTIONS, Section, SectionKind
from tomd.lib.metadata_yaml.strip import _CONTENT_HEADING_NUM_RE

_log = logging.getLogger(__name__)

_MIN_ABSTRACT_BODY_WORDS = 10
_MAX_ABSTRACT_BODY_WORDS = 200


def dedup_abstract(sections: list[Section]) -> None:
    """Remove duplicate Abstract headings, keeping the one with body content.

    After TOC stripping, the real Abstract heading (from the metadata zone)
    and a protected TOC entry for "Abstract" can both survive. This removes
    the empty duplicate. Mutates *sections* in-place.
    """
    positions: list[tuple[int, bool]] = []
    for i, sec in enumerate(sections):
        if sec.kind == SectionKind.HEADING:
            fl = sec.text.split("\n")[0].strip().lower().rstrip(":")
            if fl == "abstract":
                has_body = (i + 1 < len(sections)
                            and sections[i + 1].kind != SectionKind.HEADING)
                positions.append((i, has_body))
    if len(positions) <= 1:
        return
    with_body = [p for p in positions if p[1]]
    if with_body:
        remove = {idx for idx, hb in positions if not hb}
    else:
        remove = {idx for idx, _ in positions[1:]}
    if remove:
        _log.debug("Dedup: removing %d empty abstract heading(s)", len(remove))
        sections[:] = [s for i, s in enumerate(sections) if i not in remove]


_META_ECHO_RE = re.compile(
    r"^(?:Document|Paper\s*Number|Date|Audience|Reply[\s-]?to|Authors?|Editors?)\s*:",
    re.IGNORECASE,
)

_MONTH_NAMES = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)
_BARE_DATE_RE = re.compile(
    r"^(?:" + "|".join(_MONTH_NAMES) + r")\s+\d{1,2},?\s+\d{4}$",
    re.IGNORECASE,
)
_EMAIL_PAREN_RE = re.compile(r"[(<]?\S+@\S+[)>]?")


def _build_bare_value_matchers(metadata: dict) -> tuple[
    str, str, set[str], list[str]
]:
    """Extract bare-value match targets from the metadata dict."""
    title = (metadata.get("title") or "").strip().lower()
    doc = (metadata.get("document") or "").upper()
    audiences: set[str] = set()
    for tok in (metadata.get("audience") or "").split(","):
        tok = tok.strip()
        if tok:
            audiences.add(tok.lower())
    reply_to_names: list[str] = []
    for entry in metadata.get("reply-to") or []:
        name = re.sub(r"<[^>]*>", "", str(entry)).strip().strip('"')
        if name:
            reply_to_names.append(name.lower())
    return title, doc, audiences, reply_to_names


def _is_bare_metadata_value(stripped: str, title: str, doc: str,
                            audiences: set[str],
                            reply_to_names: list[str]) -> bool:
    """Return True if *stripped* is a bare metadata value (no label prefix)."""
    low = stripped.lower()
    if title and low == title:
        return True
    if doc and stripped.upper() == doc:
        return True
    if audiences and low in audiences:
        return True
    if _BARE_DATE_RE.match(stripped):
        return True
    if _EMAIL_PAREN_RE.search(stripped) and reply_to_names:
        for name in reply_to_names:
            if name in low:
                return True
    return False


def strip_metadata_from_uncertain(sections: list[Section],
                                  metadata: dict) -> None:
    """Remove lines from UNCERTAIN sections that echo front-matter metadata.

    Two-column PDFs and UNCERTAIN regions often contain a raw dump of
    page 0 including the metadata block. Since metadata is already in the
    YAML front matter, these lines are duplicates. Strips both labeled
    lines (``Date: ...``) and bare values (``January 22, 2026``).
    Mutates *sections* in-place.
    """
    title, doc, audiences, reply_to_names = _build_bare_value_matchers(metadata)

    for i, sec in enumerate(sections):
        if sec.kind != SectionKind.UNCERTAIN or sec.page_num != 0:
            continue
        lines = sec.text.split("\n")
        cleaned = []
        for ln in lines:
            stripped = ln.strip()
            if not stripped:
                cleaned.append(ln)
                continue
            if _META_ECHO_RE.match(stripped):
                continue
            if _is_bare_metadata_value(stripped, title, doc,
                                       audiences, reply_to_names):
                continue
            cleaned.append(ln)
        new_text = "\n".join(cleaned)
        if new_text != sec.text:
            _log.debug("Stripped metadata echo from UNCERTAIN section %d", i)
            sections[i] = replace(sec, text=new_text)


def _section_top_y(sec: Section) -> float | None:
    """Return the top-y coordinate of a section's first line, or None."""
    if sec.lines:
        return sec.lines[0].bbox[1]
    return None


def rescue_stranded_abstract_body(sections: list[Section]) -> None:
    """Move a paragraph stranded after the next heading back under Abstract.

    In two-column PDFs, column-aware sorting emits all left-column content
    before right-column content.  When the Abstract heading and the next
    heading (e.g. Background) are both in the left column, the abstract
    body text (top of the right column, same y-level) ends up after the
    next heading.  This detects the pattern and moves the paragraph back.

    Detection: Abstract HEADING at *i* is immediately followed by another
    HEADING at *i+1* (no body).  A PARAGRAPH on page 0 exists at *i+2*.
    The paragraph's top-y is closer to the Abstract heading's top-y than
    to the intervening heading's top-y, confirming it was in the opposite
    column at the Abstract's vertical level.

    Mutates *sections* in-place.
    """
    for i, sec in enumerate(sections):
        if sec.kind != SectionKind.HEADING:
            continue
        fl = sec.text.split("\n")[0].strip().lower().rstrip(":")
        if fl != "abstract":
            continue

        # Abstract must be immediately followed by another heading (no body).
        if i + 1 >= len(sections):
            continue
        next_sec = sections[i + 1]
        if next_sec.kind != SectionKind.HEADING:
            continue

        # Scan for the first non-heading section after the intervening heading.
        # It must be a paragraph or uncertain section on page 0.
        candidate_idx = None
        for j in range(i + 2, len(sections)):
            if sections[j].kind == SectionKind.HEADING:
                break
            if sections[j].page_num == 0 and sections[j].kind in (
                SectionKind.PARAGRAPH, SectionKind.UNCERTAIN,
            ):
                candidate_idx = j
                break

        if candidate_idx is None:
            continue

        candidate = sections[candidate_idx]

        # Y-position confirmation: the stranded paragraph's top-y should
        # be closer to the Abstract heading than to the intervening heading.
        abs_y = _section_top_y(sec)
        next_y = _section_top_y(next_sec)
        cand_y = _section_top_y(candidate)

        if abs_y is not None and next_y is not None and cand_y is not None:
            dist_to_abstract = abs(cand_y - abs_y)
            dist_to_next = abs(cand_y - next_y)
            if dist_to_abstract >= dist_to_next:
                # Paragraph is closer to the next heading; it belongs there.
                continue

        # Move the candidate to sit right after the Abstract heading.
        moved = sections.pop(candidate_idx)
        sections.insert(i + 1, moved)
        _log.debug(
            "Rescued stranded abstract body (section %d -> %d): %.60s",
            candidate_idx, i + 1, moved.text.replace("\n", " ")[:60],
        )
        # Only fix the first Abstract heading found.
        break


_ABSTRACT_LINE_RE = re.compile(r"^abstract\s*$", re.IGNORECASE)
_NEXT_HEADING_RE = re.compile(
    r"^(?:[IVXLCDM]+\.\s|[A-Z][A-Z\s]{3,}$|\d+(?:\.\d+)*\.?\s)",
)


def reorder_abstract_in_uncertain(sections: list[Section]) -> None:
    """Move Abstract block to the top of UNCERTAIN sections on page 0.

    Two-column PDFs often have CONTENTS and body text extracted before
    the Abstract (which sits at the bottom of page 0). This finds the
    Abstract heading within the UNCERTAIN text and moves it (plus its
    paragraph) to the beginning. Mutates *sections* in-place.
    """
    for i, sec in enumerate(sections):
        if sec.kind != SectionKind.UNCERTAIN or sec.page_num != 0:
            continue
        lines = sec.text.split("\n")
        abs_idx = None
        for j, ln in enumerate(lines):
            if _ABSTRACT_LINE_RE.match(ln.strip()):
                abs_idx = j
                break
        if abs_idx is None or abs_idx == 0:
            continue

        abs_end = len(lines)
        for j in range(abs_idx + 1, len(lines)):
            stripped = lines[j].strip()
            if not stripped:
                continue
            if _NEXT_HEADING_RE.match(stripped) or _ABSTRACT_LINE_RE.match(stripped):
                abs_end = j
                break

        # Trim trailing blank lines from the abstract block.
        while abs_end > abs_idx + 1 and not lines[abs_end - 1].strip():
            abs_end -= 1

        abstract_block = lines[abs_idx:abs_end]
        rest = lines[:abs_idx] + lines[abs_end:]
        new_text = "\n".join(abstract_block + [""] + rest)
        if new_text != sec.text:
            _log.debug("Reordered Abstract to top of UNCERTAIN section %d", i)
            sections[i] = replace(sec, text=new_text)


def promote_abstract_from_uncertain(sections: list[Section]) -> None:
    """Promote an Abstract heading + body out of a page-0 UNCERTAIN section.

    When dual extraction disagrees on page 0, the Abstract heading and its
    body paragraph can end up merged into a single UNCERTAIN section. This
    prevents downstream phases (strip_pre_content_paragraphs) from
    recognizing Abstract as a content boundary. This function splits the
    UNCERTAIN section into proper HEADING + PARAGRAPH sections so the
    Abstract survives metadata stripping.

    Guards (all must hold for the function to fire):
    - No existing content heading (numbered or known-section name) exists
      on page 0. If one exists, strip_pre_content_paragraphs already has a
      boundary and the Abstract would not be stripped.
    - No existing Abstract HEADING exists on page 0 (a duplicate on a later
      page is caught by dedup_abstract downstream).
    - The abstract body must contain at least 10 words.

    Must run BEFORE strip_pre_content_paragraphs in the pipeline.
    Mutates *sections* in-place.
    """
    # Guard: skip if a content heading already exists on page 0.
    # strip_pre_content_paragraphs uses _is_content_heading as its boundary;
    # if one exists on page 0 it won't strip Abstract-containing sections.
    for sec in sections:
        if sec.page_num != 0:
            continue
        if sec.kind not in (SectionKind.HEADING, SectionKind.TITLE):
            continue
        fl = sec.text.split("\n")[0].strip()
        if not fl:
            continue
        if _CONTENT_HEADING_NUM_RE.match(fl):
            return
        fl_low = fl.lower().rstrip(":").strip("*_ ")
        if fl_low in KNOWN_SECTIONS:
            return

    i = 0
    while i < len(sections):
        sec = sections[i]
        if sec.kind != SectionKind.UNCERTAIN or sec.page_num != 0:
            i += 1
            continue

        lines = sec.text.split("\n")
        abs_idx = None
        for j, ln in enumerate(lines):
            if _ABSTRACT_LINE_RE.match(ln.strip()):
                abs_idx = j
                break

        if abs_idx is None:
            i += 1
            continue

        # Find end of abstract body: next heading-like line or end of text.
        abs_body_start = abs_idx + 1
        abs_body_end = len(lines)
        for j in range(abs_body_start, len(lines)):
            stripped = lines[j].strip()
            if not stripped:
                continue
            if _NEXT_HEADING_RE.match(stripped) or _ABSTRACT_LINE_RE.match(stripped):
                abs_body_end = j
                break

        # Trim trailing blank lines from body.
        while abs_body_end > abs_body_start and not lines[abs_body_end - 1].strip():
            abs_body_end -= 1

        # Guard: require substantial but not excessive body text.
        # A real abstract is 10-200 words. Huge blobs (>200) are page dumps.
        body_lines = lines[abs_body_start:abs_body_end]
        body_text = "\n".join(body_lines).strip()
        word_count = len(body_text.split())
        if word_count < _MIN_ABSTRACT_BODY_WORDS:
            i += 1
            continue
        if word_count > _MAX_ABSTRACT_BODY_WORDS:
            i += 1
            continue

        # Build the replacement sections.
        replacements: list[Section] = []

        # Pre-abstract text stays as UNCERTAIN (may contain title, metadata).
        pre_lines = lines[:abs_idx]
        pre_text = "\n".join(pre_lines).strip()
        if pre_text:
            replacements.append(replace(
                sec, text=pre_text, kind=SectionKind.UNCERTAIN))

        # Abstract heading.
        replacements.append(Section(
            kind=SectionKind.HEADING,
            text="Abstract",
            confidence=Confidence.MEDIUM,
            heading_level=2,
            page_num=sec.page_num,
        ))

        # Abstract body paragraph.
        if body_text:
            replacements.append(Section(
                kind=SectionKind.PARAGRAPH,
                text=body_text,
                confidence=Confidence.MEDIUM,
                page_num=sec.page_num,
            ))

        # Post-abstract remainder stays as UNCERTAIN.
        post_lines = lines[abs_body_end:]
        post_text = "\n".join(post_lines).strip()
        if post_text:
            replacements.append(replace(
                sec, text=post_text, kind=SectionKind.UNCERTAIN))

        if replacements:
            _log.debug(
                "Promoted Abstract from UNCERTAIN section %d into %d parts",
                i, len(replacements))
            sections[i:i + 1] = replacements
            i += len(replacements)
        else:
            i += 1

        # Only process the first matching UNCERTAIN section.
        break
