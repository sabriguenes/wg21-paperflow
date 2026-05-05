#
# Copyright (c) 2026 Sergio DuBois (sentientsergio@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""WG21 mailing page scraper: fetches paper lists from open-std.org."""

from __future__ import annotations

import logging
import re
import urllib.parse

import httpx
from bs4 import BeautifulSoup
from bs4.element import Tag

logger = logging.getLogger(__name__)

BASE_URL = "https://www.open-std.org/jtc1/sc22/wg21/docs/papers"
DEFAULT_USER_AGENT = "paperflow/0.1 (+https://github.com/cppalliance/wg21-paperflow)"

_MAILING_ANCHOR_RE = re.compile(r"^mailing\d{4}-\d{2}$")
_PAPER_LINK_PATTERN = re.compile(
    r"((?:p\d+r\d+|n\d+|sd-\d+))\.([a-z]+)", re.IGNORECASE
)


def _infer_intent(title: str) -> str | None:
    """Derive intent from the paper title prefix, if present.

    The WG21 document editor enforces two title conventions:
    - ``"Info: ..."`` — informational paper → ``"info"``
    - ``"Ask: ..."`` — paper requesting a poll/vote → ``"ask"``

    Returns ``None`` when neither prefix is present; callers omit the
    ``intent`` key from the row in that case.
    """
    title_s = title.strip()
    if title_s.startswith("Info:"):
        return "info"
    if title_s.startswith("Ask:"):
        return "ask"
    return None


def _extract_paper_metadata_from_row(
    cells: list[Tag],
    page_url: str,
) -> dict | None:
    """Extract paper metadata from a WG21 mailing table row.

    Handles both 8-column (current year) and 5-column (older) layouts.

    The returned dict carries (a) parsed convenience fields and (b)
    ``raw_columns`` / ``raw_links``: every cell text and first-cell
    anchor verbatim so downstream consumers can read columns the scraper
    does not interpret (e.g. previous-version links, disposition columns).
    """
    if not cells:
        return None

    first_cell = cells[0]
    base = urllib.parse.urlparse(BASE_URL)

    raw_columns = [cell.text.strip() for cell in cells]

    title = cells[1].text.strip() if len(cells) > 1 else ""

    authors: list[str] = []
    if len(cells) > 2:
        authors_raw = cells[2].text.strip()
        if authors_raw:
            authors = [
                a.strip() for a in re.split(r",| and ", authors_raw) if a.strip()
            ]

    document_date = None
    if len(cells) > 3:
        date_str = cells[3].text.strip()
        if date_str:
            document_date = date_str

    subgroup = ""
    if len(cells) >= 8:
        subgroup = cells[6].text.strip()
    elif len(cells) > 4:
        subgroup = cells[4].text.strip()

    raw_links: list[dict] = []
    for link in first_cell.find_all("a", href=True):
        href = link.get("href", "")
        absolute = urllib.parse.urljoin(page_url, href)
        raw_links.append({
            "href": absolute,
            "text": link.text.strip(),
        })

    for link in first_cell.find_all("a", href=True):
        href = link.get("href", "")
        match = _PAPER_LINK_PATTERN.search(href)
        if not match:
            continue

        paper_url = urllib.parse.urljoin(page_url, href)
        parsed = urllib.parse.urlparse(paper_url)
        if parsed.scheme not in ("https", "http") or parsed.netloc != base.netloc:
            logger.warning("Skipping off-origin paper URL %s", paper_url)
            continue

        paper_id = match.group(1).lower()
        file_ext = match.group(2).lower()
        filename = match.group(0).lower()

        row: dict = {
            "url": paper_url,
            "filename": filename,
            "type": file_ext,
            "paper_id": paper_id,
            "title": title,
            "authors": authors,
            "document_date": document_date,
            "subgroup": subgroup,
            "raw_columns": raw_columns,
            "raw_links": raw_links,
        }
        intent = _infer_intent(title)
        if intent is not None:
            row["intent"] = intent
        return row

    return None


def _find_table_in_section(anchor) -> Tag | None:
    """Find the first <table> belonging to a mailing section.

    Stops at the next mailing anchor to avoid cross-mailing attribution.
    """
    if not anchor:
        return None
    anchor_id = anchor.get("id") or anchor.get("name") or ""
    if not _MAILING_ANCHOR_RE.match(anchor_id):
        return None
    for elem in anchor.next_elements:
        if not hasattr(elem, "name"):
            continue
        if elem is anchor:
            continue
        if elem.name == "table":
            return elem
        if not hasattr(elem, "get"):
            continue
        next_id = elem.get("id") or elem.get("name") or ""
        if next_id and _MAILING_ANCHOR_RE.match(next_id) and next_id != anchor_id:
            return None
    return None


