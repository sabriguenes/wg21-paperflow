# Copyright 2026 The C++ Alliance, Inc.
# SPDX-License-Identifier: BSL-1.0
"""Strip metadata headings from page 0 that duplicate front-matter content."""

import logging
import re

from tomd.lib.pdf.types import KNOWN_SECTIONS, Section, SectionKind

_log = logging.getLogger(__name__)


def _matches_author_name(text: str, author_tokens: set[str]) -> bool:
    """True if *text* looks like a single author name matching reply-to tokens."""
    if "," in text:
        return False
    clean_name = re.sub(r"[*_`()\[\]<>]", " ", text)
    clean_name = re.sub(r"\S+@\S+", " ", clean_name)
    clean_name = re.sub(r"https?://\S+", " ", clean_name)
    name_tokens = [t.lower() for t in clean_name.split()
                   if len(t) >= 3 and t[0].isalpha()]
    if not (1 <= len(name_tokens) <= 4):
        return False
    match_count = sum(1 for t in name_tokens if t in author_tokens)
    return match_count >= 1 and match_count / len(name_tokens) >= 0.5


# Patterns for metadata headings that duplicate front-matter content.
_META_DOC_HEADING_RE = re.compile(
    r"^(?:Doc\.?\s*(?:No\.?|Number|#)|Document\s*(?:Number|No\.?)?|"
    r"Paper\s*Number)\s*:",
    re.IGNORECASE,
)
_META_DATE_HEADING_RE = re.compile(r"^Date\s*:", re.IGNORECASE)
_SEPARATOR_HEADING_RE = re.compile(r"^[=\-_~*]{3,}$")
_WG21_CATEGORY_LABEL_RE = re.compile(
    r"^(?:Programming\s+Language\s+C\+\+|"
    r"ISO/?IEC\s+JTC\s*1|"
    r"WG\s*21\s+(?:PROPOSAL|PAPER))\s*$",
    re.IGNORECASE,
)
_META_FIELD_LABEL_RE = re.compile(
    r"^(?:Reply[- ]?to|Audience|Target|Project|"
    r"Authors?|Editors?|Co-?authors?|Subgroup|Source|"
    r"E-?mails?|Ship\s+vehicle|Targeted\s+for)\s*:",
    re.IGNORECASE,
)
_META_AUTHOR_LIST_MIN_ITEMS = 3
_META_AUTHOR_LIST_MAX_ITEM_WORDS = 4

# Bare date heading: "March 26, 2026" or "26 March 2026" without a label.
_BARE_DATE_HEADING_RE = re.compile(
    r"^(?:(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{1,2},?\s+\d{4}|"
    r"\d{1,2}\s+(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{4}|"
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2})$",
    re.IGNORECASE,
)

# Content-heading number pattern: "1.", "1.2.", "1.2.3" followed by a space.
# Leading number limited to 1-2 digits to reject year-based identifiers like
# "2026.2 – Brno" (P1000R8 schedule entries) which would otherwise match.
# Arabic numerals only; Roman numerals excluded to avoid false positives.
_CONTENT_HEADING_NUM_RE = re.compile(r"^\d{1,2}(?:\.\d+)*\.?\s")


def _title_stem_words(s: str) -> set[str]:
    """Return 6-char stems of alphabetic words (length >= 3) for fuzzy title matching."""
    s = re.sub(r"[^a-zA-Z\s]", " ", s.lower())
    return {w[:6] for w in s.split() if len(w) >= 3}


def _is_content_heading(sec: Section) -> bool:
    """True when a heading/title section is a real content heading.

    Content headings are numbered sections (``1. Foo``, ``1.1. Bar``) or
    known section names (Abstract, Motivation, ...).  Used by the
    post-strip paragraph cleanup pass to find the boundary between
    metadata and body content on page 0.
    """
    if sec.kind not in (SectionKind.HEADING, SectionKind.TITLE):
        return False
    fl = sec.text.split("\n")[0].strip()
    if not fl:
        return False
    if _CONTENT_HEADING_NUM_RE.match(fl):
        return True
    fl_low = fl.lower().rstrip(":")
    if fl_low in KNOWN_SECTIONS:
        return True
    return False


