"""Shared text utilities and constants for PDF and HTML converters."""

import re
import unicodedata

_NAMED_ENTITIES = {
    0xC0: "&Agrave;", 0xC1: "&Aacute;", 0xC2: "&Acirc;", 0xC3: "&Atilde;",
    0xC4: "&Auml;", 0xC5: "&Aring;", 0xC6: "&AElig;", 0xC7: "&Ccedil;",
    0xC8: "&Egrave;", 0xC9: "&Eacute;", 0xCA: "&Ecirc;", 0xCB: "&Euml;",
    0xCC: "&Igrave;", 0xCD: "&Iacute;", 0xCE: "&Icirc;", 0xCF: "&Iuml;",
    0xD0: "&ETH;", 0xD1: "&Ntilde;", 0xD2: "&Ograve;", 0xD3: "&Oacute;",
    0xD4: "&Ocirc;", 0xD5: "&Otilde;", 0xD6: "&Ouml;", 0xD8: "&Oslash;",
    0xD9: "&Ugrave;", 0xDA: "&Uacute;", 0xDB: "&Ucirc;", 0xDC: "&Uuml;",
    0xDD: "&Yacute;", 0xDE: "&THORN;", 0xDF: "&szlig;",
    0xE0: "&agrave;", 0xE1: "&aacute;", 0xE2: "&acirc;", 0xE3: "&atilde;",
    0xE4: "&auml;", 0xE5: "&aring;", 0xE6: "&aelig;", 0xE7: "&ccedil;",
    0xE8: "&egrave;", 0xE9: "&eacute;", 0xEA: "&ecirc;", 0xEB: "&euml;",
    0xEC: "&igrave;", 0xED: "&iacute;", 0xEE: "&icirc;", 0xEF: "&iuml;",
    0xF0: "&eth;", 0xF1: "&ntilde;", 0xF2: "&ograve;", 0xF3: "&oacute;",
    0xF4: "&ocirc;", 0xF5: "&otilde;", 0xF6: "&ouml;", 0xF8: "&oslash;",
    0xF9: "&ugrave;", 0xFA: "&uacute;", 0xFB: "&ucirc;", 0xFC: "&uuml;",
    0xFD: "&yacute;", 0xFE: "&thorn;", 0xFF: "&yuml;",
    0x0141: "&Lstrok;", 0x0142: "&lstrok;",
}


def ascii_escape(text: str) -> str:
    """Encode non-ASCII characters as HTML character references.

    Uses named entities for common diacritics (e.g. &uuml; for u-umlaut),
    falls back to numeric references (e.g. &#8212;) for others.
    """
    out = []
    for ch in text:
        cp = ord(ch)
        if cp <= 127:
            out.append(ch)
        elif cp in _NAMED_ENTITIES:
            out.append(_NAMED_ENTITIES[cp])
        else:
            out.append(f"&#{cp};")
    return "".join(out)


FORMAT_CHARS = frozenset(
    chr(c) for c in range(0x110000)
    if unicodedata.category(chr(c)) == 'Cf'
)


def strip_format_chars(text: str) -> str:
    """Remove Unicode format characters (category Cf)."""
    return "".join(c for c in text if c not in FORMAT_CHARS)


import logging as _logging

_dedup_log = _logging.getLogger(__name__)


_MAX_PARAGRAPH_OCCURRENCES = 10
_MIN_DEDUP_LENGTH = 40


def dedup_paragraphs(md: str) -> str:
    """Remove duplicate paragraphs from Markdown text.

    Two passes:
    1. Consecutive identical paragraphs are collapsed to one.
    2. Any paragraph longer than 40 chars appearing more than 3 times
       total is capped at 3 occurrences (keeps the first 3).

    Headings and code fences are never dropped.
    """
    blocks = md.split("\n\n")
    if len(blocks) <= 1:
        return md

    deduped: list[str] = [blocks[0]]
    for block in blocks[1:]:
        if block.strip() != deduped[-1].strip():
            deduped.append(block)

    from collections import Counter
    counts: Counter[str] = Counter()
    result: list[str] = []
    for block in deduped:
        stripped = block.strip()
        is_heading = stripped.startswith("#")
        is_code = stripped.startswith("```")
        if is_heading or is_code or len(stripped) < _MIN_DEDUP_LENGTH:
            result.append(block)
            continue
        counts[stripped] += 1
        if counts[stripped] <= _MAX_PARAGRAPH_OCCURRENCES:
            result.append(block)

    removed = len(blocks) - len(result)
    if removed:
        _dedup_log.debug("Deduplication removed %d repeated paragraph(s)", removed)
    return "\n\n".join(result)


