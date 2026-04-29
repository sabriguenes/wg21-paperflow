"""Tests for lib.html.extract."""

from tomd.lib.html.extract import (
    parse_html, detect_generator, extract_metadata, strip_boilerplate,
    _extract_generic_metadata, _extract_wg21_metadata, _match_field,
    _extract_mailto_email, _extract_mailto_authors, _enrich_reply_to,
)


class TestDetectGenerator:
    def test_mpark(self):
        html = '<meta name="generator" content="mpark/wg21" />'
        assert detect_generator(parse_html(html)) == "mpark"

    def test_bikeshed(self):
        html = '<meta content="Bikeshed version abc" name="generator">'
        assert detect_generator(parse_html(html)) == "bikeshed"

    def test_hackmd_link(self):
        html = '<link href="https://hackmd.io/favicon.ico" rel="icon">'
        assert detect_generator(parse_html(html)) == "hackmd"

    def test_hackmd_title(self):
        html = "<title>HackMD doc</title>"
        assert detect_generator(parse_html(html)) == "hackmd"

    def test_hand_written(self):
        html = "<address>Author info</address>"
        assert detect_generator(parse_html(html)) == "hand-written"

    def test_mpark_fallback_header(self):
        html = '<header id="title-block-header"><h1>Title</h1></header>'
        assert detect_generator(parse_html(html)) == "mpark"

    def test_unknown(self):
        html = "<html><body><p>Hello</p></body></html>"
        assert detect_generator(parse_html(html)) == "unknown"

    def test_meta_bikeshed_wins_over_address(self):
        html = """
        <meta name="generator" content="Bikeshed 1.0">
        <address>Street</address>
        """
        assert detect_generator(parse_html(html)) == "bikeshed"

    def test_link_without_hackmd_not_hackmd(self):
        html = '<link href="https://example.com/favicon.ico" rel="icon">'
        assert detect_generator(parse_html(html)) == "unknown"

    def test_title_nested_hackmd_when_string_none(self):
        html = "<title><b>HackMD</b> doc</title>"
        assert detect_generator(parse_html(html)) == "hackmd"

    def test_schultke_by_code_block_element(self):
        html = "<html><body><code-block>int x;</code-block></body></html>"
        assert detect_generator(parse_html(html)) == "schultke"

    def test_dascandy_fiets_by_meta(self):
        html = '<meta name="generator" content="dascandy/fiets">'
        assert detect_generator(parse_html(html)) == "dascandy/fiets"


class TestExtractMparkMetadata:
    MPARK_HTML = """
    <header id="title-block-header">
    <h1 class="title">any_view</h1>
    <table>
      <tr><td>Document #:</td><td>P3411R5</td></tr>
      <tr><td>Date:</td><td>2026-01-25</td></tr>
      <tr><td>Audience:</td><td>SG9, LEWG</td></tr>
      <tr><td>Reply-to:</td><td>
        Alice Smith<br>&lt;<a href="mailto:alice@x.com">alice@x.com</a>&gt;
      </td></tr>
    </table>
    </header>
    """

    def test_title(self):
        soup = parse_html(self.MPARK_HTML)
        meta = extract_metadata(soup, "mpark")
        assert meta["title"] == "any_view"

    def test_document(self):
        soup = parse_html(self.MPARK_HTML)
        meta = extract_metadata(soup, "mpark")
        assert meta["document"] == "P3411R5"

    def test_date(self):
        soup = parse_html(self.MPARK_HTML)
        meta = extract_metadata(soup, "mpark")
        assert meta["date"] == "2026-01-25"

    def test_audience(self):
        soup = parse_html(self.MPARK_HTML)
        meta = extract_metadata(soup, "mpark")
        assert meta["audience"] == "SG9, LEWG"

    def test_reply_to(self):
        soup = parse_html(self.MPARK_HTML)
        meta = extract_metadata(soup, "mpark")
        assert "reply-to" in meta
        assert any("Alice" in a and "alice@x.com" in a
                    for a in meta["reply-to"])

    def test_no_header_empty_metadata(self):
        soup = parse_html("<html><body><p>x</p></body></html>")
        assert extract_metadata(soup, "mpark") == {}

    def test_header_no_table_title_only(self):
        html = """
        <header id="title-block-header">
        <h1 class="title">Only Title</h1>
        </header>
        """
        meta = extract_metadata(parse_html(html), "mpark")
        assert meta == {"title": "Only Title"}

    def test_table_row_single_td_skipped(self):
        html = """
        <header id="title-block-header">
        <h1 class="title">T</h1>
        <table>
        <tr><td>Solo cell</td></tr>
        <tr><td>Document #:</td><td>P1111R0</td></tr>
        </table>
        </header>
        """
        meta = extract_metadata(parse_html(html), "mpark")
        assert meta["document"] == "P1111R0"

    def test_reply_to_non_ascii_name(self):
        html = """
        <header id="title-block-header">
        <h1 class="title">T</h1>
        <table>
        <tr><td>Reply-to:</td><td>
          Johel Ernesto Guerrero Pe&ntilde;a<br>&lt;<a href="mailto:j@x.com">j@x.com</a>&gt;
        </td></tr>
        </table>
        </header>
        """
        meta = extract_metadata(parse_html(html), "mpark")
        assert "reply-to" in meta
        assert any("ñ" in a for a in meta["reply-to"]), \
            f"Expected decoded ñ in author names, got: {meta['reply-to']}"

    def test_reply_to_email_only_line(self):
        html = """
        <header id="title-block-header">
        <h1 class="title">E</h1>
        <table>
        <tr><td>Reply-to:</td><td>
        orphan@x.com
        </td></tr>
        </table>
        </header>
        """
        meta = extract_metadata(parse_html(html), "mpark")
        assert "reply-to" in meta
        assert any("orphan@x.com" in a for a in meta["reply-to"])


