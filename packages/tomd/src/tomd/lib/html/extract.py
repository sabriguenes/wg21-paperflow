"""HTML parsing, generator detection, metadata extraction, and boilerplate stripping."""

import logging
import re

from bs4 import BeautifulSoup, Tag

from .. import DATE_RE, DOC_NUM_RE, EMAIL_RE, parse_author_lines

_log = logging.getLogger(__name__)

_HACKMD_RE = re.compile(r"hackmd")
_COMMA_COLLAPSE_RE = re.compile(r"(,\s*){2,}")
_MAILTO_PREFIX_RE = re.compile(r"^mailto:/{0,2}")


def _extract_mailto_email(href: str) -> str:
    """Normalize both ``mailto:`` and the invalid ``mailto://`` to a bare address."""
    return _MAILTO_PREFIX_RE.sub("", href)


def _extract_mailto_authors(container: Tag) -> list[str]:
    """Extract ``Name <email>`` entries from mailto links in *container*."""
    authors: list[str] = []
    for a in container.find_all("a", href=lambda h: h and "mailto:" in h):
        email = _extract_mailto_email(a.get("href", ""))
        name = a.get_text(strip=True)
        if not email:
            continue
        entry = f"{name} <{email}>" if name and name != email else f"<{email}>"
        if entry not in authors:
            authors.append(entry)
    return authors


def parse_html(text: str) -> BeautifulSoup:
    """Parse HTML with the built-in parser (forgiving of malformed HTML)."""
    return BeautifulSoup(text, "html.parser")


def detect_generator(soup: BeautifulSoup) -> str:
    """Identify which tool generated this HTML paper.

    Returns one of: "mpark", "bikeshed", "hackmd", "wg21", "schultke",
    "dascandy/fiets", "hand-written", "unknown".
    Checks meta generator tag first, then structural heuristics.
    """
    for meta in soup.find_all("meta"):
        name = (meta.get("name") or "").lower()
        content = meta.get("content") or ""
        if name == "generator":
            if "mpark/wg21" in content:
                return "mpark"
            if "bikeshed" in content.lower():
                return "bikeshed"
            if "dascandy/fiets" in content.lower():
                return "dascandy/fiets"
    if soup.find("link", href=_HACKMD_RE):
        return "hackmd"
    title_tag = soup.find("title")
    if title_tag and "hackmd" in title_tag.get_text().lower():
        return "hackmd"
    header = soup.find("header", id="title-block-header")
    if header:
        return "mpark"
    addr = soup.find("address")
    if addr:
        return "hand-written"
    if soup.find("div", class_="wg21-head"):
        return "wg21"
    if soup.find("code-block"):
        return "schultke"
    return "unknown"


def extract_metadata(soup: BeautifulSoup, generator: str) -> dict:
    """Extract WG21 metadata fields from the HTML.

    Two-phase architecture:
      Phase 1 -- generator-specific extractor (structural, high confidence).
      Phase 2 -- ``_enrich_reply_to`` post-pass that fills bare-name entries
                 with emails found anywhere in the metadata region.

    Returns a dict with possible keys: title, document, date,
    audience, reply-to.
    """
    if generator == "mpark":
        metadata = _extract_mpark_metadata(soup)
    elif generator == "bikeshed":
        metadata = _extract_bikeshed_metadata(soup)
    elif generator == "hand-written":
        metadata = _extract_handwritten_metadata(soup)
    elif generator == "wg21":
        metadata = _extract_wg21_metadata(soup)
    elif generator == "schultke":
        metadata = _extract_schultke_metadata(soup)
    else:
        metadata = _extract_generic_metadata(soup)

    _enrich_reply_to(soup, metadata)
    return metadata