FRONT_MATTER_ORDER = ("title", "document", "revision", "date", "intent", "audience", "reply-to")

_PID_REVISION_RE = re.compile(r"[PDpd]\d{3,5}[Rr](\d+)")


def extract_revision(document: str) -> int | None:
    """Extract revision number from a paper ID like P2583R3 -> 3."""
    m = _PID_REVISION_RE.search(document)
    return int(m.group(1)) if m else None

_TITLE_LABEL_RE = re.compile(
    r"(?:Paper\s*Number|Document(?:\s*Number)?|Title|Authors?|"
    r"Acknowledgements?|Reply[- ]?to|Audience|Date)\s*:",
    re.IGNORECASE,
)

_DOUBLE_ANGLE_RE = re.compile(r"<\s*<([^>]+)>")

_NON_AUTHOR_RE = re.compile(
    r"^(?:Target|Proposed|Wording|Structures?|Version|Contents?|"
    r"Read-Copy|Abstract|Introduction|Overview|Revision)\b",
    re.IGNORECASE,
)


def sanitize_metadata(metadata: dict) -> dict:
    """Clean up extracted metadata values before formatting.

    Fixes: embedded newlines in title, metadata labels in title text,
    double angle brackets in reply-to entries, non-author reply-to values.
    """
    md = dict(metadata)

    if "title" in md:
        title = md["title"]
        title = title.replace("\n", " ").replace("\r", " ")
        title = re.sub(r"\s{2,}", " ", title).strip()
        title_label_m = re.search(r"\bTitle\s*:\s*", title, re.IGNORECASE)
        if title_label_m:
            after_title = title[title_label_m.end():].strip()
            next_label = _TITLE_LABEL_RE.search(after_title)
            if next_label:
                title = after_title[:next_label.start()].rstrip(" ,;")
            elif after_title:
                title = after_title
        else:
            m = _TITLE_LABEL_RE.search(title)
            if m and m.start() > 0:
                title = title[:m.start()].rstrip(" ,;")
            elif m and m.start() == 0:
                after_label = title[m.end():].strip()
                next_label = _TITLE_LABEL_RE.search(after_label)
                if next_label:
                    title = after_label[:next_label.start()].rstrip(" ,;")
                elif after_label:
                    title = after_label
        md["title"] = title.strip()

    if "reply-to" in md and isinstance(md["reply-to"], list):
        cleaned = []
        for entry in md["reply-to"]:
            entry = _DOUBLE_ANGLE_RE.sub(r"<\1>", entry)
            if _NON_AUTHOR_RE.match(entry.strip()):
                continue
            cleaned.append(entry)
        if cleaned:
            md["reply-to"] = cleaned
        else:
            del md["reply-to"]

    if "revision" not in md and "document" in md:
        rev = extract_revision(str(md["document"]))
        if rev is not None:
            md["revision"] = rev

    return md