class TestExtractHackmdAndGenericMetadata:
    GENERIC_DOC_TABLE = """
    <html><body>
    <h1>GH1</h1>
    <table>
    <tr><td>Document number</td><td>P8888R1</td></tr>
    <tr><td>Date</td><td>2026-03-15</td></tr>
    <tr><td>Audience</td><td>EWG</td></tr>
    </table>
    </body></html>
    """

    def test_hackmd_uses_generic_extractor(self):
        soup = parse_html(self.GENERIC_DOC_TABLE)
        meta = extract_metadata(soup, "hackmd")
        assert meta["title"] == "GH1"
        assert meta["document"] == "P8888R1"
        assert meta["date"] == "2026-03-15"
        assert meta["audience"] == "EWG"

    def test_generic_doc_label_variant(self):
        html = """
        <html><body><h1>H</h1>
        <table><tr><td>Doc #</td><td>P7777R0</td></tr></table>
        </body></html>
        """
        meta = extract_metadata(parse_html(html), "unknown")
        assert meta["document"] == "P7777R0"

    def test_generic_th_td_uses_last_cell(self):
        html = """
        <html><body><h1>X</h1>
        <table><tr><th>Document</th><td>ignored</td><td>P6666R0</td></tr></table>
        </body></html>
        """
        meta = extract_metadata(parse_html(html), "unknown")
        assert meta["document"] == "P6666R0"


class TestExtractBikeshedMetadata:
    BIKESHED_HTML = """
    <h1 class="p-name no-ref" id="title">P3953R0<br>Rename std::runtime_format</h1>
    <h2 class="no-num" id="profile-and-date">
      Published, <time class="dt-updated" datetime="2025-12-28">2025-12-28</time>
    </h2>
    <dl>
      <dt class="editor">Author:</dt>
      <dd class="editor"><a class="email" href="mailto:bob@x.com">Bob Jones</a></dd>
      <dt>Audience:</dt>
      <dd>LEWG</dd>
    </dl>
    """

    def test_title(self):
        soup = parse_html(self.BIKESHED_HTML)
        meta = extract_metadata(soup, "bikeshed")
        assert "Rename" in meta.get("title", "")

    def test_document(self):
        soup = parse_html(self.BIKESHED_HTML)
        meta = extract_metadata(soup, "bikeshed")
        assert meta["document"] == "P3953R0"

    def test_date(self):
        soup = parse_html(self.BIKESHED_HTML)
        meta = extract_metadata(soup, "bikeshed")
        assert meta["date"] == "2025-12-28"

    def test_audience(self):
        soup = parse_html(self.BIKESHED_HTML)
        meta = extract_metadata(soup, "bikeshed")
        assert meta.get("audience") == "LEWG"

    def test_reply_to_from_dl(self):
        soup = parse_html(self.BIKESHED_HTML)
        meta = extract_metadata(soup, "bikeshed")
        assert "reply-to" in meta
        assert any("Bob" in a and "bob@x.com" in a for a in meta["reply-to"])

    def test_title_only_when_no_doc_prefix(self):
        html = '<h1 class="p-name">Plain WG21 Title</h1>'
        meta = extract_metadata(parse_html(html), "bikeshed")
        assert meta.get("title") == "Plain WG21 Title"
        assert "document" not in meta

    def test_dl_scoped_to_spec_metadata_not_earlier_dl(self):
        """Regression: audience/author must come from spec-metadata dl, not an earlier dl."""
        html = """
        <h1 class="p-name">P1000R0 My Paper</h1>
        <dl>
          <dt>Unrelated Term</dt>
          <dd>Unrelated definition - should not be audience</dd>
        </dl>
        <div data-fill-with="spec-metadata">
          <dl>
            <dt>Audience:</dt>
            <dd>EWG</dd>
          </dl>
        </div>
        """
        meta = extract_metadata(parse_html(html), "bikeshed")
        assert meta.get("audience") == "EWG"

    def test_time_dt_updated_from_datetime_attr(self):
        html = """
        <h1 class="p-name">P3999R1 X</h1>
        <time class="dt-updated" datetime="2024-06-01">ignored text</time>
        """
        meta = extract_metadata(parse_html(html), "bikeshed")
        assert meta["date"] == "2024-06-01"

    def test_time_dt_updated_from_text_when_no_attr(self):
        html = """
        <h1 class="p-name">P3999R1 X</h1>
        <time class="dt-updated">2024-07-02</time>
        """
        meta = extract_metadata(parse_html(html), "bikeshed")
        assert meta["date"] == "2024-07-02"


