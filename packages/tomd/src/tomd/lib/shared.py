"""Shared text utilities and constants for PDF and HTML converters."""

import logging as _logging
import re
import unicodedata
from collections import Counter

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


_CF_RANGES = [
    range(0x0000, 0x10000),
    range(0x1BCA0, 0x1BCA4),
    range(0xE0001, 0xE0002),
    range(0xE0020, 0xE0080),
]

FORMAT_CHARS = frozenset(
    chr(c)
    for r in _CF_RANGES
    for c in r
    if unicodedata.category(chr(c)) == 'Cf'
)


def strip_format_chars(text: str) -> str:
    """Remove Unicode format characters (category Cf)."""
    return "".join(c for c in text if c not in FORMAT_CHARS)


_dedup_log = _logging.getLogger(__name__)


_MAX_PARAGRAPH_OCCURRENCES = 10
_MIN_DEDUP_LENGTH = 40


def dedup_paragraphs(md: str) -> str:
    """Remove duplicate paragraphs from Markdown text.

    Two passes:
    1. Consecutive identical paragraphs are collapsed to one.
    2. Any paragraph longer than 40 chars appearing more than 10 times
       total is capped at 10 occurrences (keeps the first 10).

    Headings and code fences are never dropped.
    """
    blocks = md.split("\n\n")
    if len(blocks) <= 1:
        return md

    deduped: list[str] = [blocks[0]]
    for block in blocks[1:]:
        if block.strip() != deduped[-1].strip():
            deduped.append(block)

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


DEFAULT_FENCE_LANG = "cpp"


def strip_leading_h1(body: str, title: str = "") -> str:
    """Remove a leading H1 from body text if it duplicates the front-matter title.

    Strips the first non-blank line if it is an ATX H1 (starts with '# ') and
    either matches the front-matter title or is the very first content.
    Also handles plaintext title echoes (no '#' prefix) and promotes a bare
    "Abstract" line immediately following to ``## Abstract``.
    """
    lines = body.split("\n")
    title_clean = title.strip().strip('"').strip()
    title_removed_at = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue
        if stripped.startswith("# ") and not stripped.startswith("## "):
            h1_text = stripped[2:].strip()
            if not title_clean or _titles_match(h1_text, title_clean):
                lines[i] = ""
                title_removed_at = i
            break
        # Plaintext title echo (PDF structurer did not add #).
        if title_clean and not stripped.startswith("#") and _titles_match(stripped, title_clean):
            lines[i] = ""
            title_removed_at = i
        break

    # Promote a bare "Abstract" line to ## heading when it appears
    # early in the body (PDF structurer missed the heading markup).
    start = title_removed_at + 1 if title_removed_at >= 0 else 0
    for j in range(start, len(lines)):
        s = lines[j].strip()
        if not s:
            continue
        if s.startswith("<!--") and s.endswith("-->"):
            continue
        if s.lower().rstrip(":") == "abstract" and not s.startswith("#"):
            lines[j] = "## Abstract"
        break

    result = "\n".join(lines)
    result = re.sub(r"^\n{3,}", "\n\n", result)
    return result


def _titles_match(h1: str, title: str) -> bool:
    """Fuzzy match between an H1 heading and a front-matter title."""
    def normalize(s: str) -> str:
        s = re.sub(r"[^\w\s]", "", s.lower())
        return re.sub(r"\s+", " ", s).strip()
    h1_n, title_n = normalize(h1), normalize(title)
    return h1_n == title_n or title_n.startswith(h1_n)


_REDUNDANT_META_RE = re.compile(
    r"^\*{0,2}Document\s+(?:number|No\.?)\*{0,2}\s*:.*$",
    re.IGNORECASE | re.MULTILINE,
)

# Labels that identify a body line as leaked metadata.  Covers the
# standard WG21 header fields plus common variants.
_FREEFORM_META_LABEL_RE = re.compile(
    r"^\*{0,2}(?:Reply[- ]?to|Audience|Target|Date|Project|"
    r"Authors?|Editors?|Co-?authors?|Subgroup|Source|"
    r"Doc\.?\s*(?:No\.?|Number|#)|Document\s*(?:number|No\.?|#)?|"
    r"Previous|Issues?|"
    r"Follow[- ]?up(?:\s+to)?|Ship\s+vehicle|Targeted\s+for)\*{0,2}\s*:",
    re.IGNORECASE,
)

