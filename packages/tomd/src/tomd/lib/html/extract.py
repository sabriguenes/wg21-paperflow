"""HTML parsing, generator detection, metadata extraction, and boilerplate stripping."""

import logging
import re

from bs4 import BeautifulSoup, Tag

from .. import DATE_RE, DOC_NUM_RE, parse_author_lines

_log = logging.getLogger(__name__)

_HACKMD_RE = re.compile(r"hackmd")
_COMMA_COLLAPSE_RE = re.compile(r"(,\s*){2,}")


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

    HTML DOM scan (pathway 3 of 3). Dispatches to a generator-specific
    extractor based on the detected generator family.

    Returns a dict with possible keys: title, document, date,
    audience, reply-to.
    """
    if generator == "mpark":
        return _extract_mpark_metadata(soup)
    if generator == "bikeshed":
        return _extract_bikeshed_metadata(soup)
    if generator == "hand-written":
        return _extract_handwritten_metadata(soup)
    if generator == "wg21":
        return _extract_wg21_metadata(soup)
    if generator == "schultke":
        return _extract_schultke_metadata(soup)
    return _extract_generic_metadata(soup)


def _extract_mpark_metadata(soup: BeautifulSoup) -> dict:
    """mpark/wg21: metadata in table inside <header id="title-block-header">."""
    metadata: dict = {}
    header = soup.find("header", id="title-block-header")
    if not header:
        return metadata

    title_tag = header.find("h1", class_="title")
    if title_tag:
        metadata["title"] = title_tag.get_text(strip=True)

    table = header.find("table")
    if not table:
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
    if dl:
        current_label = None
        for child in dl.children:
            if not isinstance(child, Tag):
                continue
            if child.name == "dt":
                current_label = _normalize_label(child.get_text(strip=True))
            elif child.name == "dd" and current_label:
                text = child.get_text(strip=True)
                if "audience" in current_label:
                    metadata["audience"] = _get_text_br_separated(child)
                elif "editor" in current_label or "author" in current_label:
                    email_link = child.find("a", class_="email")
                    if email_link:
                        name = email_link.get_text(strip=True)
                        href = email_link.get("href", "")
                        email = href.replace("mailto:", "")
                        authors = metadata.get("reply-to", [])
                        authors.append(f"{name} <{email}>")
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

        for a in addr.find_all("a"):
            href = a.get("href", "")
            if "mailto:" in href:
                email = href.replace("mailto:", "")
                name = a.get_text(strip=True)
                authors = metadata.get("reply-to", [])
                authors.append(f"{name} <{email}>")
                metadata["reply-to"] = authors

    h1 = soup.find("h1")
    if h1 and "title" not in metadata:
        metadata["title"] = h1.get_text(strip=True)

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
                elif "reply" in label:
                    a_tag = td.find("a")
                    if a_tag:
                        href = a_tag.get("href", "")
                        email = href.replace("mailto:", "")
                        name = a_tag.get_text(strip=True)
                        authors = metadata.get("reply-to", [])
                        authors.append(f"{name} <{email}>")
                        metadata["reply-to"] = authors

    return metadata


# Canonical field names mapped to the exact strings produced by _normalize_label().
# A <dt> or table header matches a field when its normalized text is in the synonym set.
_FIELD_SYNONYMS: dict[str, frozenset[str]] = {
    "document": frozenset({
        "document number", "document no", "doc no", "doc. no.", "doc", "number",
    }),
    "date":     frozenset({"date", "revision date"}),
    "audience": frozenset({"audience", "subgroup"}),
    "reply-to": frozenset({"reply to", "reply-to", "author", "authors", "editor", "editors"}),
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
        metadata["title"] = h1.get_text(strip=True)
    dl = container.find("dl")
    if not dl:
        return metadata
    for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
        field = _match_field(dt.get_text(strip=True))
        if field is None:
            continue
        value = dd.get_text(strip=True)
        if field == "document":
            m = DOC_NUM_RE.search(value)
            if m:
                metadata["document"] = m.group(0).upper()
        elif field == "date":
            m = DATE_RE.search(value)
            if m:
                metadata["date"] = m.group(0)
        elif field == "audience":
            metadata["audience"] = _get_text_br_separated(dd)
        elif field == "reply-to":
            authors = parse_author_lines([value])
            if authors:
                metadata["reply-to"] = authors
        else:
            metadata[field] = value
    return metadata


def _extract_schultke_metadata(soup: BeautifulSoup) -> dict:
    """Jan Schultke's custom HTML generator: metadata from <h1> and tables."""
    return _extract_generic_metadata(soup)


def _extract_generic_metadata(soup: BeautifulSoup) -> dict:
    """Fallback: try common patterns."""
    metadata: dict = {}

    h1 = soup.find("h1")
    if h1:
        metadata["title"] = h1.get_text(strip=True)

    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) >= 2:
                label = _normalize_label(cells[0].get_text(strip=True))
                value = cells[-1].get_text(strip=True)
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

    return metadata


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