class TestExtractHandwrittenMetadata:
    def test_address_block(self):
        html = """
        <address>
        Document number: P4005R0<br/>
        Audience: EWG<br/>
        <a href="mailto:v@g.com">Ville V</a><br/>
        2026-02-02<br/>
        </address>
        <h1>Title Here</h1>
        """
        soup = parse_html(html)
        meta = extract_metadata(soup, "hand-written")
        assert meta["document"] == "P4005R0"
        assert meta["audience"] == "EWG"
        assert meta["date"] == "2026-02-02"
        assert meta["title"] == "Title Here"

    def test_table_header_only(self):
        html = """
        <table class="header">
        <tr><th>Document number</th><td>P5000R0</td></tr>
        <tr><th>Date</th><td>2026-01-10</td></tr>
        <tr><th>Audience</th><td>LEWG</td></tr>
        <tr><th>Reply-to</th><td><a href="mailto:a@b.co">Ann A</a></td></tr>
        </table>
        <h1>Hdr Title</h1>
        """
        soup = parse_html(html)
        meta = extract_metadata(soup, "hand-written")
        assert meta["document"] == "P5000R0"
        assert meta["date"] == "2026-01-10"
        assert meta["audience"] == "LEWG"
        assert meta["title"] == "Hdr Title"
        assert any("Ann" in x and "a@b.co" in x for x in meta["reply-to"])


class TestAudienceBrSeparation:
    """Audience cells with <br>-separated values must produce comma-separated text."""

    def test_mpark_br_separated_audience(self):
        html = """
        <header id="title-block-header">
        <h1 class="title">T</h1>
        <table>
          <tr><td>Document #:</td><td>P3045R7</td></tr>
          <tr><td>Audience:</td><td>
            LEWG Library Evolution Working Group<br>
            SG6 Numerics<br>
            SG16 Unicode<br>
            SG20 Education<br>
          </td></tr>
        </table>
        </header>
        """
        meta = extract_metadata(parse_html(html), "mpark")
        aud = meta["audience"]
        assert "LEWG" in aud
        assert "SG6" in aud
        assert "SG16" in aud
        assert "SG20" in aud
        assert "," in aud

    def test_generic_br_separated_audience(self):
        html = """
        <table>
          <tr><th>Audience:</th><td>EWG<br>CWG<br>SG12</td></tr>
        </table>
        """
        meta = _extract_generic_metadata(parse_html(html))
        aud = meta["audience"]
        assert "EWG" in aud
        assert "CWG" in aud
        assert "SG12" in aud
        assert "," in aud

    def test_single_audience_no_br(self):
        html = """
        <header id="title-block-header">
        <h1 class="title">T</h1>
        <table>
          <tr><td>Audience:</td><td>LEWG</td></tr>
        </table>
        </header>
        """
        meta = extract_metadata(parse_html(html), "mpark")
        assert meta["audience"] == "LEWG"

    def test_handwritten_table_br_audience(self):
        html = """
        <table class="header">
          <tr><th>Audience</th><td>SG1<br>LEWG<br>LWG</td></tr>
        </table>
        <h1>Title</h1>
        """
        meta = extract_metadata(parse_html(html), "hand-written")
        aud = meta["audience"]
        assert "SG1" in aud
        assert "LEWG" in aud
        assert "LWG" in aud
        assert "," in aud

    def test_wg21_br_audience(self):
        html = """
        <div class="wg21-head">
          <dl>
            <dt>Audience:</dt><dd>SG1<br>LEWG</dd>
          </dl>
        </div>
        """
        meta = _extract_wg21_metadata(parse_html(html))
        aud = meta["audience"]
        assert "SG1" in aud
        assert "LEWG" in aud
        assert "," in aud

    def test_bikeshed_br_audience(self):
        html = """
        <div data-fill-with="spec-metadata">
          <dl>
            <dt>Audience:</dt>
            <dd>EWG<br>LEWG</dd>
          </dl>
        </div>
        """
        meta = extract_metadata(parse_html(html), "bikeshed")
        aud = meta["audience"]
        assert "EWG" in aud
        assert "LEWG" in aud
        assert "," in aud


class TestStripBoilerplate:
    def test_removes_style_script(self):
        html = "<style>body{}</style><script>x()</script><p>Keep</p>"
        soup = parse_html(html)
        strip_boilerplate(soup, "mpark")
        assert soup.find("style") is None
        assert soup.find("script") is None
        assert soup.find("p") is not None

    def test_removes_toc(self):
        html = '<div id="TOC"><ul><li>Item</li></ul></div><p>Body</p>'
        soup = parse_html(html)
        strip_boilerplate(soup, "mpark")
        assert soup.find(id="TOC") is None

    def test_unknown_returns_problem(self):
        html = "<p>Hello</p>"
        soup = parse_html(html)
        problems = strip_boilerplate(soup, "unknown")
        assert len(problems) == 1
        assert "Unrecognized" in problems[0]

    def test_known_returns_no_problem(self):
        html = '<header id="title-block-header"></header><p>Body</p>'
        soup = parse_html(html)
        problems = strip_boilerplate(soup, "mpark")
        assert len(problems) == 0

    def test_removes_lowercase_toc_id(self):
        html = '<div id="toc"><p>x</p></div><p>Keep</p>'
        soup = parse_html(html)
        strip_boilerplate(soup, "mpark")
        assert soup.find(id="toc") is None
        assert soup.find("p").get_text() == "Keep"

    def test_removes_nav_table_of_contents(self):
        html = (
            '<nav data-fill-with="table-of-contents"><ul><li>a</li></ul></nav>'
            "<p>Body</p>"
        )
        soup = parse_html(html)
        strip_boilerplate(soup, "mpark")
        assert soup.find("nav") is None

    def test_mpark_removes_title_block_header(self):
        html = '<header id="title-block-header"><p>H</p></header><p>B</p>'
        soup = parse_html(html)
        strip_boilerplate(soup, "mpark")
        assert soup.find("header", id="title-block-header") is None
        assert soup.get_text().strip() == "B"

    def test_bikeshed_removes_chrome(self):
        html = """
        <div data-fill-with="metadata">M</div>
        <h1 class="p-name">T</h1>
        <h2 id="profile-and-date">D</h2>
        <p>Keep</p>
        """
        soup = parse_html(html)
        strip_boilerplate(soup, "bikeshed")
        assert soup.find("div", attrs={"data-fill-with": True}) is None
        assert soup.find("h1", class_="p-name") is None
        assert soup.find("h2", id="profile-and-date") is None
        assert soup.find("p").get_text() == "Keep"

    def test_hand_written_removes_address_and_header_table(self):
        html = """
        <address>A</address>
        <table class="header"><tr><th>X</th><td>Y</td></tr></table>
        <p>Body</p>
        """
        soup = parse_html(html)
        strip_boilerplate(soup, "hand-written")
        assert soup.find("address") is None
        assert soup.find("table", class_="header") is None
        assert soup.find("p").get_text() == "Body"