# "Document P2034R6 Authors ..." -- no colon after "Document", but the
# paper ID immediately follows.  Treated as a metadata line.
# Also matches "ISO/IEC JTC1 SC22 WG21 N5040 ..." style header lines.
_FREEFORM_DOC_ID_RE = re.compile(
    r"^(?:Document\s+[DPN]\d{3,5}R?\d*\b|ISO/?IEC\s+JTC)",
    re.IGNORECASE,
)

# Lines that should never be stripped (structural markdown).
_STRUCTURAL_LINE_RE = re.compile(r"^(?:#{1,6}\s|```|[>*\-+]\s|\d+\.\s|\|)")

# Heading whose text (after # prefix) is a metadata label.
_HEADING_META_LABEL_RE = re.compile(
    r"^#{1,6}\s+\*{0,2}(?:Reply[- ]?to|Audience|Target|Date|Project|"
    r"Authors?|Editors?|Co-?authors?|Subgroup|Source|"
    r"Doc\.?\s*(?:No\.?|Number|#)|Document\s*(?:number|No\.?|#)?)\*{0,2}\s*:",
    re.IGNORECASE,
)

# List item whose text is a metadata label ("- **Document number:** ...").
_LIST_META_LABEL_RE = re.compile(
    r"^[-*+]\s+\*{0,2}(?:Reply[- ]?to|Audience|Target|Date|Project|"
    r"Authors?|Editors?|Co-?authors?|Subgroup|Source|"
    r"Doc\.?\s*(?:No\.?|Number|#)|Document\s*(?:number|No\.?|#)?)\*{0,2}\s*:",
    re.IGNORECASE,
)

# Bare date heading: "### March 26, 2026" or "### 2026-03-26".
_HEADING_BARE_DATE_RE = re.compile(
    r"^#{1,6}\s+\*?(?:"
    r"(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{1,2},?\s+\d{4}|"
    r"\d{1,2}\s+(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{4}|"
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2})\*?\s*$",
    re.IGNORECASE,
)

# Maximum non-blank body lines to scan for leaked metadata.
_FREEFORM_SCAN_DEPTH = 15

_strip_meta_log = _logging.getLogger(__name__)


_LONG_CONTENT_THRESHOLD = 120
_CODE_FENCE_RE = re.compile(r"^```")


