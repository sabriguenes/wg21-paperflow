#
# Copyright (c) 2026 C++ Alliance (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Tests for parse_all_mailings, discover_years, and fetch_all_mailings_for_year."""

from __future__ import annotations

from mailing.scrape import (
    discover_years,
    fetch_papers_for_year,
    parse_all_mailings,
)

YEAR_PAGE_HTML = """\
<html><body>
<a id="mailing2026-04">2026-04 post-Croydon mailing</a>
<table>
<tr><td><a href="p3000r5.pdf">P3000R5</a></td><td>Contracts</td><td>Berne</td><td>2026-03-15</td><td></td><td></td><td>CWG</td><td></td></tr>
<tr><td><a href="p3100r1.html">P3100R1</a></td><td>Reflection</td><td>Childers</td><td>2026-03-10</td><td></td><td></td><td>EWG</td><td></td></tr>
</table>

<a id="mailing2026-02">2026-02 pre-Croydon mailing</a>
<table>
<tr><td><a href="p2900r14.pdf">P2900R14</a></td><td>Old Contracts</td><td>Berne</td><td>2026-01-20</td><td></td><td></td><td>CWG</td><td></td></tr>
</table>
</body></html>
"""

ROOT_PAGE_HTML = """\
<html><body>
<ul>
<li><a href="/jtc1/sc22/wg21/docs/papers/2026/">2026</a></li>
<li><a href="/jtc1/sc22/wg21/docs/papers/2025/">2025</a></li>
<li><a href="/jtc1/sc22/wg21/docs/papers/2024/">2024</a></li>
</ul>
</body></html>
"""


_BASE = "https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2026/"


def test_parse_all_mailings_finds_both():
    result = parse_all_mailings(YEAR_PAGE_HTML, _BASE)
    assert "2026-04" in result
    assert "2026-02" in result
    assert len(result["2026-04"]) == 2
    assert len(result["2026-02"]) == 1


def test_parse_all_mailings_paper_ids():
    result = parse_all_mailings(YEAR_PAGE_HTML, _BASE)
    ids_04 = {p["paper_id"] for p in result["2026-04"]}
    ids_02 = {p["paper_id"] for p in result["2026-02"]}
    assert ids_04 == {"p3000r5", "p3100r1"}
    assert ids_02 == {"p2900r14"}


def test_parse_all_mailings_empty_page():
    result = parse_all_mailings("<html><body></body></html>", _BASE)
    assert result == {}


_PAGE_URL = "https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2026/"


def test_fetch_papers_for_year_returns_merged_papers(monkeypatch):
    """fetch_papers_for_year merges all monthly mailings for the year."""
    import mailing.scrape as mod

    def fake_fetch(year, *, timeout=60.0):
        assert year == "2026"
        return parse_all_mailings(YEAR_PAGE_HTML, _PAGE_URL)

    monkeypatch.setattr(mod, "fetch_all_mailings_for_year", fake_fetch)
    papers = fetch_papers_for_year("2026")
    # All papers from both mailings (3 total, de-duped by paper_id)
    assert len(papers) == 3
    pids = {p["paper_id"] for p in papers}
    assert "p3000r5" in pids
    assert "p2900r14" in pids


def test_fetch_papers_for_year_empty_year(monkeypatch):
    import mailing.scrape as mod

    def fake_fetch(year, *, timeout=60.0):
        return {}

    monkeypatch.setattr(mod, "fetch_all_mailings_for_year", fake_fetch)
    papers = fetch_papers_for_year("2099")
    assert papers == []


def test_discover_years_parses_root(monkeypatch):
    import httpx

    class FakeResponse:
        status_code = 200
        text = ROOT_PAGE_HTML
        def raise_for_status(self):
            pass

    monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResponse())
    years = discover_years()
    assert years == ["2024", "2025", "2026"]


ROOT_PAGE_HTML_RELATIVE = """\
<html><body>
<ul>
<li><a href="2026/">2026</a></li>
<li><a href="2025/">2025</a></li>
<li><a href="1989/">1989</a></li>
<li><a href="index.html">index</a></li>
<li><a href="archive2024/">archive2024</a></li>
</ul>
</body></html>
"""


def test_discover_years_parses_root_relative_hrefs(monkeypatch):
    """The live open-std.org root serves relative hrefs like ``<a href="2026/">``.

    Regression: the old absolute-only pattern returned ``[]`` against this layout.
    """
    import httpx

    class FakeResponse:
        status_code = 200
        text = ROOT_PAGE_HTML_RELATIVE
        def raise_for_status(self):
            pass

    monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResponse())
    years = discover_years()
    assert years == ["1989", "2025", "2026"]