# ---------------------------------------------------------------------------
# wg21 generator (cow-tool / wg21-head format)
# ---------------------------------------------------------------------------

def _wg21_html(extra_dl: str = "", title: str = "My Paper: Title") -> str:
    return f"""
    <div class="wg21-head">
      <h1>{title}</h1>
      <dl>
        <dt>Document number:</dt><dd>P9999R0</dd>
        <dt>Date:</dt><dd>2026-01-15</dd>
        <dt>Audience:</dt><dd>EWG</dd>
        <dt>Reply-to:</dt><dd>Jane Doe &lt;jane@example.com&gt;</dd>
        {extra_dl}
      </dl>
      <hr>
    </div>
    <h2>Introduction</h2><p>Body text.</p>
    """


class TestDetectGeneratorWg21:
    def test_wg21_head_detected(self):
        soup = parse_html('<div class="wg21-head"><h1>T</h1></div>')
        assert detect_generator(soup) == "wg21"

    def test_without_wg21_head_stays_unknown(self):
        soup = parse_html("<html><body><p>Hello</p></body></html>")
        assert detect_generator(soup) != "wg21"

    def test_wg21_takes_priority_over_unknown(self):
        # No mpark/bikeshed/hackmd/address signals, but has wg21-head
        soup = parse_html('<div class="wg21-head"><dl></dl></div>')
        assert detect_generator(soup) == "wg21"


class TestExtractWg21Metadata:
    def test_all_fields(self):
        meta = _extract_wg21_metadata(parse_html(_wg21_html()))
        assert meta["document"] == "P9999R0"
        assert meta["date"] == "2026-01-15"
        assert meta["audience"] == "EWG"
        assert meta["title"] == "My Paper: Title"
        assert any("jane@example.com" in a for a in meta["reply-to"])

    def test_doc_no_label_variation(self):
        """'Doc. No.:' (n5034-style) maps to document field via _normalize_label."""
        html = """
        <div class="wg21-head">
          <dl>
            <dt>Doc. No.:</dt><dd>N5034</dd>
            <dt>Date:</dt><dd>2026-03-01</dd>
            <dt>Audience:</dt><dd>All</dd>
          </dl>
        </div>
        """
        meta = _extract_wg21_metadata(parse_html(html))
        assert meta.get("document") == "N5034"

    def test_reply_to_plain_name_no_email(self):
        html = """
        <div class="wg21-head">
          <dl>
            <dt>Document number:</dt><dd>P1R0</dd>
            <dt>Reply-to:</dt><dd>Alice Smith</dd>
          </dl>
        </div>
        """
        meta = _extract_wg21_metadata(parse_html(html))
        assert "Alice Smith" in meta.get("reply-to", [])

    def test_unknown_labels_ignored(self):
        html = """
        <div class="wg21-head">
          <dl>
            <dt>Document number:</dt><dd>P1234R0</dd>
            <dt>GitHub Issue:</dt><dd>wg21.link/P1234/github</dd>
            <dt>Source:</dt><dd>github.com/example</dd>
          </dl>
        </div>
        """
        meta = _extract_wg21_metadata(parse_html(html))
        assert "github issue" not in meta
        assert "source" not in meta
        assert meta.get("document") == "P1234R0"

    def test_no_container_returns_empty(self):
        """Graceful failure: no wg21-head at all."""
        html = "<html><body><p>No metadata here.</p></body></html>"
        assert _extract_wg21_metadata(parse_html(html)) == {}

    def test_missing_dl_returns_title_only(self):
        html = '<div class="wg21-head"><h1>Only Title</h1></div>'
        meta = _extract_wg21_metadata(parse_html(html))
        assert meta.get("title") == "Only Title"
        assert "document" not in meta

    def test_malformed_dl_no_crash(self):
        """More <dt> than <dd> - zip stops at the shorter list, no exception."""
        html = """
        <div class="wg21-head">
          <dl>
            <dt>Document number:</dt><dd>P1234R0</dd>
            <dt>Date:</dt>
          </dl>
        </div>
        """
        meta = _extract_wg21_metadata(parse_html(html))
        assert meta.get("document") == "P1234R0"  # first pair extracted fine