def _dedupe_by_filename(papers: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for p in papers:
        if p["filename"] not in seen:
            seen.add(p["filename"])
            unique.append(p)
    return unique


def _parse_table_rows(table: Tag, page_url: str) -> list[dict]:
    """Extract paper dicts from a single mailing table."""
    paper_rows: list[dict] = []
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if not cells or any(cell.get("colspan") for cell in cells):
            continue
        paper = _extract_paper_metadata_from_row(cells, page_url)
        if paper:
            paper_rows.append(paper)
    return _dedupe_by_filename(paper_rows)


def parse_papers_for_mailing(
    html: str,
    mailing_date: str,
    page_url: str,
) -> list[dict]:
    """Parse papers for a single mailing from a year index HTML page.

    Returns list of dicts with: paper_id, url, filename, type, title,
    authors, document_date, subgroup, raw_columns, raw_links, and optionally
    intent (``"info"`` or ``"ask"`` when the title carries the prefix).
    """
    soup = BeautifulSoup(html, "html.parser")
    anchor_id = f"mailing{mailing_date}"
    anchor = soup.find(id=anchor_id) or soup.find(attrs={"name": anchor_id})
    if not anchor:
        logger.warning("Anchor %s not found on %s", anchor_id, page_url)
        return []

    table = _find_table_in_section(anchor)
    if not table:
        logger.warning("No table found after anchor %s", anchor_id)
        return []

    return _parse_table_rows(table, page_url)


def parse_all_mailings(
    html: str,
    page_url: str,
) -> dict[str, list[dict]]:
    """Parse ALL mailings from a year index HTML page.

    Finds every mailing anchor on the page and parses its table.
    Returns ``{"2026-01": [...], "2026-02": [...], ...}``.
    """
    soup = BeautifulSoup(html, "html.parser")
    result: dict[str, list[dict]] = {}

    for anchor in soup.find_all(id=_MAILING_ANCHOR_RE):
        anchor_id = anchor.get("id") or ""
        mailing_id = anchor_id.replace("mailing", "")
        table = _find_table_in_section(anchor)
        if not table:
            logger.warning("No table found after anchor %s", anchor_id)
            continue
        papers = _parse_table_rows(table, page_url)
        if papers:
            result[mailing_id] = papers

    for anchor in soup.find_all(attrs={"name": _MAILING_ANCHOR_RE}):
        name = anchor.get("name") or ""
        mailing_id = name.replace("mailing", "")
        if mailing_id in result:
            continue
        table = _find_table_in_section(anchor)
        if not table:
            logger.warning("No table found after anchor %s", name)
            continue
        papers = _parse_table_rows(table, page_url)
        if papers:
            result[mailing_id] = papers

    return result


_YEAR_LINK_RE = re.compile(r"(?:^|/)(\d{4})/?$")


def _fetch_year_page(year: str, *, timeout: float = 60.0) -> tuple[str, str]:
    """Fetch a year index page. Returns ``(html, page_url)``."""
    url = f"{BASE_URL}/{year}/"
    logger.info("Fetching year page %s from %s", year, url)
    response = httpx.get(
        url,
        timeout=timeout,
        headers={"User-Agent": DEFAULT_USER_AGENT},
    )
    response.raise_for_status()
    return response.text, url


def discover_years(*, timeout: float = 60.0) -> list[str]:
    """Fetch the root papers index and return all available year strings, sorted."""
    root_url = f"{BASE_URL}/"
    logger.info("Discovering years from %s", root_url)
    response = httpx.get(
        root_url,
        timeout=timeout,
        headers={"User-Agent": DEFAULT_USER_AGENT},
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    years: set[str] = set()
    for link in soup.find_all("a", href=True):
        m = _YEAR_LINK_RE.search(link["href"])
        if m:
            years.add(m.group(1))
    return sorted(years)


def fetch_all_mailings_for_year(
    year: str,
    *,
    timeout: float = 60.0,
) -> dict[str, list[dict]]:
    """Fetch all mailings for a year from open-std.org.

    One HTTP request. Returns ``{"2026-01": [...], "2026-02": [...], ...}``.
    """
    try:
        html, page_url = _fetch_year_page(year, timeout=timeout)
    except httpx.HTTPError:
        logger.exception("Failed to fetch year page for %s.", year)
        return {}

    return parse_all_mailings(html, page_url)


def fetch_papers_for_year(
    year: str,
    *,
    timeout: float = 60.0,
) -> list[dict]:
    """Fetch all paper metadata for a given year from open-std.org.

    Fetches the year index page once and merges all monthly mailings into a
    single flat list. Papers are de-duplicated by ``paper_id``; the last
    mailing in the year wins for a given paper ID (most recent revision).

    Returns an empty list if the year page cannot be fetched.
    """
    all_mailings = fetch_all_mailings_for_year(year, timeout=timeout)
    if not all_mailings:
        logger.warning("No mailings found on year page for %s.", year)
        return []
    # Merge monthly mailings; later months overwrite earlier ones for same paper_id.
    seen: dict[str, dict] = {}
    for _mailing_id, papers in sorted(all_mailings.items()):
        for paper in papers:
            pid = paper.get("paper_id", "")
            if pid:
                seen[pid] = paper
    return list(seen.values())


def fetch_paper_ids_for_year(year: str) -> list[str]:
    """Fetch just the paper IDs for a year."""
    papers = fetch_papers_for_year(year)
    return [p["paper_id"] for p in papers]