def _strip_freeform_metadata_lines(md: str,
                                    metadata: dict | None = None) -> str:
    """Strip free-form metadata lines leaked into the body after front matter.

    Scans the first non-blank body lines (up to ``_FREEFORM_SCAN_DEPTH``)
    and removes those that start with a known metadata label, plus any
    continuation lines (indented / comma-prefixed) that follow them.
    Stops at structural markdown (headings, lists, blockquotes, table
    rows), long paragraph text, or code fences.

    When ``metadata`` is provided, also strips metadata-carrying structural
    lines (headings with author names or dates, list items with metadata
    labels) and bare author+email lines matching ``reply-to``.
    """
    # Build author tokens from metadata for content-aware stripping.
    # Metadata can be a dict (from YAML) or a PaperRow dataclass.
    author_tokens: set[str] = set()
    if metadata:
        if isinstance(metadata, dict):
            reply_to = (metadata.get("reply-to")
                        or metadata.get("authors") or [])
        else:
            reply_to = getattr(metadata, "authors", None) or []
        for entry in reply_to:
            name = entry.split("<")[0].strip()
            for token in name.split(","):
                token = token.strip()
                for p in token.split():
                    cleaned = p.strip(".,;:*_()").lower()
                    if len(cleaned) >= 3:
                        author_tokens.add(cleaned)
    fm_end = _find_front_matter_end(md)
    if fm_end is None:
        return md
    body_start = md.find("\n", fm_end)
    if body_start < 0:
        return md
    body_start += 1

    lines = md[body_start:].split("\n")
    to_remove: set[int] = set()
    non_blank_seen = 0
    in_code_fence = False

    for i, line in enumerate(lines):
        if i in to_remove:
            continue
        stripped = line.strip()
        if not stripped:
            continue

        non_blank_seen += 1
        if non_blank_seen > _FREEFORM_SCAN_DEPTH:
            break

        # Never touch content inside code fences.
        if _CODE_FENCE_RE.match(stripped):
            if in_code_fence:
                in_code_fence = False
                continue
            in_code_fence = True
            continue
        if in_code_fence:
            continue

        # Structural markdown (headings, lists, blockquotes, tables).
        # Before breaking, check if the structural line itself carries
        # metadata that should be stripped.
        if _STRUCTURAL_LINE_RE.match(stripped):
            is_meta_structural = False

            # Heading or list item with a metadata label.
            if _HEADING_META_LABEL_RE.match(stripped):
                is_meta_structural = True
            elif _LIST_META_LABEL_RE.match(stripped):
                is_meta_structural = True
            # Bare date heading ("### March 26, 2026").
            elif _HEADING_BARE_DATE_RE.match(stripped):
                is_meta_structural = True
            # Author-name heading matching reply-to ("#### *Christof Meerwald*").
            elif author_tokens and re.match(r"^#{1,6}\s+", stripped):
                heading_text = re.sub(r"^#{1,6}\s+", "", stripped)
                clean_name = re.sub(r"[*_`()\[\]<>]", " ", heading_text)
                clean_name = re.sub(r"\S+@\S+", " ", clean_name)
                clean_name = re.sub(r"https?://\S+", " ", clean_name)
                name_toks = [t.lower() for t in clean_name.split()
                             if len(t) >= 3 and t[0].isalpha()]
                if 1 <= len(name_toks) <= 4:
                    match_ct = sum(1 for t in name_toks if t in author_tokens)
                    if match_ct >= 1 and match_ct / len(name_toks) >= 0.5:
                        is_meta_structural = True

            if is_meta_structural:
                _strip_meta_log.debug(
                    "Stripping metadata structural line %d: %.80s",
                    i, stripped)
                to_remove.add(i)
                continue

            break

        # Long lines are body paragraphs: stop scanning.
        if (len(stripped) > _LONG_CONTENT_THRESHOLD
                and not _FREEFORM_META_LABEL_RE.match(stripped)
                and not _FREEFORM_DOC_ID_RE.match(stripped)):
            break

        if _FREEFORM_META_LABEL_RE.match(stripped) or _FREEFORM_DOC_ID_RE.match(stripped):
            _strip_meta_log.debug(
                "Stripping leaked metadata line %d: %.80s", i, stripped)
            to_remove.add(i)
            # Also remove continuation lines that belong to this entry.
            # Two cases: (a) indented / comma-prefixed continuations,
            # (b) value-on-next-line when the label ends with ":"
            #    e.g. "Date:\nJanuary 22, 2026".
            label_only = stripped.endswith(":")
            for k in range(i + 1, len(lines)):
                cont = lines[k].strip()
                if not cont:
                    if label_only:
                        continue
                    break
                if _FREEFORM_META_LABEL_RE.match(cont):
                    break
                if _STRUCTURAL_LINE_RE.match(cont):
                    break
                if label_only:
                    to_remove.add(k)
                    label_only = False
                    continue
                if not lines[k][0].isspace() and not cont.startswith(","):
                    break
                to_remove.add(k)
            continue

        # Bare author name + email matching reply-to metadata.
        # "Vinnie Falco vinnie.falco@gmail.com" or
        # "Vinnie Falco <vinnie.falco@gmail.com>"
        if author_tokens and ("@" in stripped or "mailto:" in stripped):
            clean_text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", stripped)
            clean_text = re.sub(r"\S+@\S+", " ", clean_text)
            clean_text = re.sub(r"[*_`()\[\]<>]", " ", clean_text)
            name_toks = [t.lower() for t in clean_text.split()
                         if len(t) >= 3 and t[0].isalpha()]
            if name_toks:
                match_ct = sum(1 for t in name_toks if t in author_tokens)
                if match_ct >= 1 and match_ct / len(name_toks) >= 0.5:
                    _strip_meta_log.debug(
                        "Stripping bare author+email line %d: %.80s",
                        i, stripped)
                    to_remove.add(i)
                    continue

        # Short non-metadata line: skip over it (title echo, page number,
        # tomd uncertain marker, etc.).  Do not strip it.

    if not to_remove:
        return md

    new_lines = [ln for j, ln in enumerate(lines) if j not in to_remove]
    result = md[:body_start] + "\n".join(new_lines)
    # Collapse runs of 3+ newlines left by removed lines into double.
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result


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