class TestStripBoilerplateWg21:
    def test_removes_wg21_head(self):
        soup = parse_html(_wg21_html())
        strip_boilerplate(soup, "wg21")
        assert soup.find("div", class_="wg21-head") is None

    def test_removes_toc_div(self):
        html = '<div class="wg21-head"></div><div class="toc"><a>1</a></div><p>Body</p>'
        soup = parse_html(html)
        strip_boilerplate(soup, "wg21")
        assert soup.find("div", class_="toc") is None

    def test_body_content_preserved(self):
        soup = parse_html(_wg21_html())
        strip_boilerplate(soup, "wg21")
        assert "Body text." in soup.get_text()

    def test_wg21_generates_no_problems(self):
        soup = parse_html(_wg21_html())
        problems = strip_boilerplate(soup, "wg21")
        assert problems == []


# ---------------------------------------------------------------------------
# Generic extractor baseline
# ---------------------------------------------------------------------------

class TestExtractGenericMetadata:
    def test_document_number_label(self):
        html = """
        <table>
          <tr><th>Document number:</th><td>P1234R5</td></tr>
        </table>
        """
        meta = _extract_generic_metadata(parse_html(html))
        assert meta.get("document") == "P1234R5"

    def test_doc_no_variation(self):
        """n5034-style 'Doc. No.:' label."""
        html = """
        <table border="1">
          <tr><th>Doc. No.:</th><td>N5034</td></tr>
          <tr><th>Date:</th><td>2026-03-01</td></tr>
        </table>
        """
        meta = _extract_generic_metadata(parse_html(html))
        assert meta.get("document") == "N5034"
        assert meta.get("date") == "2026-03-01"

    def test_date_label(self):
        html = "<table><tr><th>Date:</th><td>2025-11-01</td></tr></table>"
        meta = _extract_generic_metadata(parse_html(html))
        assert meta.get("date") == "2025-11-01"

    def test_audience_label(self):
        html = "<table><tr><th>Audience:</th><td>SG1</td></tr></table>"
        meta = _extract_generic_metadata(parse_html(html))
        assert meta.get("audience") == "SG1"

    def test_no_table_returns_empty(self):
        """Known failure case: plain prose with no metadata table."""
        html = "<html><body><h1>Title</h1><p>Body text only.</p></body></html>"
        meta = _extract_generic_metadata(parse_html(html))
        assert meta == {} or "document" not in meta

    def test_unrecognized_table_labels_return_empty(self):
        """Table exists but no WG21-recognizable labels."""
        html = """
        <table>
          <tr><th>Color</th><td>Red</td></tr>
          <tr><th>Size</th><td>Large</td></tr>
        </table>
        """
        meta = _extract_generic_metadata(parse_html(html))
        assert "document" not in meta
        assert "date" not in meta

    def test_reply_to_table_label_with_mailto(self):
        html = """
        <table>
          <tr><th>Reply to:</th>
              <td><a href="mailto:alice@example.com">Alice Smith</a></td></tr>
        </table>
        """
        meta = _extract_generic_metadata(parse_html(html))
        assert meta.get("reply-to") == ["Alice Smith <alice@example.com>"]

    def test_reply_to_multiple_authors_in_cell(self):
        html = """
        <table>
          <tr><th>Authors:</th>
              <td>
                <a href="mailto:a@x.com">Alice</a>
                <a href="mailto:b@x.com">Bob</a>
              </td></tr>
        </table>
        """
        meta = _extract_generic_metadata(parse_html(html))
        assert meta.get("reply-to") == [
            "Alice <a@x.com>",
            "Bob <b@x.com>",
        ]

    def test_reply_to_mailto_fallback_no_label(self):
        """Loose mailto link outside tables is only promoted when a bare
        name already exists from table parsing.  A standalone mailto
        without any table context is not blindly assigned as reply-to."""
        html = """
        <h1>Title</h1>
        <table><tr><th>Reply to:</th><td>Eve Jones</td></tr></table>
        <a href="mailto:eve@example.com">Eve Jones</a>
        <h2>Body</h2>
        """
        meta = extract_metadata(parse_html(html), "unknown")
        assert meta.get("reply-to") == ["Eve Jones <eve@example.com>"]

    def test_reply_to_split_row_name_then_email(self):
        """Name in one row, email mailto in the next (p4160r0 pattern).

        The table loop captures the bare name; the enrichment post-pass
        merges it with the mailto email found in the metadata region.
        """
        html = """
        <table>
          <tr><td>Reply to:</td><td>Jens Maurer</td></tr>
          <tr><td></td><td><a href="mailto://jens@gmx.net">jens@gmx.net</a></td></tr>
        </table>
        """
        meta = extract_metadata(parse_html(html), "unknown")
        assert meta.get("reply-to") == ["Jens Maurer <jens@gmx.net>"]

    def test_mailto_double_slash_normalized(self):
        html = """
        <table>
          <tr><th>Reply to:</th>
              <td><a href="mailto://x@y.com">X Y</a></td></tr>
        </table>
        """
        meta = _extract_generic_metadata(parse_html(html))
        assert meta.get("reply-to") == ["X Y <x@y.com>"]


class TestExtractMailtoEmail:
    def test_standard_mailto(self):
        assert _extract_mailto_email("mailto:a@b.com") == "a@b.com"

    def test_double_slash_mailto(self):
        assert _extract_mailto_email("mailto://a@b.com") == "a@b.com"

    def test_single_slash_mailto(self):
        assert _extract_mailto_email("mailto:/a@b.com") == "a@b.com"

    def test_empty_string(self):
        assert _extract_mailto_email("") == ""

    def test_no_prefix(self):
        assert _extract_mailto_email("a@b.com") == "a@b.com"


