# Copyright 2026 The C++ Alliance, Inc.
# SPDX-License-Identifier: BSL-1.0
"""Metadata field extraction from PDF sections and blocks.

Pathway 1 (section-line regex scan) and safety-net email fallback.
"""

import logging
import re
from dataclasses import replace
from pathlib import Path

from tomd.lib.shared import DATE_RE, DOC_NUM_RE, EMAIL_RE
from tomd.lib.pdf.types import (
    Section, SectionKind,
    SECTION_NUM_RE, DOC_FIELD_RE, REPLY_TO_RE, AUDIENCE_RE,
    KNOWN_SECTIONS,
)

_log = logging.getLogger(__name__)

_PID_BASE_RE = re.compile(r"([DPN])(\d{3,5})(?:R(\d+))?", re.IGNORECASE)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_EMAIL_INLINE_RE = re.compile(r"\S+@\S+\.\S+")
# Collapses any run of whitespace (including newlines) into a single space.
# Distinct from cleanup._MULTI_SPACE_RE which targets only spaces and tabs.
_MULTI_SPACE_RE = re.compile(r"\s{2,}")
_LEADING_TRAILING_COMMA_RE = re.compile(r"^[,\s]+|[,\s]+$")


def extract_metadata(sections: list[Section]) -> tuple[dict[str, str | list[str]], list[Section]]:
    """Pull WG21 metadata fields from early sections into a dict.

    PDF section line scan (pathway 1 of 3). Lower precedence than
    wg21.extract_metadata_from_blocks; both are merged in convert_pdf.

    Returns (metadata_dict, remaining_sections).
    """
    meta: dict[str, str | list[str]] = {}
    remaining = []
    metadata_zone = True

    for sec in sections:
        if sec.kind == SectionKind.UNCERTAIN:
            remaining.append(sec)
            continue

        text = sec.text.strip()
        if not text:
            remaining.append(sec)
            continue

        if metadata_zone:
            consumed = False
            for line_text in text.split("\n"):
                lt = line_text.strip()
                if not lt:
                    continue

                m = DOC_FIELD_RE.match(lt)
                if m:
                    meta["document"] = m.group(1).upper()
                    consumed = True
                    continue

                m = DATE_RE.search(lt)
                if m and "date" not in meta and not SECTION_NUM_RE.match(lt):
                    if lt.lower().startswith("date"):
                        meta["date"] = m.group(1)
                        consumed = True
                        continue

                m = REPLY_TO_RE.match(lt)
                if m:
                    raw = m.group(1).strip()
                    raw = _HTML_TAG_RE.sub("", raw)
                    raw = _EMAIL_INLINE_RE.sub("", raw)
                    raw = _MULTI_SPACE_RE.sub(" ", raw).strip()
                    raw = _LEADING_TRAILING_COMMA_RE.sub("", raw)
                    if raw:
                        if "reply-to" not in meta:
                            meta["reply-to"] = []
                        meta["reply-to"].append(raw)
                    consumed = True
                    continue

                m = AUDIENCE_RE.match(lt)
                if m:
                    meta["audience"] = m.group(1).strip()
                    consumed = True
                    continue

            if consumed:
                leftover = []
                for line_text in text.split("\n"):
                    lt = line_text.strip()
                    if not lt:
                        continue
                    if (DOC_FIELD_RE.match(lt) or REPLY_TO_RE.match(lt)
                            or AUDIENCE_RE.match(lt)):
                        continue
                    if lt.lower().startswith("date") and DATE_RE.search(lt):
                        continue
                    leftover.append(lt)
                if leftover:
                    remaining.append(replace(sec, text="\n".join(leftover)))
                continue

            alpha = [c for c in text if c.isalpha()]
            if alpha and all(c.isupper() for c in alpha) and len(text.split()) <= 3:
                # Guard: multi-line sections like "I.\nINTRODUCTION" have a
                # known section name on a subsequent line. Check each line
                # individually so the Roman-numeral prefix doesn't hide the
                # match against KNOWN_SECTIONS.
                is_known = any(
                    ln.strip().lower().rstrip(":") in KNOWN_SECTIONS
                    for ln in text.split("\n") if ln.strip()
                )
                if not is_known:
                    if text.lower().rstrip(":") not in KNOWN_SECTIONS:
                        _log.debug("Consumed category label in metadata zone: %r", text)
                        continue
                if is_known:
                    metadata_zone = False

            if SECTION_NUM_RE.match(text.split("\n")[0]):
                metadata_zone = False

        remaining.append(sec)

    return meta, remaining