def _extract_mpark_metadata(soup: BeautifulSoup) -> dict:
    """mpark/wg21: metadata in table inside <header id="title-block-header">."""
    metadata: dict = {}
    header = soup.find("header", id="title-block-header")
    if not header:
        return metadata

    title_tag = header.find("h1", class_="title")
    if title_tag:
        metadata["title"] = title_tag.get_text(" ", strip=True)

    table = header.find("table")
    if not table:
        # Pandoc papers: header has only <h1>, mailto links may be in
        # the header itself or the next sibling element.  The enrichment
        # post-pass (_enrich_reply_to) handles name-email correlation.
        if "reply-to" not in metadata:
            authors = _extract_mailto_authors(header)
            if not authors:
                sib = header.find_next_sibling()
                if sib:
                    authors = _extract_mailto_authors(sib)
            if authors:
                metadata["reply-to"] = authors
        return metadata

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        label = _normalize_label(cells[0].get_text(strip=True))
        value_cell = cells[1]

        if "document" in label:
            text = value_cell.get_text(strip=True)
            m = DOC_NUM_RE.search(text)
            if m:
                metadata["document"] = m.group(0).upper()

        elif label == "date":
            text = value_cell.get_text(strip=True)
            m = DATE_RE.search(text)
            if m:
                metadata["date"] = m.group(0)

        elif label == "audience":
            metadata["audience"] = _get_text_br_separated(value_cell)

        elif "reply" in label:
            authors = _parse_mpark_authors(value_cell)
            if authors:
                metadata["reply-to"] = authors

    return metadata


def _normalize_label(text: str) -> str:
    """Normalize a metadata label for keyword matching.

    Strips surrounding whitespace, drops a trailing colon, and lowercases.
    """
    return text.strip().rstrip(":").lower()


def _get_text_br_separated(cell: Tag) -> str:
    """Extract text from a cell, converting <br> tags to comma separators."""
    for br in cell.find_all("br"):
        br.replace_with(", ")
    text = cell.get_text(strip=True)
    text = _COMMA_COLLAPSE_RE.sub(", ", text)
    return text.strip(", ")


_ANGLE_BRACKET_RE = re.compile(r"[<>]")


_BR_SENTINEL = "\x00"


def _parse_mpark_authors(cell: Tag) -> list[str]:
    """Parse 'Name<br>&lt;email&gt;' author entries from a table cell."""
    for br in cell.find_all("br"):
        br.replace_with(_BR_SENTINEL)
    text = cell.get_text()
    text = text.replace("\n", " ").replace("\r", " ")
    lines = text.split(_BR_SENTINEL)

    def _clean_author(text):
        return _ANGLE_BRACKET_RE.sub("", text).strip()

    return parse_author_lines(
        lines,
        clean_line=_clean_author,
        skip_line=lambda l: bool(DOC_NUM_RE.match(l)),
    )


def _extract_bikeshed_metadata(soup: BeautifulSoup) -> dict:
    """Bikeshed: metadata in <dl> inside <div data-fill-with="spec-metadata">."""
    metadata: dict = {}

    h1 = soup.find("h1", class_="p-name")
    if h1:
        text = h1.get_text(" ", strip=True)
        m = DOC_NUM_RE.match(text)
        if m:
            doc = m.group(0).upper()
            title = text[m.end():].strip()
            metadata["document"] = doc
            if title:
                metadata["title"] = title
        else:
            metadata["title"] = text

    time_tag = soup.find("time", class_="dt-updated")
    if time_tag:
        dt = time_tag.get("datetime") or time_tag.get_text(strip=True)
        m = DATE_RE.search(dt)
        if m:
            metadata["date"] = m.group(0)

    spec_meta_div = soup.find("div", {"data-fill-with": "spec-metadata"})
    dl = (spec_meta_div or soup).find("dl")
    editor_dds: list[Tag] = []
    if dl:
        current_label = None
        for child in dl.children:
            if not isinstance(child, Tag):
                continue
            if child.name == "dt":
                current_label = _normalize_label(child.get_text(strip=True))
            elif child.name == "dd" and current_label:
                if "audience" in current_label:
                    metadata["audience"] = _get_text_br_separated(child)
                elif "editor" in current_label or "author" in current_label:
                    editor_dds.append(child)

    # Also collect <dd> elements with explicit editor/author CSS class
    if dl:
        for dd in dl.find_all("dd", class_=lambda c: c and
                              ("editor" in c or "author" in c)):
            if dd not in editor_dds:
                editor_dds.append(dd)

    if "reply-to" not in metadata and editor_dds:
        authors: list[str] = []
        for dd in editor_dds:
            email_link = dd.find(
                "a", class_=lambda c: c and ("email" in c or "u-email" in c),
            )
            if not email_link:
                email_link = dd.find(
                    "a", href=lambda h: h and "mailto:" in h,
                )
            if email_link:
                href = email_link.get("href", "")
                email = _extract_mailto_email(href)
                name_el = (
                    dd.find("a", class_="p-name")
                    or dd.find("span", class_="p-name")
                )
                raw_name = (name_el or email_link).get_text(strip=True)
                name = raw_name.split(" - ")[0].strip()
                if email:
                    authors.append(f"{name} <{email}>")
                continue
            name_span = dd.find("span", class_="p-name")
            if name_span:
                text = name_span.get_text(strip=True)
                m = EMAIL_RE.search(text)
                if m:
                    email = m.group(0)
                    name = text[:m.start()].strip().rstrip("<").strip()
                    authors.append(f"{name} <{email}>")
        if authors:
            metadata["reply-to"] = authors

    return metadata


