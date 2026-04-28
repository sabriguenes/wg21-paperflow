"""Tests for lib.html.extract."""

from tomd.lib.html.extract import (
    parse_html, detect_generator, extract_metadata, strip_boilerplate,
    _extract_generic_metadata, _extract_wg21_metadata, _match_field,
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