_ORPHAN_BULLET_RE = re.compile(r"^\s*[-*\u2022\u2023\u25e6\u2043]\s+")
_ORPHAN_HEADING_RE = re.compile(r"^#{2,}\s+(.*)")
_ORPHAN_MIN_BULLETS = 3
_ORPHAN_MATCH_RATIO = 0.5
_SECTION_NUM_STRIP_RE = re.compile(r"^\d+(?:\.\d+)*\.?\s*")
_ORPHAN_TOC_LABELS = frozenset({"table of contents", "contents", "toc"})
_ORPHAN_INLINE_MD_RE = re.compile(r"\*{1,3}|`|(?:\[([^\]]+)\]\([^)]*\))")
_TRIPLE_NEWLINE_RE = re.compile(r"\n{3,}")
_orphan_log = _logging.getLogger(__name__)


def strip_orphan_toc_list(md: str) -> str:
    """Remove bullet-list TOCs orphaned before the first heading.

    Some WG21 PDFs have a minimal Table of Contents as a bullet list of
    section names (no dot-leaders, no page numbers). These survive PDF
    extraction as orphan content between the front matter and the first
    ``## `` heading. This helper strips them when the majority of bullet
    texts match actual headings in the document.
    """
    fm_end_offset = _find_front_matter_end(md)
    if fm_end_offset is None:
        return md

    lines = md.split("\n")
    # Find the line index of the closing --- in front matter.
    char_pos = 0
    fm_end_line = -1
    for i, line in enumerate(lines):
        if char_pos >= fm_end_offset:
            fm_end_line = i
            break
        char_pos += len(line) + 1
    if fm_end_line < 0:
        return md

    preamble_start = fm_end_line + 1

    # Find the first ## heading after front matter.
    first_heading_line = -1
    for i in range(preamble_start, len(lines)):
        if _ORPHAN_HEADING_RE.match(lines[i]):
            first_heading_line = i
            break
    if first_heading_line < 0:
        return md

    # Collect bullet lines in the preamble zone.
    bullet_entries: list[tuple[int, str]] = []
    for i in range(preamble_start, first_heading_line):
        if _ORPHAN_BULLET_RE.match(lines[i]):
            text = _ORPHAN_BULLET_RE.sub("", lines[i]).strip().lower()
            text = _SECTION_NUM_STRIP_RE.sub("", text)
            text = _ORPHAN_INLINE_MD_RE.sub(lambda m: m.group(1) or "", text)
            bullet_entries.append((i, text.strip()))

    if len(bullet_entries) < _ORPHAN_MIN_BULLETS:
        return md

    # Collect all document headings (## and deeper), normalized.
    doc_headings: set[str] = set()
    for line in lines[first_heading_line:]:
        m = _ORPHAN_HEADING_RE.match(line)
        if m:
            h = m.group(1).strip().lower()
            h = _SECTION_NUM_STRIP_RE.sub("", h)
            doc_headings.add(h)

    matches = sum(1 for _, bt in bullet_entries if bt in doc_headings)
    if matches / len(bullet_entries) < _ORPHAN_MATCH_RATIO:
        return md

    _orphan_log.debug(
        "Stripped orphan TOC list: %d bullets, %d/%d matched headings",
        len(bullet_entries), matches, len(bullet_entries),
    )

    # Verify no substantive non-bullet content between first and last bullet.
    bullet_indices = {i for i, _ in bullet_entries}
    strip_start = min(bullet_indices)
    strip_end = max(bullet_indices)
    for i in range(strip_start, strip_end + 1):
        if i not in bullet_indices and lines[i].strip():
            return md

    # Absorb a TOC label line immediately before the first bullet.
    if strip_start > preamble_start:
        label_candidate = lines[strip_start - 1].strip().lower()
        label_candidate = _ORPHAN_INLINE_MD_RE.sub(
            lambda m: m.group(1) or "", label_candidate,
        )
        if label_candidate in _ORPHAN_TOC_LABELS:
            strip_start -= 1

    # Extend to absorb trailing blank lines up to the first heading.
    while strip_end + 1 < first_heading_line and not lines[strip_end + 1].strip():
        strip_end += 1

    strip_set = set(range(strip_start, strip_end + 1))
    cleaned = [lines[i] for i in range(len(lines)) if i not in strip_set]
    # Collapse triple-newlines only in the preamble area, not the whole doc.
    preamble_end_in_cleaned = max(0, first_heading_line - len(strip_set))
    head = "\n".join(cleaned[:preamble_end_in_cleaned])
    tail = "\n".join(cleaned[preamble_end_in_cleaned:])
    head = _TRIPLE_NEWLINE_RE.sub("\n\n", head)
    return head + "\n" + tail if tail else head