def _yaml_escape(s: str) -> str:
    """Escape a string for safe inclusion in double-quoted YAML."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _yaml_value(key: str, val) -> str:
    """Format a single YAML value, quoting where needed."""
    if isinstance(val, list):
        items = [f'  - "{_yaml_escape(str(v))}"' for v in val]
        return f"{key}:\n" + "\n".join(items)
    val = str(val) if not isinstance(val, str) else val
    if key == "title" or any(c in val for c in ':{}[]#&*?|>!%@`"\'\n\\'):
        return f'{key}: "{_yaml_escape(val)}"'
    return f"{key}: {val}"


def format_front_matter(metadata: dict) -> str:
    """Format metadata dict as YAML front matter in strict canonical order.

    Strict-order contract: keys are emitted exactly in the order
    ``title, document, date, intent, audience, reply-to``. Missing keys
    are skipped (no placeholders, no blank lines). Unknown keys appear
    after ``audience`` so ``reply-to`` is always last. Callers and
    downstream tools may rely on this ordering for diffs and parsing.

    Title and values containing YAML-special characters are double-quoted
    with backslash-escaping for embedded quotes, backslashes, and newlines.
    Reply-to is a YAML list of double-quoted strings. Returns the empty
    string when ``metadata`` is empty.
    """
    if not metadata:
        return ""
    metadata = dict(metadata)
    if "title" in metadata and isinstance(metadata["title"], str):
        title = metadata["title"].replace("\n", " ")
        title = re.sub(r"\s*::\s*", "::", title)
        title = re.sub(r"  +", " ", title).strip()
        metadata["title"] = title

    if "revision" not in metadata and "document" in metadata:
        rev = extract_revision(str(metadata["document"]))
        if rev is not None:
            metadata["revision"] = rev

    if "intent" not in metadata:
        title = metadata.get("title", "")
        if isinstance(title, str):
            t = title.strip()
            if t.startswith("Info:"):
                metadata["intent"] = "info"
            elif t.startswith("Ask:"):
                metadata["intent"] = "ask"

    lines = ["---"]
    pre_reply_to: list[str] = []
    reply_to_line: str | None = None
    for key in FRONT_MATTER_ORDER:
        if key not in metadata:
            continue
        rendered = _yaml_value(key, metadata[key])
        if key == "reply-to":
            reply_to_line = rendered
        else:
            pre_reply_to.append(rendered)
    lines.extend(pre_reply_to)
    for key, val in metadata.items():
        if key not in FRONT_MATTER_ORDER:
            lines.append(_yaml_value(key, val))
    if reply_to_line is not None:
        lines.append(reply_to_line)
    lines.append("---")
    return "\n".join(lines)


def strip_leading_h1(body: str, title: str = "") -> str:
    """Remove a leading H1 from body text if it duplicates the front-matter title.

    Strips the first non-blank line if it is an ATX H1 (starts with '# ') and
    either matches the front-matter title or is the very first content.
    """
    lines = body.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# ") and not stripped.startswith("## "):
            h1_text = stripped[2:].strip()
            title_clean = title.strip().strip('"').strip()
            if not title_clean or _titles_match(h1_text, title_clean):
                lines[i] = ""
            break
        else:
            break
    result = "\n".join(lines)
    result = re.sub(r"^\n{3,}", "\n\n", result)
    return result


def _titles_match(h1: str, title: str) -> bool:
    """Fuzzy match between an H1 heading and a front-matter title."""
    def normalize(s: str) -> str:
        s = re.sub(r"[^\w\s]", "", s.lower())
        return re.sub(r"\s+", " ", s).strip()
    return normalize(h1) == normalize(title)


_REDUNDANT_META_RE = re.compile(
    r"^Document\s+(?:number|No\.?)\s*:.*$",
    re.IGNORECASE | re.MULTILINE,
)

_REDUNDANT_TABLE_RE = re.compile(
    r"^(?:\|[^\n]*\|\n){2,6}\n*---\n*",
    re.MULTILINE,
)


def strip_redundant_body_meta(md: str) -> str:
    """Remove body lines that duplicate YAML frontmatter metadata.

    Strips:
    - Standalone 'Document number: XXX' lines
    - Pipe tables immediately following front matter that contain only
      metadata fields (Document number, Date, Audience, Reply-to),
      followed by a --- HR separator
    """
    md = _REDUNDANT_META_RE.sub("", md)
    md = _strip_metadata_table(md)
    return md


_META_TABLE_LABELS = frozenset({
    "document", "doc.", "doc", "replaces", "date", "dates", "reply",
    "reply-to", "time", "link", "meeting", "password", "audience",
    "subgroup", "author", "authors", "editor", "editors", "target",
    "targeted", "project", "number", "source", "title", "ship",
    "intent", "revision",
})


def _is_metadata_table(lines: list[str]) -> bool:
    """Return True if a leading pipe table consists entirely of metadata labels.

    Checks that every data row's first cell (lowercased, colon-stripped)
    is a known metadata keyword. Delimiter rows (containing only dashes)
    are skipped.
    """
    for line in lines:
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        raw_cells = [c.strip() for c in stripped.split("|")]
        # raw_cells[0] and [-1] are empty from leading/trailing pipes
        inner = raw_cells[1:-1] if len(raw_cells) >= 3 else raw_cells
        first_cell = inner[0].lower().rstrip(":").rstrip("#").strip() if inner else ""
        if not first_cell:
            continue
        if re.fullmatch(r"[-: ]+", first_cell):
            continue
        words = first_cell.split()
        if not words or words[0] not in _META_TABLE_LABELS:
            return False
    return True


def _strip_metadata_table(md: str) -> str:
    """Strip a leading metadata pipe table + HR from the body after front matter.

    Strips if the table's first-cell keywords are all known metadata labels
    (Document, Date, Audience, Reply-to, Replaces, etc.).
    Tables with non-metadata rows are left intact.
    """
    fm_end = md.find("---", 4)
    if fm_end < 0:
        return md
    body_start = md.find("\n", fm_end)
    if body_start < 0:
        return md
    body_start += 1
    body = md[body_start:].lstrip("\n")

    if not body.startswith("|"):
        return md

    lines = body.split("\n")

    table_end = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            table_end = i + 1
        elif not stripped:
            continue
        else:
            break

    if table_end == 0:
        return md

    table_lines = lines[:table_end]
    if not _is_metadata_table(table_lines):
        return md

    rest = "\n".join(lines[table_end:]).lstrip("\n")
    if rest.startswith("---"):
        rest = rest[3:].lstrip("\n")

    return md[:body_start] + "\n" + rest


ALLOWED_LINK_SCHEMES = frozenset({"http", "https", "mailto"})

EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")


def parse_author_lines(lines, clean_line=None, skip_line=None):
    """Parse author name + email pairs from an iterable of raw line strings.

    Each entry in the returned list is either 'Name <email>' when a name
    and email are found together, or a bare name string when no email
    follows. `clean_line` normalizes each line before processing (default:
    str.strip). `skip_line` returns True for lines that are not author
    content, such as metadata labels (default: never skip).
    """
    if clean_line is None:
        clean_line = str.strip
    if skip_line is None:
        skip_line = lambda _: False

    authors = []
    pending_name = None

    for raw in lines:
        line = clean_line(raw)
        if not line:
            continue

        email_match = EMAIL_RE.search(line)
        if email_match:
            email = email_match.group(0)
            name_part = clean_line(line[:email_match.start()])
            name_part = name_part.strip("<>").strip()

            if name_part:
                authors.append(f"{name_part} <{email}>")
                pending_name = None
            elif pending_name:
                authors.append(f"{pending_name} <{email}>")
                pending_name = None
            else:
                authors.append(f"<{email}>")
        else:
            cleaned = clean_line(line)
            if cleaned and not skip_line(cleaned):
                if pending_name:
                    authors.append(pending_name)
                pending_name = cleaned

    if pending_name:
        authors.append(pending_name)

    return authors

DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

# Core pattern shapes (no anchors, no label context) reused across modules
# so every document- and section-number pattern has a single source of truth.
# `lib/pdf/types.py` builds the labeled PDF variants (DOC_FIELD_RE,
# SECTION_NUM_RE) on top of these.
DOC_NUM_PATTERN = (
    r"[DPN]\d{3,5}R\d+"
    r"|[DPN]\d{3,5}"
    r"|N\d{3,5}"
    r"|SD-\d+"
)

SECTION_NUM_PATTERN = r"\d+(?:\.\d+)*"

# Broad document-number match used for header stripping and HTML metadata.
# For line-anchored field extraction in PDF blocks, see DOC_FIELD_RE in
# lib/pdf/types.py, which targets "Document Number: PXXXXrN" line prefixes.
DOC_NUM_RE = re.compile(rf"\b({DOC_NUM_PATTERN})\b", re.IGNORECASE)

# Leading section-number prefix used by the HTML renderer to strip a number
# (e.g. "2.1.3 " or "1. ") from heading text.
SECTION_NUM_PREFIX_RE = re.compile(rf"^{SECTION_NUM_PATTERN}\.?\s+")