def _extract_handwritten_metadata(soup: BeautifulSoup) -> dict:
    """Hand-written: metadata in <address> or table.header."""
    metadata: dict = {}

    addr = soup.find("address")
    if addr:
        text = addr.get_text(separator="\n")
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            if "document" in line.lower() and "number" in line.lower():
                m = DOC_NUM_RE.search(line)
                if m:
                    metadata["document"] = m.group(0).upper()
            elif line.lower().startswith("audience"):
                metadata["audience"] = line.split(":", 1)[-1].strip()
            elif (m := DATE_RE.search(line)):
                metadata["date"] = m.group(0)

        addr_authors = _extract_mailto_authors(addr)
        if addr_authors:
            metadata["reply-to"] = addr_authors

    h1 = soup.find("h1")
    if h1 and "title" not in metadata:
        metadata["title"] = h1.get_text(" ", strip=True)

    table = soup.find("table", class_="header")
    if table:
        for row in table.find_all("tr"):
            th = row.find("th")
            td = row.find("td")
            if th and td:
                label = _normalize_label(th.get_text(strip=True))
                value = td.get_text(strip=True)
                if "document" in label:
                    m = DOC_NUM_RE.search(value)
                    if m:
                        metadata["document"] = m.group(0).upper()
                elif "date" in label:
                    m = DATE_RE.search(value)
                    if m:
                        metadata["date"] = m.group(0)
                elif "audience" in label:
                    metadata["audience"] = _get_text_br_separated(td)
                elif "reply" in label or "author" in label or "editor" in label:
                    authors = _extract_mailto_authors(td)
                    if authors:
                        existing = metadata.get("reply-to", [])
                        for a_entry in authors:
                            if a_entry not in existing:
                                existing.append(a_entry)
                        metadata["reply-to"] = existing

    return metadata


# Canonical field names mapped to the exact strings produced by _normalize_label().
# A <dt> or table header matches a field when its normalized text is in the synonym set.
#
# Design decision (April 2026, Slack #cppa-wg21): all author-like labels
# ("Author", "Authors", "Editor", "Co-Authors", "Reply-to") map to the
# single "reply-to" frontmatter field.  Mungo Gill noted that although
# "authors" is more descriptive, consistency with the papers CPPA
# generates (which use "reply-to") is more important.  Vinnie confirmed
# the goal is to extract name/email pairs into this one field.
_FIELD_SYNONYMS: dict[str, frozenset[str]] = {
    "document": frozenset({
        "document number", "document no", "doc no", "doc. no.", "doc", "number",
    }),
    "date":     frozenset({"date", "revision date"}),
    "audience": frozenset({"audience", "subgroup"}),
    "reply-to": frozenset({
        "reply to", "reply-to", "author", "authors", "editor", "editors",
        "co-author", "co-authors",
    }),
}


def _match_field(label: str) -> str | None:
    """Map a metadata label to its canonical field name, or None if unrecognized."""
    norm = _normalize_label(label)
    for field, synonyms in _FIELD_SYNONYMS.items():
        if norm in synonyms:
            return field
    return None