def strip_freeform_metadata_lines(md: str,
                                   metadata: dict | None = None) -> str:
    """Public entry point for free-form metadata stripping.

    Called from ``api.convert_paper`` after ``_strip_body_metadata_text``,
    so it runs on the final markdown but does not affect the golden-test
    layer (``run_pipeline`` / ``convert_html``).

    When ``metadata`` is provided, content-aware stripping kicks in:
    structural lines (headings, list items) carrying metadata and bare
    author+email lines are also removed.
    """
    return _strip_freeform_metadata_lines(md, metadata=metadata)


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


_FRONT_MATTER_END_RE = re.compile(r"\A---[ \t]*\n.*?\n(---)", re.DOTALL)


def _find_front_matter_end(md: str) -> int | None:
    """Find the character offset of the closing ``---`` in YAML front matter.

    Returns the offset where the closing ``---`` starts, or None if the
    string does not begin with a valid YAML front matter block.
    """
    m = _FRONT_MATTER_END_RE.match(md)
    return m.start(1) if m else None


def _strip_metadata_table(md: str) -> str:
    """Strip a leading metadata pipe table + HR from the body after front matter.

    Strips if the table's first-cell keywords are all known metadata labels
    (Document, Date, Audience, Reply-to, Replaces, etc.).
    Tables with non-metadata rows are left intact.
    """
    fm_end = _find_front_matter_end(md)
    if fm_end is None:
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

_deobfuscate_log = _logging.getLogger(__name__)

# Three families of anti-spam obfuscation:
# Family 1: word-based -- "user at domain dot com", all 4 bracket variants
# Family 2: underscore-based -- "user_at_domain.com"  (real dots in domain)
# Family 3: AT-only -- "user AT domain.tld" (obfuscated AT, real dots in domain)
#
# Bracket families for AT: (at), [at], {at}, <at>, bare "at"
# Bracket families for DOT: (dot), [dot], {dot}, <dot>, bare "dot"
_AT_ALTERNATION = r"(?:\(at\)|\[at\]|\{at\}|<at>|\bat\b)"
_DOT_ALTERNATION = r"(?:\(dot\)|\[dot\]|\{dot\}|<dot>|\bdot\b)"
_OBFUSCATED_WORD_RE = re.compile(
    r"\b(\w[\w.+-]*)"
    r"\s*" + _AT_ALTERNATION + r"\s*"
    r"(\w[\w-]*"
    r"(?:\s*" + _DOT_ALTERNATION + r"\s*\w[\w-]*)+)"
    r"\b",
    re.IGNORECASE,
)
_OBFUSCATED_AT_ONLY_RE = re.compile(
    r"\b(\w[\w.+-]*)"
    r"\s*" + _AT_ALTERNATION + r"\s*"
    r"([\w][\w-]*(?:\.[\w][\w-]*)+)"
    r"\b",
    re.IGNORECASE,
)
_OBFUSCATED_UNDERSCORE_RE = re.compile(
    r"\b(\w[\w.+-]*)_at_([\w.-]+\.\w+)\b",
    re.IGNORECASE,
)

_DOT_REPLACE_RE = re.compile(
    r"\s*" + _DOT_ALTERNATION + r"\s*",
    re.IGNORECASE,
)


