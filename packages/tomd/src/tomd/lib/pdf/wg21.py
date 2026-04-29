"""WG21-specific metadata extraction from PDF blocks.

Parses the metadata block at the top of WG21 papers (document number,
date, audience, reply-to) from the raw MuPDF block structure, before
table detection or structuring runs.
"""

import logging
import re

from .. import strip_format_chars, EMAIL_RE, DATE_RE, parse_author_lines
from .types import Block

_log = logging.getLogger(__name__)

_LABEL_RE = re.compile(
    r"(Document\s*(?:Number|No\.?|#)|Doc\.?\s*No\.?|Title|Date|Audience|Subgroup|"
    r"Reply[- ]?to|Authors?|Editors?|Target|Project|E-?mails?)\s*:",
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
        skip_line=lambda l: bool(_LABEL_RE.match(l)) or l.strip().isdigit(),
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
        m = DATE_RE.search(value)
        if m:
            metadata["date"] = m.group(0)
    elif label_lower in ("audience", "subgroup", "target"):
        value = _clean(" ".join(value_lines))
        if value:
            metadata["audience"] = value
    elif "reply" in label_lower or label_lower in ("author", "authors", "editor", "editors"):
        authors = _parse_authors(value_lines)
        if authors:
            existing = metadata.get("reply-to", [])
            existing_has_emails = any(
                "<" in e or "@" in e for e in existing
            )
            if existing and existing_has_emails:
                for entry in authors:
                    if not _is_already_present(entry, existing):
                        existing.append(entry)
                metadata["reply-to"] = existing
            else:
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
    Metadata dict keys: "title", "document", "date", "audience", "reply-to".
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
        has_label = any(_LABEL_RE.match(_strip_bullets(_clean(ln.text))) for ln in block.lines)
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
        if _NOT_A_TITLE.match(joined) or _BARE_DATE_RE.match(joined):
            continue
        if block.font_size > 0:
            lightness = _lookup_lightness(text_colors, block.bbox[1])
            pre_label_blocks.append(
                (i, block.font_size, lightness, joined))

    title_idx = None
    if pre_label_blocks:
        best = max(pre_label_blocks, key=lambda x: (x[1], -x[2]))
        title_idx = (best[0], best[3])
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
            if not m:
                continue

            found_any = True
            label = m.group(1)
            remainder = stripped_text[m.end():].strip()

            value_lines = []
            if remainder:
                value_lines.append(remainder)

            for vl in block.lines[li + 1:]:
                vl_text = _strip_bullets(_clean(vl.text))
                if _LABEL_RE.match(vl_text):
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
                m = DATE_RE.search(lt)
                if m and not _DOC_NUM_VALUE_RE.search(lt):
                    metadata["date"] = m.group(0)
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

    if consumed:
        _log.debug("Extracted metadata: %s (consumed blocks %s)",
                    list(metadata.keys()), sorted(consumed))

    return metadata, consumed