def _extract_wg21_metadata(soup: BeautifulSoup) -> dict:
    """Extract metadata from papers using the wg21 cow-tool generator.

    Reads the title from the first <h1> inside <div class="wg21-head"> and
    the remaining fields from the accompanying <dl> definition list.
    """
    container = soup.find("div", class_="wg21-head")
    if not container:
        return {}
    metadata: dict = {}
    h1 = container.find("h1")
    if h1:
        metadata["title"] = h1.get_text(" ", strip=True)
    dl = container.find("dl")
    if not dl:
        return metadata
    current_field: str | None = None
    for child in dl.children:
        if not isinstance(child, Tag):
            continue
        if child.name == "dt":
            current_field = _match_field(child.get_text(strip=True))
        elif child.name == "dd" and current_field:
            value = child.get_text(strip=True)
            if current_field == "document":
                m = DOC_NUM_RE.search(value)
                if m:
                    metadata["document"] = m.group(0).upper()
            elif current_field == "date":
                m = DATE_RE.search(value)
                if m:
                    metadata["date"] = m.group(0)
            elif current_field == "audience":
                metadata["audience"] = _get_text_br_separated(child)
            elif current_field == "reply-to":
                authors = _extract_mailto_authors(child)
                if not authors:
                    authors = parse_author_lines([value])
                existing = metadata.get("reply-to", [])
                for entry in authors:
                    if entry not in existing:
                        existing.append(entry)
                if existing:
                    metadata["reply-to"] = existing
    return metadata


def _extract_schultke_metadata(soup: BeautifulSoup) -> dict:
    """Jan Schultke's custom HTML generator: metadata from <dl> and tables.

    Schultke papers use a ``<dl>`` definition list for metadata, with
    labels like ``Reply-to:``, ``Co-Authors:``, ``Co-authors:``.
    Multiple ``<dd>`` elements may follow a single ``<dt>`` (continuation),
    and a single ``<dd>`` may contain multiple authors separated by ``<br/>``.
    Falls back to the generic table extractor for remaining fields.
    """
    metadata = _extract_generic_metadata(soup)

    for dl in soup.find_all("dl"):
        current_label = None
        for child in dl.children:
            if not isinstance(child, Tag):
                continue
            if child.name == "dt":
                current_label = _normalize_label(child.get_text(strip=True))
            elif child.name == "dd" and current_label:
                if "reply" in current_label or "author" in current_label or "co-author" in current_label:
                    authors = _extract_mailto_authors(child)
                    if authors:
                        existing = metadata.get("reply-to", [])
                        for entry in authors:
                            if entry not in existing:
                                existing.append(entry)
                        metadata["reply-to"] = existing
                    else:
                        text = child.get_text(strip=True)
                        if text:
                            parsed = parse_author_lines([text])
                            existing = metadata.get("reply-to", [])
                            for entry in parsed:
                                if entry not in existing:
                                    existing.append(entry)
                            metadata["reply-to"] = existing
                elif "document" in current_label:
                    text = child.get_text(strip=True)
                    m = DOC_NUM_RE.search(text)
                    if m:
                        metadata["document"] = m.group(0).upper()
                elif "date" in current_label:
                    text = child.get_text(strip=True)
                    m = DATE_RE.search(text)
                    if m:
                        metadata["date"] = m.group(0)
                elif "audience" in current_label:
                    metadata["audience"] = _get_text_br_separated(child)

    return metadata