def deobfuscate_email(text: str) -> tuple[str, tuple[int, int]] | None:
    """Reverse common anti-spam email obfuscation patterns.

    Handles three families (checked most-specific-first):
    - Underscore-based: ``user_at_domain.com`` (n5038/n5040 pattern)
    - Word/bracket AT+DOT: ``user at domain dot com``, all bracket variants
    - AT-only: ``user AT domain.tld`` (obfuscated AT, real dots in domain)

    Returns ``(email, (match_start, match_end))`` when the reconstructed
    address passes ``EMAIL_RE`` validation, or ``None``.  The span refers
    to the obfuscated region in *text* so callers can slice the name
    portion without re-matching.  Shared across HTML and PDF pipelines.
    """
    # Family 2: underscore (most specific, least likely to false-positive)
    m = _OBFUSCATED_UNDERSCORE_RE.search(text)
    if m:
        candidate = f"{m.group(1)}@{m.group(2)}"
        if EMAIL_RE.fullmatch(candidate):
            return (candidate, (m.start(), m.end()))

    # Family 1: word/bracket "at" + "dot" (both obfuscated)
    m = _OBFUSCATED_WORD_RE.search(text)
    if m:
        local = m.group(1)
        domain_raw = m.group(2)
        domain = _DOT_REPLACE_RE.sub(".", domain_raw)
        candidate = f"{local}@{domain}"
        if EMAIL_RE.fullmatch(candidate):
            return (candidate, (m.start(), m.end()))

    # Family 3: AT-only (obfuscated AT, real dots in domain)
    m = _OBFUSCATED_AT_ONLY_RE.search(text)
    if m:
        candidate = f"{m.group(1)}@{m.group(2)}"
        if EMAIL_RE.fullmatch(candidate):
            return (candidate, (m.start(), m.end()))

    return None


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
        def skip_line(_):
            return False

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
            deob_result = deobfuscate_email(line)
            if deob_result:
                deob, (match_start, _match_end) = deob_result
                _deobfuscate_log.debug("deobfuscated an author email")
                name_part = clean_line(line[:match_start])
                name_part = name_part.strip(",").strip()
                if name_part:
                    authors.append(f"{name_part} <{deob}>")
                    pending_name = None
                elif pending_name:
                    authors.append(f"{pending_name} <{deob}>")
                    pending_name = None
                else:
                    authors.append(f"<{deob}>")
            else:
                cleaned = clean_line(line)
                if cleaned and not skip_line(cleaned):
                    if pending_name:
                        authors.append(pending_name)
                    pending_name = cleaned

    if pending_name:
        authors.append(pending_name)

    return authors

_enrich_log = _logging.getLogger(__name__ + ".enrich")

# Minimum length for a last-name token to qualify for email matching.
# Short tokens (<=3 chars) cause false positives: "RCU" in "paulmckrcu".
_MIN_LAST_NAME_LEN = 4

# Reject name candidates that contain these: they're metadata labels or
# title fragments, not person names.
_NON_NAME_CHARS = re.compile(r"[:\[\]{}=<>]")


def _looks_like_person_name(text: str) -> bool:
    """Heuristic: reject strings that are obviously not person names."""
    tokens = text.strip().split()
    if len(tokens) < 2:
        return False
    if _NON_NAME_CHARS.search(text):
        return False
    # Person names are short; title/metadata fragments are long.
    if len(text) > 60:
        return False
    return True


