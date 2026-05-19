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
<a id="mailing2026-04">mailing2026-04 post-Croydon</a>
<table>
<tr><td><a href="p3000r5.pdf">P3000R5</a></td><td>Contracts</td><td>Berne</td><td>2026-03-15</td><td>2026-04</td><td><a href="../2025/p3000r4.pdf">P3000R4</a></td><td>CWG</td><td>Adopted 2026-03</td></tr>
<tr><td><a href="p3100r1.html">P3100R1</a></td><td>Reflection</td><td>Childers</td><td>2026-03-10</td><td>2026-04</td><td><a href="p3100r0.html">P3100R0</a></td><td>EWG</td><td></td></tr>
</table>

<a id="mailing2026-02">mailing2026-02 pre-Croydon</a>
<table>
<tr><td><a href="p2900r14.pdf">P2900R14</a></td><td>Old Contracts</td><td>Berne</td><td>2026-01-20</td><td>2026-02</td><td><a href="../2025/p2900r13.pdf">P2900R13</a></td><td>CWG</td><td></td></tr>
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


# --- previous_version, disposition, mailing_label ---


def test_parse_paper_extracts_previous_version():
    result = parse_all_mailings(YEAR_PAGE_HTML, _BASE)
    p3000 = [p for p in result["2026-04"] if p["paper_id"] == "p3000r5"][0]
    assert p3000["previous_version"] == "p3000r4"
    assert p3000["previous_version_url"].endswith("/2025/p3000r4.pdf")


def test_parse_paper_previous_version_relative_url():
    """Previous-version link with a relative href is resolved to absolute."""
    result = parse_all_mailings(YEAR_PAGE_HTML, _BASE)
    p3100 = [p for p in result["2026-04"] if p["paper_id"] == "p3100r1"][0]
    assert p3100["previous_version"] == "p3100r0"
    assert p3100["previous_version_url"].startswith("https://")


def test_parse_paper_previous_version_empty_for_r0():
    """Papers with no prior revision have empty previous_version."""
    html = """\
<html><body>
<a id="mailing2026-01">mailing2026-01</a>
<table>
<tr><td><a href="p4000r0.pdf">P4000R0</a></td><td>New Paper</td><td>Author</td><td>2026-01-10</td><td>2026-01</td><td></td><td>EWG</td><td></td></tr>
</table>
</body></html>
"""
    result = parse_all_mailings(html, _BASE)
    paper = result["2026-01"][0]
    assert paper["previous_version"] == ""
    assert paper["previous_version_url"] == ""


def test_parse_paper_extracts_disposition():
    result = parse_all_mailings(YEAR_PAGE_HTML, _BASE)
    p3000 = [p for p in result["2026-04"] if p["paper_id"] == "p3000r5"][0]
    assert p3000["disposition"] == "Adopted 2026-03"


def test_parse_paper_disposition_empty_when_not_adopted():
    result = parse_all_mailings(YEAR_PAGE_HTML, _BASE)
    p3100 = [p for p in result["2026-04"] if p["paper_id"] == "p3100r1"][0]
    assert p3100["disposition"] == ""


def test_parse_paper_extracts_mailing_label():
    result = parse_all_mailings(YEAR_PAGE_HTML, _BASE)
    for paper in result["2026-04"]:
        assert paper["mailing_label"] == "post-Croydon"
    for paper in result["2026-02"]:
        assert paper["mailing_label"] == "pre-Croydon"


def test_mailing_label_empty_when_no_suffix():
    html = """\
<html><body>
<a id="mailing2026-01">mailing2026-01</a>
<table>
<tr><td><a href="p4000r0.pdf">P4000R0</a></td><td>Paper</td><td>A</td><td>2026-01-10</td><td>2026-01</td><td></td><td>EWG</td><td></td></tr>
</table>
</body></html>
"""
    result = parse_all_mailings(html, _BASE)
    assert result["2026-01"][0]["mailing_label"] == ""