def strip_metadata_headings(sections: list[Section],
                            metadata: dict) -> int:
    """Remove page-0 heading sections that duplicate front-matter metadata.

    Strips headings that contain document numbers, dates, author lists,
    category labels, or separator lines when the corresponding data is
    already captured in the YAML front matter. Only touches headings
    before the first content heading (numbered section or known section
    name like Abstract/Motivation).

    Returns the number of sections removed.
    """
    doc_num = (metadata.get("document") or "").upper()
    date_val = metadata.get("date") or ""
    title_val = metadata.get("title") or ""

    # Build set of author surname tokens from reply-to.
    author_tokens: set[str] = set()
    for entry in metadata.get("reply-to", []):
        name = entry.split("<")[0].strip()
        for token in name.split(","):
            token = token.strip()
            parts = token.split()
            for p in parts:
                cleaned = p.strip(".,;:*_()").lower()
                if len(cleaned) >= 3:
                    author_tokens.add(cleaned)

    # Find the boundary: first heading that is a real content heading
    # (numbered section, known section name, or title). We only strip
    # metadata headings BEFORE this boundary.
    boundary = len(sections)
    for i, sec in enumerate(sections):
        if sec.page_num != 0:
            boundary = i
            break
        if _is_content_heading(sec):
            boundary = i
            break

    to_remove: set[int] = set()
    for i in range(boundary):
        sec = sections[i]
        if sec.kind != SectionKind.HEADING:
            continue

        text = sec.text.strip()
        first_line = text.split("\n")[0].strip()

        # Empty heading (bare "##" with no text).
        if not first_line:
            to_remove.add(i)
            continue

        # Document number heading (Doc. no.: PXXXXRN).
        if doc_num and _META_DOC_HEADING_RE.match(first_line):
            if doc_num in first_line.upper():
                to_remove.add(i)
                continue

        # Date heading (Date: YYYY-MM-DD).
        if date_val and _META_DATE_HEADING_RE.match(first_line):
            to_remove.add(i)
            continue

        # Bare date heading ("March 26, 2026" without label).
        clean_fl = first_line.strip("*_ ")
        if _BARE_DATE_HEADING_RE.match(clean_fl):
            to_remove.add(i)
            continue

        # Paper Number heading (Paper Number: PXXXXRN).
        if doc_num and re.match(r"Paper\s*Number\s*:", first_line, re.IGNORECASE):
            if doc_num in first_line.upper():
                to_remove.add(i)
                continue

        # Separator line (=====, -----, etc.).
        if _SEPARATOR_HEADING_RE.match(first_line):
            to_remove.add(i)
            continue

        # WG21 category label (Programming Language C++, etc.).
        if _WG21_CATEGORY_LABEL_RE.match(first_line):
            to_remove.add(i)
            continue

        # Title echo: heading text matches the front-matter title.
        # Require at least 2 overlapping stems so that a single shared word
        # (e.g. "IS schedule" vs "Proposed C++ IS schedule") does not trigger.
        if title_val:
            hw = _title_stem_words(first_line)
            tw = _title_stem_words(title_val)
            overlap = hw & tw
            if (hw and tw and len(overlap) >= 2
                    and len(overlap) / max(len(hw), len(tw)) >= 0.5):
                to_remove.add(i)
                continue

        # WG21 metadata field label heading (Target:, Audience:, etc.).
        if _META_FIELD_LABEL_RE.match(first_line):
            to_remove.add(i)
            continue

        # Author-list heading: comma-separated person names.
        if "," in first_line:
            clean = first_line.strip("*_ ")
            items = [item.strip() for item in clean.split(",") if item.strip()]
            if len(items) >= _META_AUTHOR_LIST_MIN_ITEMS:
                name_like = sum(
                    1 for item in items
                    if (1 <= len(item.split()) <= _META_AUTHOR_LIST_MAX_ITEM_WORDS
                        and item[0].isupper())
                )
                if name_like / len(items) >= 0.8:
                    to_remove.add(i)
                    continue
            # Token match against known reply-to names.
            if author_tokens:
                tokens = [t.strip(".,;:*_()").lower()
                          for t in clean.replace(",", " ").split()
                          if len(t.strip(".,;:*_()")) >= 3]
                if tokens:
                    match_ratio = sum(1 for t in tokens if t in author_tokens) / len(tokens)
                    if match_ratio >= 0.4:
                        to_remove.add(i)
                        continue

        # Single-author heading matching reply-to tokens.
        if author_tokens and _matches_author_name(first_line, author_tokens):
            to_remove.add(i)
            continue

    # Second pass: high-confidence metadata duplicates on page 0 AFTER
    # the boundary.  Two-column PDFs repeat title/date/author in right column.
    # Track whether body paragraphs have appeared: once real body content
    # exists between the boundary and a heading, that heading is part of
    # the document structure, not a metadata echo.
    seen_body_paragraph = False
    for i in range(boundary, len(sections)):
        sec = sections[i]
        if sec.page_num != 0:
            break
        if sec.kind not in (SectionKind.HEADING, SectionKind.TITLE):
            if sec.kind in (SectionKind.PARAGRAPH, SectionKind.LIST):
                seen_body_paragraph = True
            continue

        first_line = sec.text.strip().split("\n")[0].strip()
        if not first_line:
            continue

        # Title echo (same word-stem overlap check as pass 1).
        # Skip when body paragraphs precede this heading: it is a real
        # sub-heading, not a two-column metadata duplicate.
        if title_val and not seen_body_paragraph:
            hw = _title_stem_words(first_line)
            tw = _title_stem_words(title_val)
            overlap = hw & tw
            if (hw and tw and len(overlap) >= 2
                    and len(overlap) / max(len(hw), len(tw)) >= 0.5):
                to_remove.add(i)
                continue

        # Bare date heading.
        clean_fl = first_line.strip("*_ ")
        if _BARE_DATE_HEADING_RE.match(clean_fl):
            to_remove.add(i)
            continue

        # Single-author name matching reply-to tokens.
        if author_tokens and _matches_author_name(first_line, author_tokens):
            to_remove.add(i)
            continue

    if to_remove:
        _log.info("Stripping %d metadata heading(s) from body: %s",
                  len(to_remove),
                  [sections[i].text.split("\n")[0].strip()[:60] for i in sorted(to_remove)])
        sections[:] = [s for i, s in enumerate(sections) if i not in to_remove]

    return len(to_remove)


