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


FRONT_MATTER_ORDER = ("title", "document", "date", "audience", "reply-to")

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
    if any(c in val for c in ':{}[]#&*?|>!%@`"\'\n\\'):
        return f'{key}: "{_yaml_escape(val)}"'
    return f"{key}: {val}"


def format_front_matter(metadata: dict) -> str:
    """Format metadata dict as YAML front matter.

    Field order: title, document, date, audience, reply-to.
    Title and values containing YAML-special characters are double-quoted
    with backslash-escaping for embedded quotes, backslashes, and newlines.
    Reply-to is a YAML list of double-quoted strings.
    Returns empty string if metadata is empty.
    """
    if not metadata:
        return ""
    lines = ["---"]
    for key in FRONT_MATTER_ORDER:
        if key in metadata:
            lines.append(_yaml_value(key, metadata[key]))
    for key, val in metadata.items():
        if key not in FRONT_MATTER_ORDER:
            lines.append(_yaml_value(key, val))
    lines.append("---")
    return "\n".join(lines)


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
