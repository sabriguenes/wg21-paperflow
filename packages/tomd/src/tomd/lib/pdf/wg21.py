"""WG21-specific metadata extraction from PDF blocks.

Parses the metadata block at the top of WG21 papers (document number,
date, audience, reply-to) from the raw MuPDF block structure, before
table detection or structuring runs.
"""

import logging
import re

from .. import (
    strip_format_chars, EMAIL_RE, parse_author_lines,
    deobfuscate_email, enrich_reply_to_names, normalize_date,
)
from .types import Block

_log = logging.getLogger(__name__)

# Guards for wg21 pre-label title continuation (mirrors structure.py guards).
_KNOWN_CONT_SKIP = frozenset({
    "abstract", "revisions", "contents", "foreword", "agenda",
    "table of contents", "tony table", "introduction",
    "proposed wording", "motivation", "overview",
})
_SECTION_NUM_LIKE = re.compile(r"^\d+[\.\)]\s")
_SEPARATOR_LINE = re.compile(r"^[=\-_~*]{3,}$")
_DOC_NO_LIKE = re.compile(r"(?:doc|document)\b.*\b(?:no|number|#)\b", re.IGNORECASE)

_LABEL_RE = re.compile(
    r"(Document\s*(?:Number|No\.?|#)|Doc\.?\s*No\.?|Title|Date|Intent|Audience|Subgroup|"
    r"Reply[- ]?to|Authors?|Editors?|Co-?authors?|Target|Project|"
    r"E-?mails?|Issues?|Previous|Follow[- ]?up(?:\s+to)?|Source|Reference|Contributors?)\s*:",
    re.IGNORECASE,
)

# Bare labels without colon (Scrivener-style PDFs place label alone on a line).
_BARE_LABEL_RE = re.compile(
    r"^(Document|Title|Date|Intent|Audience|Subgroup|Reply[- ]?to|Authors?|Editors?|"
    r"Co-?authors?|Target|Project|Issues?|Previous|Follow[- ]?up|Contributors?)$",
    re.IGNORECASE,
)

_DOC_NUM_VALUE_RE = re.compile(
    r"([DPN]\d{3,5}(?:R\d+)?)",
    re.IGNORECASE,
)

_PARENS_RE = re.compile(r"[()]")

_NOT_A_TITLE = re.compile(
    r"^(?:Abstract|Contents|Table\s+of\s+Contents|Introduction|"
    r"Revision\s+History|Acknowledgements?|References|Appendix|"
    r"Scope|Overview|Motivation|Summary|Preamble|Changelog|"
    r"Doc\.?\s*(?:No\.?|Number|#)\s*:)$",
    re.IGNORECASE,
)

_BARE_DATE_RE = re.compile(
    r"^(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{1,2},?\s+\d{4}$",
    re.IGNORECASE,
)