def strip_pre_heading_fragments(sections: list[Section]) -> int:
    """Strip non-heading page-0 sections that sit before the first heading.

    After metadata extraction, leftover sections (author names, doc numbers,
    affiliations) would otherwise appear as duplicate body text. This removes
    them. Mutates *sections* in-place.

    Returns the number of sections removed.
    """
    first_heading_idx = next(
        (i for i, s in enumerate(sections)
         if s.kind in (SectionKind.HEADING, SectionKind.TITLE) and s.page_num == 0),
        None,
    )
    if first_heading_idx is None or first_heading_idx == 0:
        return 0
    pre = [sections[i] for i in range(first_heading_idx)
           if sections[i].page_num == 0
           and sections[i].kind not in (SectionKind.HEADING, SectionKind.TITLE)]
    if not pre:
        return 0
    _log.debug("Stripping %d pre-heading metadata section(s) from body", len(pre))
    pre_set = set(id(s) for s in pre)
    sections[:] = [s for s in sections if id(s) not in pre_set]
    return len(pre)


def strip_pre_content_paragraphs(sections: list[Section]) -> int:
    """Strip page-0 paragraphs that sit before the first content heading.

    After strip_metadata_headings removes title-echo headings, metadata
    paragraphs (doc numbers, author affiliations) may become the leading
    sections. These are already captured in YAML front matter and must not
    appear in body. Mutates *sections* in-place.

    Returns the number of sections removed.
    """
    content_idx = next(
        (i for i, s in enumerate(sections) if _is_content_heading(s)),
        None,
    )
    if content_idx is None or content_idx == 0:
        return 0
    meta_paras = [
        sections[i] for i in range(content_idx)
        if sections[i].page_num == 0
        and sections[i].kind not in (SectionKind.HEADING, SectionKind.TITLE)
    ]
    if not meta_paras:
        return 0
    _log.debug(
        "Stripping %d pre-content metadata paragraph(s) from body",
        len(meta_paras),
    )
    para_set = set(id(s) for s in meta_paras)
    sections[:] = [s for s in sections if id(s) not in para_set]
    return len(meta_paras)