def enrich_reply_to_names(
    reply_to: list[str],
    author_names: list[str],
) -> list[str]:
    """Pair bare ``<email>`` reply-to entries with author names.

    For each entry that has an email but no name (e.g. ``<daveed@example.com>``),
    extract the email local-part and domain, then check each *author_names*
    entry for a last-name match against either the local-part or the domain
    (case-insensitive).  If exactly one author matches, pair them.

    ``author_names`` should contain bare name strings (no email) already
    present in ``reply_to`` or elsewhere in the paper metadata.  Names that
    already have an email (contain ``<`` or ``@``) are skipped as candidates.

    Returns a new list; the input is not mutated.
    """
    if not reply_to or not author_names:
        return list(reply_to)

    # Build candidate name list: only bare names that look like person names.
    bare_names = [
        n for n in author_names
        if "<" not in n and "@" not in n
        and n.strip() and _looks_like_person_name(n)
    ]
    if not bare_names:
        return list(reply_to)

    result = []
    for entry in reply_to:
        stripped = entry.strip()
        # Only process bare-email entries: <email> or just email, no name text.
        email_m = EMAIL_RE.search(stripped)
        has_name = (
            email_m
            and stripped[:email_m.start()].strip().strip("<>").strip()
        )
        if not email_m or has_name:
            result.append(entry)
            continue

        email = email_m.group(0)
        local_part = email.split("@")[0].lower()
        domain = email.split("@")[1].lower() if "@" in email else ""
        # Strip TLD from domain for matching (vandevoorde.com -> vandevoorde)
        domain_base = domain.rsplit(".", 1)[0] if "." in domain else domain

        matches = []
        for name in bare_names:
            tokens = name.strip().split()
            if not tokens:
                continue
            last = tokens[-1].lower()
            if len(last) < _MIN_LAST_NAME_LEN:
                continue
            if last in local_part or last in domain_base:
                matches.append(name)

        if len(matches) == 1:
            _enrich_log.info(
                "Paired bare reply-to email with author name"
            )
            result.append(f"{matches[0]} <{email}>")
        else:
            result.append(entry)

    return result


DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

_MONTH_MAP: dict[str, int] = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12,
}

# "Month DD, YYYY" or "Month DD YYYY" (with optional period after abbrev)
_NATURAL_DATE_RE = re.compile(
    r"(?P<month>January|February|March|April|May|June|July|August|"
    r"September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?"
    r"\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4})",
    re.IGNORECASE,
)

# "DD Month YYYY" (European style)
_EURO_DATE_RE = re.compile(
    r"(?P<day>\d{1,2})\s+(?P<month>January|February|March|April|May|June|July|August|"
    r"September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?,?\s+(?P<year>\d{4})",
    re.IGNORECASE,
)

_date_log = _logging.getLogger(__name__ + ".date")


_SLASH_DATE_RE = re.compile(r"\b(\d{4})/(\d{2})/(\d{2})\b")


def normalize_date(text: str) -> str | None:
    """Parse common date formats and return ISO YYYY-MM-DD, or None.

    Tries in order:
      1. ISO literal (YYYY-MM-DD) via DATE_RE
      2. Slash-separated YYYY/MM/DD (common in WG21 papers)
      3. Natural "Month DD, YYYY" (e.g. "February 22, 2026")
      4. European "DD Month YYYY" (e.g. "22 February 2026")

    Returns None when no recognized date format is found.
    """
    if not text:
        return None
    m = DATE_RE.search(text)
    if m:
        return m.group(1)
    m = _SLASH_DATE_RE.search(text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = _NATURAL_DATE_RE.search(text)
    if not m:
        m = _EURO_DATE_RE.search(text)
    if m:
        month_num = _MONTH_MAP.get(m.group("month").lower().rstrip("."))
        if month_num is None:
            return None
        day = int(m.group("day"))
        year = int(m.group("year"))
        if not (1 <= day <= 31 and 1900 <= year <= 2100):
            return None
        _date_log.debug(
            "Converted natural date %r -> %04d-%02d-%02d",
            m.group(0), year, month_num, day,
        )
        return f"{year:04d}-{month_num:02d}-{day:02d}"
    return None

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

_ROMAN = r"[IVXLCDM]+"
SECTION_NUM_PATTERN = rf"(?:\d+(?:\.\d+)*|{_ROMAN})"

# Broad document-number match used for header stripping and HTML metadata.
# For line-anchored field extraction in PDF blocks, see DOC_FIELD_RE in
# lib/pdf/types.py, which targets "Document Number: PXXXXrN" line prefixes.
DOC_NUM_RE = re.compile(rf"\b({DOC_NUM_PATTERN})\b", re.IGNORECASE)

# Leading section-number prefix used by the HTML renderer to strip a number
# (e.g. "2.1.3 " or "1. ") from heading text.
SECTION_NUM_PREFIX_RE = re.compile(rf"^{SECTION_NUM_PATTERN}\.?\s+")