_INLINE_DATE_RE = re.compile(
    r"[NDP]\d{3,5}(?:R\d+)?\s*[-\u2013\u2014]\s*(\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)

# Maximum number of continuation blocks consumed after a reply-to label.
REPLY_TO_CONTINUATION_CAP = 5


_BULLET_CHARS = frozenset("●○◆◇▪▫•‣⁃\u200b")


def _strip_bullets(text: str) -> str:
    """Strip leading bullet characters and zero-width spaces."""
    i = 0
    while i < len(text) and text[i] in _BULLET_CHARS:
        i += 1
    return text[i:].lstrip()


def _clean(text: str) -> str:
    """Strip zero-width chars and whitespace."""
    return strip_format_chars(text).strip()


def _parse_authors(lines: list[str]) -> list[str]:
    """Parse author name + email from lines into 'Name <email>' entries."""
    def _clean_author(text):
        return _PARENS_RE.sub("", _clean(text)).strip()

    return parse_author_lines(
        lines,
        clean_line=_clean_author,
        skip_line=lambda line: bool(_LABEL_RE.match(line)) or line.strip().isdigit(),
    )


def _is_already_present(entry: str, existing: list[str]) -> bool:
    """Check if entry (bare name or name+email) is already represented."""
    if entry in existing:
        return True
    entry_lower = entry.lower().strip()
    for ex in existing:
        ex_lower = ex.lower().strip()
        if "<" in ex_lower and entry_lower == ex_lower.split("<")[0].strip():
            return True
        if "<" in entry_lower and ex_lower == entry_lower.split("<")[0].strip():
            return True
    return False


def _store_field(metadata: dict, label: str, value_lines: list[str]) -> None:
    """Store a parsed metadata field into the dict."""
    label_lower = label.lower().strip()

    if label_lower == "title":
        value = _clean(" ".join(value_lines))
        if value:
            metadata["title"] = value
    elif "document" in label_lower or label_lower.startswith("doc"):
        value = _clean(" ".join(value_lines))
        m = _DOC_NUM_VALUE_RE.search(value)
        if m:
            metadata["document"] = m.group(1).upper()
    elif label_lower == "date":
        value = _clean(" ".join(value_lines))
        parsed = normalize_date(value)
        if parsed:
            metadata["date"] = parsed
        else:
            _log.debug("Date label found but could not parse: %r", value)
    elif label_lower == "intent":
        value = _clean(" ".join(value_lines)).lower()
        if value:
            metadata["intent"] = value
    elif label_lower in ("audience", "subgroup", "target"):
        value = _clean(" ".join(value_lines))
        if value:
            # Strip author contamination: when the next line after audience
            # has no label separator, its text (name + email) bleeds in.
            # Truncate at angle bracket or email, then at double-space
            # (indicates merged PDF lines where author name bled in).
            angle_idx = value.find("<")
            email_m = EMAIL_RE.search(value)
            cut = None
            if email_m:
                cut = email_m.start()
            if angle_idx >= 0 and (cut is None or angle_idx < cut):
                cut = angle_idx
            if cut is not None:
                value = value[:cut].rstrip(" ,")
            if "  " in value:
                value = value.split("  ", 1)[0].rstrip(" ,")
            if not value:
                return
            # Target is a fallback: only set audience when no explicit
            # Audience/Subgroup field was already extracted.
            if label_lower == "target" and "audience" in metadata:
                pass
            else:
                metadata["audience"] = value
    elif "reply" in label_lower or "author" in label_lower or label_lower in ("editor", "editors"):
        is_explicit_reply_to = "reply" in label_lower
        authors = _parse_authors(value_lines)
        if authors:
            # Track all author-like names for post-pass enrichment.
            all_names = metadata.get("_author_names", [])
            for a in authors:
                if "<" not in a and "@" not in a and a not in all_names:
                    all_names.append(a)
            metadata["_author_names"] = all_names

            if is_explicit_reply_to:
                # Explicit Reply-to label: store directly, upgrading
                # bare-name fallback entries with email-bearing ones.
                existing = metadata.get("reply-to", [])
                for entry in authors:
                    has_email = "<" in entry or "@" in entry
                    upgraded = False
                    if has_email:
                        entry_name = entry.split("<")[0].strip()
                        for idx, ex in enumerate(existing):
                            ex_bare = "<" not in ex and "@" not in ex
                            if ex_bare and ex.strip().lower() == entry_name.lower():
                                existing[idx] = entry
                                upgraded = True
                                break
                    if not upgraded and not _is_already_present(entry, existing):
                        existing.append(entry)
                metadata["reply-to"] = existing
            elif "reply-to" not in metadata:
                # Author/Editor/Co-author: only as fallback when no
                # explicit Reply-to was extracted yet (aligns with
                # HTML_ARCH "Reply-to wins" principle).
                metadata["reply-to"] = authors
    elif label_lower in ("email", "emails", "e-mail"):
        raw = " ".join(value_lines)
        emails = EMAIL_RE.findall(raw)
        if emails:
            existing = metadata.get("reply-to", [])
            existing_emails = {e.lower() for e in EMAIL_RE.findall(" ".join(existing))}
            new_emails = [e for e in emails if e.lower() not in existing_emails]
            bare_names = [e for e in existing if "<" not in e and "@" not in e]
            if bare_names and len(bare_names) == len(new_emails):
                result = []
                email_iter = iter(new_emails)
                for entry in existing:
                    if entry in bare_names:
                        result.append(f"{entry} <{next(email_iter)}>")
                    else:
                        result.append(entry)
                metadata["reply-to"] = result
            else:
                for email in new_emails:
                    existing.append(f"<{email}>")
                metadata["reply-to"] = existing


_COLOR_Y_TOLERANCE = 5.0


def _lookup_lightness(text_colors: dict[float, float] | None, y: float) -> float:
    """Find the lightness value for the nearest y within tolerance."""
    if not text_colors:
        return 0.0
    best_y = min(text_colors.keys(), key=lambda k: abs(k - y), default=None)
    if best_y is not None and abs(best_y - y) <= _COLOR_Y_TOLERANCE:
        return text_colors[best_y]
    return 0.0


def extract_metadata_from_blocks(blocks: list[Block],
                                 text_colors: dict[float, float] | None = None,
                                 ) -> tuple[dict, set[int]]:
    """Extract WG21 metadata from the first blocks of page 0.

    PDF block-level scan (pathway 2 of 3). Higher precedence than
    structure._extract_metadata; both are merged in convert_pdf with this
    result winning on key conflicts.

    Handles two formats:
      - Scrivener: each field is its own block (label on line 0, value on line 1+)
      - Google Docs: multiple fields in one block (each line has label: value)

    Title is chosen by two signals: largest font size (primary) and
    darkest color (secondary, via space-color proxy for Type 3 fonts).

    Returns (metadata_dict, consumed_block_indices).
    Metadata dict keys: "title", "document", "date", "intent", "audience", "reply-to".
    All keys are optional; only fields found in the PDF are included.
    "reply-to" value is a list of "Name <email>" strings.
    """
    metadata: dict = {}
    consumed: set[int] = set()

    page0_blocks = [(i, b) for i, b in enumerate(blocks) if b.page_num == 0]

    pre_label_blocks: list[tuple[int, float, float, str]] = []
    for i, block in page0_blocks:
        if not block.lines:
            continue
        has_label = any(
            _LABEL_RE.match(_strip_bullets(_clean(ln.text)))
            or _BARE_LABEL_RE.match(_strip_bullets(_clean(ln.text)))
            for ln in block.lines
        )
        if has_label:
            break
        content_lines = [
            _clean(ln.text).replace("\n", " ")
            for ln in block.lines if _clean(ln.text)
        ]
        if not content_lines:
            continue
        content_lines = [
            cl for cl in content_lines
            if not _DOC_NUM_VALUE_RE.fullmatch(cl.strip())
            and not (len(cl.strip()) <= 2 and cl.strip().isalpha())
        ]
        if not content_lines:
            continue
        joined = " ".join(content_lines)
        if _NOT_A_TITLE.match(joined):
            continue
        if _BARE_DATE_RE.match(joined):
            parsed = normalize_date(joined)
            if parsed and "date" not in metadata:
                metadata["date"] = parsed
                consumed.add(i)
            continue
        if block.font_size > 0:
            lightness = _lookup_lightness(text_colors, block.bbox[1])
            pre_label_blocks.append(
                (i, block.font_size, lightness, joined))

    title_idx = None
    if pre_label_blocks:
        best = max(pre_label_blocks, key=lambda x: (x[1], -x[2]))
        best_pos = next(
            i for i, e in enumerate(pre_label_blocks) if e is best
        )
        # Walk forward from best through contiguous pre-label blocks
        # that share font size and lightness. Stop at the first block
        # that fails any guard: digit-only, separator, section number,
        # known section name, author name heuristic.
        _CONT_TOL = 0.05
        _LIGHT_TOL = 0.15
        title_parts = [best[3]]
        title_first_idx = best[0]
        prev_block_idx = best[0]
        for entry in pre_label_blocks[best_pos + 1:]:
            if entry[0] != prev_block_idx + 1:
                break
            txt = entry[3].strip()
            if not txt or txt.isdigit():
                break
            fs_ok = best[1] > 0 and abs(entry[1] - best[1]) / best[1] <= _CONT_TOL
            light_ok = abs(entry[2] - best[2]) <= _LIGHT_TOL
            if not (fs_ok and light_ok):
                break
            low = txt.lower().rstrip(":")
            if low in _KNOWN_CONT_SKIP:
                break
            if _SECTION_NUM_LIKE.match(txt):
                break
            if _SEPARATOR_LINE.fullmatch(txt):
                break
            if _DOC_NO_LIKE.search(txt):
                break
            title_parts.append(txt)
            prev_block_idx = entry[0]
        title_idx = (title_first_idx, " ".join(title_parts))
        for entry in pre_label_blocks:
            consumed.add(entry[0])

    label_block_ids: set[int] = set()

    for i, block in page0_blocks:
        if not block.lines:
            continue

        found_any = False

        for li, line in enumerate(block.lines):
            line_text = _clean(line.text)
            if not line_text:
                continue

            stripped_text = _strip_bullets(line_text)
            m = _LABEL_RE.match(stripped_text)
            bare_m = None if m else _BARE_LABEL_RE.match(stripped_text)
            if not m and not bare_m:
                continue

            found_any = True
            label = m.group(1) if m else bare_m.group(1)
            remainder = stripped_text[m.end():].strip() if m else ""

            value_lines = []
            if remainder:
                value_lines.append(remainder)

            for vl in block.lines[li + 1:]:
                vl_text = _strip_bullets(_clean(vl.text))
                if _LABEL_RE.match(vl_text) or _BARE_LABEL_RE.match(vl_text):
                    break
                value_lines.append(vl_text)

            _store_field(metadata, label, value_lines)

        if found_any:
            consumed.add(i)
            label_block_ids.add(i)
            if "reply" in " ".join(_clean(ln.text) for ln in block.lines).lower():
                continuation_count = 0
                for j, next_block in page0_blocks:
                    if j <= i:
                        continue
                    if j in consumed:
                        continue
                    if continuation_count >= REPLY_TO_CONTINUATION_CAP:
                        break
                    next_text = _strip_bullets(_clean(next_block.lines[0].text)) if next_block.lines else ""
                    if not next_text or _LABEL_RE.match(next_text):
                        break
                    has_email = any(EMAIL_RE.search(ln.text) for ln in next_block.lines)
                    if has_email:
                        extra_authors = _parse_authors([ln.text for ln in next_block.lines])
                        if extra_authors:
                            existing = metadata.get("reply-to", [])
                            for entry in extra_authors:
                                email_m = EMAIL_RE.search(entry)
                                is_bare_email = (
                                    email_m and entry.strip().strip("<>") == email_m.group(0)
                                )
                                if email_m and is_bare_email:
                                    email_addr = email_m.group(0)
                                    paired = False
                                    for idx, ex in enumerate(existing):
                                        if "<" not in ex and "@" not in ex:
                                            existing[idx] = f"{ex} <{email_addr}>"
                                            paired = True
                                            break
                                    if not paired and not _is_already_present(entry, existing):
                                        existing.append(entry)
                                elif entry is not None and not _is_already_present(entry, existing):
                                    existing.append(entry)
                            metadata["reply-to"] = existing
                            consumed.add(j)
                            continuation_count += 1
                    else:
                        break

    if title_idx is not None and "title" not in metadata:
        idx, title_text = title_idx
        if title_text:
            metadata["title"] = title_text
            consumed.add(idx)

    if "date" not in metadata:
        for i, block in page0_blocks:
            for ln in block.lines:
                m = _INLINE_DATE_RE.search(_clean(ln.text))
                if m:
                    metadata["date"] = m.group(1)
                    break
            if "date" in metadata:
                break

    if "date" not in metadata:
        nearby = set(label_block_ids)
        for idx in list(label_block_ids):
            nearby.add(idx + 1)
        pre_label_ids = consumed - label_block_ids
        nearby -= pre_label_ids
        for i, block in page0_blocks:
            if i not in nearby:
                continue
            for ln in block.lines:
                lt = _clean(ln.text)
                if _LABEL_RE.match(lt):
                    continue
                if _DOC_NUM_VALUE_RE.search(lt):
                    continue
                parsed = normalize_date(lt)
                if parsed:
                    metadata["date"] = parsed
                    break
            if "date" in metadata:
                break

    if "reply-to" not in metadata:
        _HEADING_RE = re.compile(
            r"^(?:Abstract|Contents|Table\s+of\s+Contents|Introduction|"
            r"Foreword|Revision|Preamble|Overview|Motivation)\b",
            re.IGNORECASE,
        )
        _EMAIL_LINE_RE = re.compile(
            r"^(.+?)\s*[<(](" + EMAIL_RE.pattern + r")[)>]\s*$"
        )
        for i, block in page0_blocks:
            first_text = _strip_bullets(_clean(
                block.lines[0].text)) if block.lines else ""
            if _HEADING_RE.match(first_text):
                break
            for ln in block.lines:
                lt = _strip_bullets(_clean(ln.text))
                if _LABEL_RE.match(lt):
                    continue
                m = _EMAIL_LINE_RE.match(lt)
                if m:
                    name = m.group(1).strip().strip("<>").strip()
                    email = m.group(2)
                    entry = f"{name} <{email}>" if name else f"<{email}>"
                    existing = metadata.get("reply-to", [])
                    if entry not in existing:
                        metadata["reply-to"] = existing + [entry]
                    continue
                deob_result = deobfuscate_email(lt)
                if deob_result:
                    deob_email, (match_start, _) = deob_result
                    name = lt[:match_start].strip().rstrip(",").strip()
                    entry = f"{name} <{deob_email}>" if name else f"<{deob_email}>"
                    existing = metadata.get("reply-to", [])
                    if entry not in existing:
                        metadata["reply-to"] = existing + [entry]

    # Post-pass: pair bare <email> entries with author names.  Name
    # candidates come from _author_names (accumulated from Author/Editor
    # labels) plus any bare names still in reply-to.
    if "reply-to" in metadata:
        all_names = metadata.pop("_author_names", [])
        bare_in_rt = [
            e for e in metadata["reply-to"]
            if "<" not in e and "@" not in e
        ]
        candidates = list(all_names)
        for n in bare_in_rt:
            if n not in candidates:
                candidates.append(n)
        if candidates:
            metadata["reply-to"] = enrich_reply_to_names(
                metadata["reply-to"], candidates,
            )
    else:
        metadata.pop("_author_names", None)

    if consumed:
        _log.debug("Extracted metadata: %s (consumed blocks %s)",
                    list(metadata.keys()), sorted(consumed))

    return metadata, consumed