def _extract_generic_metadata(soup: BeautifulSoup) -> dict:
    """Fallback: try common patterns.

    Collects reply-to and author/editor entries into separate buckets so
    that a later "Authors:" row cannot overwrite an earlier "Reply-to:" row.
    The buckets are merged at the end: mailto entries win, then reply-to
    entries, then author entries.
    """
    metadata: dict = {}
    reply_entries: list[str] = []
    author_entries: list[str] = []

    h1 = soup.find("h1")
    if h1:
        metadata["title"] = h1.get_text(" ", strip=True)

    for table in soup.find_all("table"):
        current_field: str | None = None
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) >= 2:
                label = _normalize_label(cells[0].get_text(strip=True))
                value = cells[-1].get_text(strip=True)

                is_reply = "reply" in label or "author" in label or "editor" in label

                if label:
                    current_field = "reply" if is_reply else label
                elif not label and current_field != "reply":
                    continue

                if "document" in label or "doc" in label:
                    m = DOC_NUM_RE.search(value)
                    if m:
                        metadata["document"] = m.group(0).upper()
                elif "date" in label:
                    m = DATE_RE.search(value)
                    if m:
                        metadata["date"] = m.group(0)
                elif "audience" in label:
                    metadata["audience"] = _get_text_br_separated(cells[-1])
                elif is_reply or (not label and current_field == "reply"):
                    mailto = _extract_mailto_authors(cells[-1])
                    bucket = reply_entries if ("reply" in label or not label) else author_entries
                    if mailto:
                        for entry in mailto:
                            if entry not in bucket:
                                bucket.append(entry)
                    elif value:
                        parsed = parse_author_lines([value])
                        for entry in parsed:
                            if entry not in bucket:
                                bucket.append(entry)

    # Merge: prefer reply_entries (from "Reply-to:"), fall back to
    # author_entries (from "Authors:"/"Editors:"). Both are kept so the
    # enrichment pass can correlate bare names with unassigned emails.
    merged = reply_entries or author_entries
    if reply_entries and author_entries:
        for entry in author_entries:
            if entry not in merged:
                merged.append(entry)
    if merged:
        metadata["reply-to"] = merged

    return metadata


def _collect_metadata_emails(soup: BeautifulSoup) -> list[str]:
    """Gather all email addresses from the metadata region (before first <h2>).

    Returns deduplicated list of bare email strings.  Sources checked
    in confidence order: mailto links, then plain-text EMAIL_RE matches
    in tables, lists, paragraphs, and definition lists.
    """
    first_h2 = soup.find("h2")
    emails: list[str] = []
    seen: set[str] = set()

    def _add(email: str) -> None:
        lower = email.lower()
        if lower not in seen:
            seen.add(lower)
            emails.append(email)

    # Pass 1: mailto links (highest confidence)
    for a in soup.find_all("a", href=lambda h: h and "mailto:" in h):
        if first_h2 and a.find_previous("h2"):
            continue
        email = _extract_mailto_email(a.get("href", ""))
        if email:
            _add(email)

    # Pass 2: plain-text emails in metadata containers
    for tag in soup.find_all(["td", "dd", "li", "p", "span"]):
        if first_h2 and tag.find_previous("h2"):
            continue
        for m in EMAIL_RE.finditer(tag.get_text()):
            _add(m.group(0))

    return emails


def _recover_name_from_context(soup: BeautifulSoup, email: str) -> str:
    """Find a human name adjacent to *email* in the metadata region.

    Pandoc emits ``Name <a href="mailto:x">x</a>`` where the link text
    equals the address.  The name sits in the parent element's text just
    before the email.
    """
    first_h2 = soup.find("h2")
    for a in soup.find_all("a", href=lambda h: h and "mailto:" in h):
        if first_h2 and a.find_previous("h2"):
            continue
        href_email = _extract_mailto_email(a.get("href", ""))
        if href_email.lower() != email.lower():
            continue
        parent = a.parent
        if not parent:
            continue
        full = parent.get_text(separator="\n", strip=True)
        idx = full.find(email)
        if idx <= 0:
            continue
        before = full[:idx].strip().rstrip("<").strip()
        last_line = before.rsplit("\n", 1)[-1].strip()
        last_line = last_line.split(":")[-1].strip()
        if last_line and not EMAIL_RE.fullmatch(last_line):
            return last_line
    return ""