class TestExtractMailtoAuthors:
    def test_single_mailto(self):
        html = '<div><a href="mailto:a@b.com">Alice</a></div>'
        container = parse_html(html).find("div")
        assert _extract_mailto_authors(container) == ["Alice <a@b.com>"]

    def test_multiple_mailtos(self):
        html = """
        <div>
          <a href="mailto:a@b.com">Alice</a>
          <a href="mailto:c@d.com">Bob</a>
        </div>
        """
        container = parse_html(html).find("div")
        result = _extract_mailto_authors(container)
        assert len(result) == 2
        assert "Alice <a@b.com>" in result
        assert "Bob <c@d.com>" in result

    def test_deduplicates(self):
        html = """
        <div>
          <a href="mailto:a@b.com">Alice</a>
          <a href="mailto:a@b.com">Alice</a>
        </div>
        """
        container = parse_html(html).find("div")
        assert len(_extract_mailto_authors(container)) == 1

    def test_bare_email_no_name(self):
        html = '<div><a href="mailto:a@b.com">a@b.com</a></div>'
        container = parse_html(html).find("div")
        assert _extract_mailto_authors(container) == ["<a@b.com>"]

    def test_empty_container(self):
        html = "<div><p>No links here</p></div>"
        container = parse_html(html).find("div")
        assert _extract_mailto_authors(container) == []


class TestBikeshedNestedEditorEmails:
    """Bikeshed papers where dd.editor contains <a class='email'> or inline spans."""

    def test_email_link_in_nested_dd(self):
        html = """
        <meta name="generator" content="Bikeshed">
        <h1 class="p-name">P1000R0 My Paper</h1>
        <div data-fill-with="spec-metadata">
          <dl>
            <dt class="editor">Editor:</dt>
            <dd class="editor h-card vcard">
              <a class="p-name fn u-url url" href="https://example.com">Daniel Towner</a>
              (<a class="p-org org h-org" href="https://intel.com">Intel</a>)
              <a class="u-email email" href="mailto:daniel.towner@intel.com">daniel.towner@intel.com</a>
            </dd>
            <dt>Audience:</dt>
            <dd>SG1</dd>
          </dl>
        </div>
        """
        meta = extract_metadata(parse_html(html), "bikeshed")
        assert "reply-to" in meta
        assert any("daniel.towner@intel.com" in a for a in meta["reply-to"])
        assert any("Daniel Towner" in a for a in meta["reply-to"])

    def test_multiple_editors(self):
        html = """
        <meta name="generator" content="Bikeshed">
        <h1 class="p-name">P2000R0 Paper Two</h1>
        <div data-fill-with="spec-metadata">
          <dl>
            <dt class="editor">Editors:</dt>
            <dd class="editor h-card">
              <span class="p-name fn">Alice</span>
              <a class="u-email email" href="mailto:alice@x.com">alice@x.com</a>
            </dd>
            <dd class="editor h-card">
              <span class="p-name fn">Bob</span>
              <a class="u-email email" href="mailto:bob@y.com">bob@y.com</a>
            </dd>
          </dl>
        </div>
        """
        meta = extract_metadata(parse_html(html), "bikeshed")
        assert len(meta.get("reply-to", [])) == 2
        assert any("alice@x.com" in a for a in meta["reply-to"])
        assert any("bob@y.com" in a for a in meta["reply-to"])

    def test_inline_email_in_span(self):
        html = """
        <meta name="generator" content="Bikeshed">
        <h1 class="p-name">P3000R0 Inline</h1>
        <div data-fill-with="spec-metadata">
          <dl>
            <dt class="editor">Author:</dt>
            <dd class="editor">
              <span class="p-name fn">Victor Z victor@z.com</span>
            </dd>
          </dl>
        </div>
        """
        meta = extract_metadata(parse_html(html), "bikeshed")
        assert "reply-to" in meta
        assert any("victor@z.com" in a for a in meta["reply-to"])

    def test_strips_affiliation_from_name(self):
        html = """
        <meta name="generator" content="Bikeshed">
        <h1 class="p-name">P4000R0 Affiliation</h1>
        <div data-fill-with="spec-metadata">
          <dl>
            <dt class="editor">Editor:</dt>
            <dd class="editor h-card">
              <a class="p-name" href="#">Dan Towner - Intel</a>
              <a class="u-email email" href="mailto:dan@intel.com">dan@intel.com</a>
            </dd>
          </dl>
        </div>
        """
        meta = extract_metadata(parse_html(html), "bikeshed")
        assert "reply-to" in meta
        entry = meta["reply-to"][0]
        assert "Intel" not in entry.split("<")[0]
        assert "dan@intel.com" in entry