def enrich_pdf_reply_to(
    metadata: dict, blocks: list, *, max_lines: int = 30
) -> None:
    """Safety-net post-pass: scan page 0 for emails missed by labeled extractors.

    Mirrors the HTML _enrich_reply_to pattern. Runs after wg21/structure merge.
    """
    if not isinstance(metadata.get("reply-to"), list):
        metadata["reply-to"] = []

    page0_lines: list[str] = []
    for b in blocks:
        if b.page_num != 0:
            continue
        for ln in b.lines:
            page0_lines.append(ln.text.strip())
            if len(page0_lines) >= max_lines:
                break
        if len(page0_lines) >= max_lines:
            break

    existing = metadata.get("reply-to", [])
    existing_joined = " ".join(existing)
    existing_emails = {e.lower() for e in EMAIL_RE.findall(existing_joined)}

    page0_text = "\n".join(page0_lines)
    page0_emails = EMAIL_RE.findall(page0_text)
    missing = [e for e in page0_emails if e.lower() not in existing_emails]
    if not missing:
        return

    _NAMED_EMAIL_RE = re.compile(
        r"([A-Z][A-Za-z.''\- ]+?)\s*[<(](" + EMAIL_RE.pattern + r")[)>]"
    )
    _BARE_EMAIL_RE = re.compile(
        r"^\s*[<(]?(" + EMAIL_RE.pattern + r")[)>]?\s*$"
    )
    line_map: dict[str, str] = {}
    for idx, line in enumerate(page0_lines):
        for m in _NAMED_EMAIL_RE.finditer(line):
            name = m.group(1).strip().rstrip(",/;")
            line_map[m.group(2).lower()] = name
        m = _BARE_EMAIL_RE.match(line)
        if m and m.group(1).lower() not in line_map:
            if idx > 0:
                prev = page0_lines[idx - 1].strip().rstrip(":")
                if prev and "@" not in prev and "<" not in prev:
                    line_map[m.group(1).lower()] = prev

    paired: set[str] = set()
    for email in missing:
        name = line_map.get(email.lower(), "")
        if name:
            for idx, entry in enumerate(existing):
                if entry == name or (
                    "<" not in entry and "@" not in entry
                    and name.lower().startswith(entry.lower())
                ):
                    existing[idx] = f"{entry} <{email}>"
                    paired.add(email.lower())
                    break

    for email in missing:
        if email.lower() in paired:
            continue
        name = line_map.get(email.lower(), "")
        if name:
            existing.append(f"{name} <{email}>")
        else:
            existing.append(f"<{email}>")
    metadata["reply-to"] = existing


def override_revision_from_filename(metadata: dict, path: Path) -> None:
    """Override document revision from filename when the base paper number
    matches but revisions differ. Skip when the extracted document has a
    D-prefix (draft), since D/P mismatches are expected WG21 workflow."""
    if "document" not in metadata:
        return
    doc_m = _PID_BASE_RE.search(metadata["document"])
    stem_m = _PID_BASE_RE.search(path.stem)
    if not doc_m or not stem_m:
        return
    if doc_m.group(1).upper() == "D":
        return
    if doc_m.group(2) != stem_m.group(2):
        return
    stem_rev = stem_m.group(3)
    doc_rev = doc_m.group(3)
    if stem_rev is not None and stem_rev != doc_rev:
        prefix = stem_m.group(1).upper()
        number = stem_m.group(2)
        metadata["document"] = f"{prefix}{number}R{stem_rev}"
        _log.debug("Overrode document revision from filename: %s -> %s",
                   f"{doc_m.group(0)}", metadata["document"])


_TITLE_BOILERPLATE_RE = re.compile(
    r"^(?:Microsoft\s+Word|Document\d|Untitled|"
    r"[DPN]\d{3,5}(?:R\d+)?|Presentation\d?)$",
    re.IGNORECASE,
)

_AUTHOR_BOILERPLATE_RE = re.compile(
    r"^(?:Admin|Scanner|Unknown|Default|User|Owner|"
    r"Microsoft|Adobe|LaTeX|TeX|MiKTeX|pdfTeX|dvips|"
    r"Acrobat|LibreOffice|OpenOffice|Google|Apple|"
    r"[a-z0-9._-]+\.(?:pdf|doc|docx|tex))$",
    re.IGNORECASE,
)


def apply_pdf_metadata_fallbacks(
    metadata: dict,
    path: Path,
    pdf_info_date: str | None,
    pdf_info_title: str | None,
    doc_metadata: dict,
    sections: list[Section],
    all_mupdf_blocks: list,
    title_pid_prefix_re: re.Pattern,
) -> None:
    """Fill missing metadata fields from PDF-info and filename fallbacks.

    Called after primary extraction (section-line scan + wg21 block scan)
    has populated what it can. This function applies lower-precedence
    fallbacks: document from filename, date from PDF info, title from
    first heading or PDF info, reply-to from PDF info author, and
    email enrichment from page-0 blocks.

    Mutates *metadata* in place.
    """
    if "document" not in metadata:
        stem_match = DOC_NUM_RE.search(path.stem)
        if stem_match:
            metadata["document"] = stem_match.group(1).upper()

    if "date" not in metadata and pdf_info_date:
        metadata["date"] = pdf_info_date

    override_revision_from_filename(metadata, path)

    if not metadata.get("title"):
        for sec in sections:
            if sec.kind == SectionKind.HEADING:
                first_line = sec.text.split("\n")[0].strip().lstrip("# ").strip()
                if (first_line
                        and first_line.lower().rstrip(":") not in KNOWN_SECTIONS):
                    metadata["title"] = first_line
                    break

    if not metadata.get("title") and pdf_info_title:
        if not _TITLE_BOILERPLATE_RE.match(pdf_info_title):
            metadata["title"] = pdf_info_title

    if metadata.get("title"):
        stripped = title_pid_prefix_re.sub("", metadata["title"]).strip()
        if stripped:
            metadata["title"] = stripped

    if "reply-to" not in metadata:
        pdf_info_author = (doc_metadata.get("author") or "").strip()
        if pdf_info_author and len(pdf_info_author) >= 4:
            if not _AUTHOR_BOILERPLATE_RE.match(pdf_info_author):
                metadata["reply-to"] = [pdf_info_author]

    enrich_pdf_reply_to(metadata, all_mupdf_blocks)