def _enrich_reply_to(soup: BeautifulSoup, metadata: dict) -> None:
    """Post-pass: fill in missing emails and names for reply-to entries.

    Three enrichments, all additive-only (never remove existing data):

    0. **Internal merge**: pair bare names and bare emails already
       co-existing in the same reply-to list (e.g. from separate
       table rows). Only when counts match 1:1.
    1. **External emails**: scan the metadata region (before first
       ``<h2>``) for emails not yet present and merge with remaining
       bare names, or append as ``<email>``.
    2. **Context names**: for remaining ``<email>``-only entries,
       recover the adjacent human name from HTML parent text
       (Pandoc pattern).
    """
    rt = metadata.get("reply-to", [])
    if not rt:
        # Bootstrap: no reply-to found by the generator-specific extractor.
        # Scan the metadata region for mailto links and seed reply-to.
        all_emails = _collect_metadata_emails(soup)
        if all_emails:
            seeded: list[str] = []
            for email in all_emails:
                name = _recover_name_from_context(soup, email)
                seeded.append(f"{name} <{email}>" if name else f"<{email}>")
            metadata["reply-to"] = seeded
        return

    # --- Enrichment 0: merge bare names with bare emails in-list ---
    bare_names = [e for e in rt if "<" not in e and "@" not in e]
    bare_emails = [e for e in rt
                   if e.startswith("<") and e.endswith(">") and "@" in e]
    if bare_names and bare_emails and len(bare_names) == len(bare_emails):
        merged_entries: list[str] = []
        name_iter = iter(bare_names)
        email_iter = iter(bare_emails)
        used_names: set[str] = set()
        used_emails: set[str] = set()
        for name, email_entry in zip(bare_names, bare_emails):
            email = email_entry[1:-1]
            merged_entries.append(f"{name} <{email}>")
            used_names.add(name)
            used_emails.add(email_entry)
        result = []
        for entry in rt:
            if entry in used_names or entry in used_emails:
                continue
            result.append(entry)
        metadata["reply-to"] = merged_entries + result
        rt = metadata["reply-to"]
        bare_names = [e for e in rt if "<" not in e and "@" not in e]

    # --- Enrichment 1: fill emails for remaining bare names ---
    if bare_names:
        assigned = set()
        for entry in rt:
            m = EMAIL_RE.search(entry)
            if m:
                assigned.add(m.group(0).lower())

        region_emails = _collect_metadata_emails(soup)
        unassigned = [e for e in region_emails if e.lower() not in assigned]

        if unassigned:
            if len(bare_names) == len(unassigned):
                for name, email in zip(bare_names, unassigned):
                    merged = f"{name} <{email}>"
                    metadata["reply-to"] = [
                        merged if e == name else e for e in metadata["reply-to"]
                    ]
            else:
                for email in unassigned:
                    entry = f"<{email}>"
                    if entry not in metadata["reply-to"]:
                        metadata["reply-to"].append(entry)

    # --- Enrichment 2: recover names for bare-email entries ---
    updated = []
    for entry in metadata["reply-to"]:
        if entry.startswith("<") and entry.endswith(">") and "@" in entry:
            email = entry[1:-1]
            name = _recover_name_from_context(soup, email)
            updated.append(f"{name} <{email}>" if name else entry)
        else:
            updated.append(entry)
    metadata["reply-to"] = updated


def strip_boilerplate(soup: BeautifulSoup, generator: str) -> list[str]:
    """Remove non-content elements from `soup` in-place.

    Returns list of problem descriptions.
    """
    problems = []

    for tag in soup.find_all(["style", "script", "link"]):
        tag.decompose()

    for tag in soup.find_all("meta"):
        tag.decompose()

    toc = soup.find(id="TOC") or soup.find(id="toc")
    if toc:
        toc.decompose()

    toc_nav = soup.find("nav", {"data-fill-with": "table-of-contents"})
    if toc_nav:
        toc_nav.decompose()

    header = soup.find("header", id="title-block-header")
    if header:
        header.decompose()

    if generator == "bikeshed":
        for div in soup.find_all("div", {"data-fill-with": True}):
            div.decompose()
        for h1 in soup.find_all("h1", class_="p-name"):
            h1.decompose()
        for h2 in soup.find_all("h2", id="profile-and-date"):
            h2.decompose()

    if generator == "hand-written":
        for addr in soup.find_all("address"):
            addr.decompose()
        for table in soup.find_all("table", class_="header"):
            table.decompose()

    if generator == "wg21":
        for el in soup.find_all("div", class_="wg21-head"):
            el.decompose()
        for el in soup.find_all("div", class_="toc"):
            el.decompose()

    if generator == "unknown":
        problems.append(
            "Unrecognized HTML generator. Metadata extraction may be incomplete. "
            "Content was extracted as-is but may include boilerplate."
        )

    return problems