class TestMparkNoTableFallback:
    """Papers detected as mpark but lacking a metadata table."""

    def test_mailto_in_header_without_table(self):
        html = """
        <header id="title-block-header">
          <h1 class="title">My Paper</h1>
          <p>By <a href="mailto:author@example.com">Author Name</a></p>
        </header>
        """
        meta = extract_metadata(parse_html(html), "mpark")
        assert meta["title"] == "My Paper"
        assert "reply-to" in meta
        assert any("author@example.com" in a for a in meta["reply-to"])

    def test_no_mailto_no_table_returns_title_only(self):
        html = """
        <header id="title-block-header">
          <h1 class="title">Title Only</h1>
          <p>Some description with no emails</p>
        </header>
        """
        meta = extract_metadata(parse_html(html), "mpark")
        assert meta == {"title": "Title Only"}

    def test_pandoc_mailto_in_sibling_ul(self):
        """Pandoc: header has only <h1>, metadata is in a <ul> sibling."""
        html = """
        <header id="title-block-header">
          <h1 class="title">Timed lock algorithms</h1>
        </header>
        <ul>
          <li><strong>Reply-to:</strong> Ted Lyngmo
            <a class="email" href="mailto:ted@lyncon.se">ted@lyncon.se</a></li>
        </ul>
        """
        meta = extract_metadata(parse_html(html), "mpark")
        assert "reply-to" in meta
        assert any("Ted Lyngmo" in a and "ted@lyncon.se" in a
                    for a in meta["reply-to"])

    def test_pandoc_mailto_in_sibling_p(self):
        """Pandoc: header sibling is <p> with inline author."""
        html = """
        <header id="title-block-header">
          <h1 class="title">Constant evaluation when?</h1>
        </header>
        <p><em>Audience</em>: CWG<br/>
        S. Davis Herring &lt;<a href="mailto:herring@lanl.gov"
        class="email">herring@lanl.gov</a>&gt;<br/>
        Los Alamos National Laboratory</p>
        """
        meta = extract_metadata(parse_html(html), "mpark")
        assert "reply-to" in meta
        assert any("S. Davis Herring" in a and "herring@lanl.gov" in a
                    for a in meta["reply-to"])

    def test_with_table_uses_table_not_fallback(self):
        html = """
        <header id="title-block-header">
          <h1 class="title">T</h1>
          <a href="mailto:wrong@example.com">Wrong</a>
          <table>
            <tr><td>Reply-to:</td><td>
              Alice<br>&lt;<a href="mailto:alice@x.com">alice@x.com</a>&gt;
            </td></tr>
          </table>
        </header>
        """
        meta = extract_metadata(parse_html(html), "mpark")
        assert "reply-to" in meta
        assert any("alice@x.com" in a for a in meta["reply-to"])


class TestEnrichReplyTo:
    """Tests for the _enrich_reply_to post-pass."""

    def test_bare_name_gets_email_from_mailto(self):
        """Bare name with unassigned email in metadata region."""
        html = """
        <p><a href="mailto:cpp@kaotic.software">cpp@kaotic.software</a></p>
        <h2>Introduction</h2>
        """
        soup = parse_html(html)
        metadata = {"reply-to": ["Tiago Freire"]}
        _enrich_reply_to(soup, metadata)
        assert metadata["reply-to"] == ["Tiago Freire <cpp@kaotic.software>"]

    def test_internal_merge_bare_name_and_bare_email(self):
        """p3161r5 pattern: reply-to has <email> and bare name from
        separate table rows. Enrichment 0 merges them internally."""
        html = """<h2>Body</h2>"""
        soup = parse_html(html)
        metadata = {"reply-to": ["<cpp@kaotic.software>", "Tiago Freire"]}
        _enrich_reply_to(soup, metadata)
        assert metadata["reply-to"] == ["Tiago Freire <cpp@kaotic.software>"]

    def test_noop_when_already_complete(self):
        """Entries that already have Name <email> are not modified."""
        html = """<a href="mailto:alice@x.com">Alice</a><h2>Body</h2>"""
        soup = parse_html(html)
        metadata = {"reply-to": ["Alice <alice@x.com>"]}
        _enrich_reply_to(soup, metadata)
        assert metadata["reply-to"] == ["Alice <alice@x.com>"]

    def test_bare_email_gets_name_from_context(self):
        """Pandoc sibling: link text = email, name is in parent text."""
        html = """
        <p>Ted Lyngmo
          <a href="mailto:ted@lyncon.se">ted@lyncon.se</a></p>
        <h2>Body</h2>
        """
        soup = parse_html(html)
        metadata = {"reply-to": ["<ted@lyncon.se>"]}
        _enrich_reply_to(soup, metadata)
        assert metadata["reply-to"] == ["Ted Lyngmo <ted@lyncon.se>"]

    def test_h2_boundary_respected(self):
        """Emails after <h2> are not harvested by the enrichment pass."""
        html = """
        <table><tr><th>Reply-to:</th><td>Alice</td></tr></table>
        <h2>Introduction</h2>
        <a href="mailto:bob@example.com">bob@example.com</a>
        """
        soup = parse_html(html)
        metadata = {"reply-to": ["Alice"]}
        _enrich_reply_to(soup, metadata)
        assert metadata["reply-to"] == ["Alice"]

    def test_multiple_bare_names_multiple_emails(self):
        """1:1 merge when counts match."""
        html = """
        <a href="mailto:a@x.com">a@x.com</a>
        <a href="mailto:b@x.com">b@x.com</a>
        <h2>Body</h2>
        """
        soup = parse_html(html)
        metadata = {"reply-to": ["Alice", "Bob"]}
        _enrich_reply_to(soup, metadata)
        assert "Alice <a@x.com>" in metadata["reply-to"]
        assert "Bob <b@x.com>" in metadata["reply-to"]

    def test_count_mismatch_appends_separately(self):
        """When bare names and emails don't match 1:1, emails are appended."""
        html = """
        <a href="mailto:a@x.com">a@x.com</a>
        <a href="mailto:b@x.com">b@x.com</a>
        <h2>Body</h2>
        """
        soup = parse_html(html)
        metadata = {"reply-to": ["Alice"]}
        _enrich_reply_to(soup, metadata)
        assert "Alice" in metadata["reply-to"]
        assert "<a@x.com>" in metadata["reply-to"]
        assert "<b@x.com>" in metadata["reply-to"]

    def test_no_reply_to_bootstraps_from_metadata_region(self):
        """No reply-to key: bootstrap from mailto links in metadata region."""
        html = """<a href="mailto:a@x.com">a</a>"""
        soup = parse_html(html)
        metadata = {}
        _enrich_reply_to(soup, metadata)
        assert metadata["reply-to"] == ["<a@x.com>"]

    def test_no_reply_to_no_emails_noop(self):
        """No reply-to key and no emails: enrichment is a no-op."""
        html = """<p>Some text without emails</p>"""
        soup = parse_html(html)
        metadata = {}
        _enrich_reply_to(soup, metadata)
        assert "reply-to" not in metadata


class TestGenericMetadataAccumulation:
    """Tests for the fix-overwrite change in _extract_generic_metadata."""

    def test_reply_to_not_overwritten_by_authors(self):
        """Reply-to row with email is not replaced by a later Authors row."""
        html = """
        <table>
          <tr><th>Reply-to:</th>
              <td><a href="mailto:cpp@kaotic.software">cpp@kaotic.software</a></td></tr>
          <tr><th>Authors:</th><td>Tiago Freire</td></tr>
        </table>
        """
        meta = _extract_generic_metadata(parse_html(html))
        assert any("cpp@kaotic.software" in e for e in meta.get("reply-to", []))
        assert any("Tiago Freire" in e for e in meta.get("reply-to", []))

    def test_both_buckets_merged(self):
        """Both reply and author entries appear in the final list."""
        html = """
        <table>
          <tr><th>Reply-to:</th>
              <td><a href="mailto:a@x.com">Alice</a></td></tr>
          <tr><th>Authors:</th>
              <td><a href="mailto:b@x.com">Bob</a></td></tr>
        </table>
        """
        meta = _extract_generic_metadata(parse_html(html))
        rt = meta.get("reply-to", [])
        assert any("Alice" in e and "a@x.com" in e for e in rt)
        assert any("Bob" in e and "b@x.com" in e for e in rt)


class TestContinuationRows:
    """Tests for multi-row reply-to with empty label cells."""

    def test_multirow_reply_to_all_authors(self):
        """p3655r4 pattern: Reply-to label only on first row, empty on rest."""
        html = """
        <table><tbody>
          <tr><td>Document #</td><td>P3655R4</td></tr>
          <tr><td>Date</td><td>2025-10-05</td></tr>
          <tr><td>Reply-to</td>
              <td>Peter Bindels &lt;dascandy@gmail.com&gt;</td></tr>
          <tr><td> </td>
              <td>Hana Dusikova &lt;hanicka@hanicka.net&gt;</td></tr>
          <tr><td> </td>
              <td>Jeremy Rifkin &lt;jeremy@rifkin.dev&gt;</td></tr>
        </tbody></table>
        """
        meta = _extract_generic_metadata(parse_html(html))
        rt = meta.get("reply-to", [])
        assert len(rt) == 3
        assert any("Peter Bindels" in e and "dascandy@gmail.com" in e for e in rt)
        assert any("Hana Dusikova" in e and "hanicka@hanicka.net" in e for e in rt)
        assert any("Jeremy Rifkin" in e and "jeremy@rifkin.dev" in e for e in rt)

    def test_continuation_stops_at_next_label(self):
        """Empty rows after a non-reply label must not be treated as authors."""
        html = """
        <table>
          <tr><td>Reply-to</td><td>Alice &lt;a@x.com&gt;</td></tr>
          <tr><td>Date</td><td>2025-01-01</td></tr>
          <tr><td> </td><td>Not an author</td></tr>
        </table>
        """
        meta = _extract_generic_metadata(parse_html(html))
        rt = meta.get("reply-to", [])
        assert len(rt) == 1
        assert "Alice" in rt[0]
        assert "Not an author" not in str(rt)

    def test_single_row_unaffected(self):
        """Single reply-to row still works as before."""
        html = """
        <table>
          <tr><td>Reply-to</td>
              <td>Bob &lt;bob@x.com&gt;</td></tr>
          <tr><td>Date</td><td>2025-01-01</td></tr>
        </table>
        """
        meta = _extract_generic_metadata(parse_html(html))
        rt = meta.get("reply-to", [])
        assert len(rt) == 1
        assert "Bob" in rt[0] and "bob@x.com" in rt[0]

    def test_continuation_with_mailto(self):
        """Continuation rows with mailto links are also captured."""
        html = """
        <table>
          <tr><td>Reply to:</td><td>Jens Maurer</td></tr>
          <tr><td></td>
              <td><a href="mailto://jens@gmx.net">jens@gmx.net</a></td></tr>
        </table>
        """
        meta = _extract_generic_metadata(parse_html(html))
        rt = meta.get("reply-to", [])
        assert any("jens@gmx.net" in e for e in rt)
        assert any("Jens Maurer" in e for e in rt)
