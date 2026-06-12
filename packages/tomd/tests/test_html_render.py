"""Tests for lib.html.render."""

from tomd.lib.html.extract import parse_html
from tomd.lib.html.render import render_body


class TestHeading:
    def test_atx_level(self):
        soup = parse_html("<h2>Introduction</h2>")
        md = render_body(soup, "mpark")
        assert "## Introduction" in md

    def test_strips_section_number_span(self):
        soup = parse_html(
            '<h1><span class="header-section-number">1</span> Abstract</h1>')
        md = render_body(soup, "mpark")
        assert "# Abstract" in md

    def test_preserves_leading_dotted_number(self):
        soup = parse_html("<h3>2.1.3 Details</h3>")
        md = render_body(soup, "mpark")
        assert "### 2.1.3 Details" in md

    def test_bold_suppressed(self):
        soup = parse_html("<h2><strong>Bold Heading</strong></h2>")
        md = render_body(soup, "mpark")
        assert "## Bold Heading" in md
        assert "**" not in md


class TestParagraph:
    def test_collapses_whitespace(self):
        soup = parse_html("<p>Hello   \n  world</p>")
        md = render_body(soup, "mpark")
        assert "Hello world" in md

    def test_inline_code(self):
        soup = parse_html("<p>Use <code>std::vector</code> here.</p>")
        md = render_body(soup, "mpark")
        assert "`std::vector`" in md


class TestCodeBlock:
    def test_fenced(self):
        soup = parse_html('<pre class="sourceCode cpp"><code>int x = 1;</code></pre>')
        md = render_body(soup, "mpark")
        assert "```cpp" in md
        assert "int x = 1;" in md

    def test_language_from_class(self):
        soup = parse_html(
            '<div class="sourceCode"><pre class="sourceCode python">'
            '<code class="sourceCode python">print("hi")</code></pre></div>')
        md = render_body(soup, "mpark")
        assert "```python" in md

    def test_default_cpp_for_mpark(self):
        soup = parse_html("<pre><code>void f();</code></pre>")
        md = render_body(soup, "mpark")
        assert "```cpp" in md

    def test_no_default_for_unknown(self):
        soup = parse_html("<pre><code>void f();</code></pre>")
        md = render_body(soup, "unknown")
        assert "```\n" in md


class TestTable:
    def test_pipe_table(self):
        soup = parse_html("""
        <table>
          <tr><th>A</th><th>B</th></tr>
          <tr><td>1</td><td>2</td></tr>
        </table>
        """)
        md = render_body(soup, "mpark")
        assert "| A | B |" in md
        assert "| --- | --- |" in md
        assert "| 1 | 2 |" in md

    def test_pipe_escaped(self):
        soup = parse_html("""
        <table><tr><td>a|b</td><td>c</td></tr></table>
        """)
        md = render_body(soup, "mpark")
        assert r"a\|b" in md


class TestList:
    def test_unordered(self):
        soup = parse_html("<ul><li>One</li><li>Two</li></ul>")
        md = render_body(soup, "mpark")
        assert "- One" in md
        assert "- Two" in md

    def test_ordered(self):
        soup = parse_html("<ol><li>First</li><li>Second</li></ol>")
        md = render_body(soup, "mpark")
        assert "1. First" in md
        assert "2. Second" in md

    def test_nested(self):
        soup = parse_html("""
        <ul>
          <li>Parent
            <ul><li>Child</li></ul>
          </li>
        </ul>
        """)
        md = render_body(soup, "mpark")
        lines = md.strip().splitlines()
        parent_line = next(l for l in lines if "Parent" in l)
        assert "Child" not in parent_line
        assert "  - Child" in md
        assert md.count("Child") == 1, (
            f"Child appears {md.count('Child')} times, expected 1. md={md!r}")
        assert md.count("Parent") == 1

    def test_nested_three_levels(self):
        soup = parse_html("""
        <ul>
          <li>One
            <ul>
              <li>Two
                <ul><li>Three</li></ul>
              </li>
            </ul>
          </li>
        </ul>
        """)
        md = render_body(soup, "mpark")
        assert md.count("One") == 1
        assert md.count("Two") == 1
        assert md.count("Three") == 1
        assert "- One" in md
        assert "  - Two" in md
        assert "    - Three" in md

    def test_nested_ordered(self):
        soup = parse_html("""
        <ol>
          <li>First
            <ul><li>Bullet</li></ul>
          </li>
          <li>Second
            <ol><li>Sub</li></ol>
          </li>
        </ol>
        """)
        md = render_body(soup, "mpark")
        assert md.count("Bullet") == 1
        assert md.count("Sub") == 1
        assert "1. First" in md
        assert "  - Bullet" in md
        assert "2. Second" in md
        assert "  1. Sub" in md

    def test_nested_mixed_content(self):
        soup = parse_html("""
        <ul>
          <li>Before <strong>emphasis</strong>
            <ul><li>Nested</li></ul>
            after text
          </li>
        </ul>
        """)
        md = render_body(soup, "mpark")
        assert md.count("Nested") == 1
        assert "Before" in md
        assert "**emphasis**" in md
        assert md.count("after text") == 1

    def test_nested_multi_level(self):
        soup = parse_html("""
        <ul>
          <li>A
            <ul>
              <li>B
                <ol><li>C</li></ol>
              </li>
            </ul>
          </li>
        </ul>
        """)
        md = render_body(soup, "mpark")
        lines = md.strip().splitlines()
        a_line = next(l for l in lines if "A" in l and l.strip().startswith("-"))
        assert "B" not in a_line
        assert "C" not in a_line
        b_line = next(l for l in lines if "B" in l)
        assert "C" not in b_line


class TestWording:
    def test_wording_add_fence(self):
        soup = parse_html('<div class="wording-add"><p>New text</p></div>')
        md = render_body(soup, "mpark")
        assert ":::wording-add" in md
        assert ":::" in md.split(":::wording-add")[1]

    def test_wording_remove_fence(self):
        soup = parse_html('<div class="wording-remove"><p>Old text</p></div>')
        md = render_body(soup, "mpark")
        assert ":::wording-remove" in md

    def test_wording_mixed_fence(self):
        soup = parse_html('<div class="wording"><p>Spec text</p></div>')
        md = render_body(soup, "mpark")
        assert ":::wording\n" in md

    def test_ins_del_passthrough(self):
        soup = parse_html("<p><ins>added</ins> and <del>removed</del></p>")
        md = render_body(soup, "mpark")
        assert "<ins>added</ins>" in md
        assert "<del>removed</del>" in md


class TestBlockquote:
    def test_blockquote(self):
        soup = parse_html("<blockquote><p>Quoted text</p></blockquote>")
        md = render_body(soup, "mpark")
        assert "> Quoted text" in md


class TestInlineFormatting:
    def test_bold(self):
        soup = parse_html("<p><strong>bold</strong></p>")
        md = render_body(soup, "mpark")
        assert "**bold**" in md

    def test_italic(self):
        soup = parse_html("<p><em>italic</em></p>")
        md = render_body(soup, "mpark")
        assert "*italic*" in md

    def test_link(self):
        soup = parse_html('<p><a href="https://example.com">link</a></p>')
        md = render_body(soup, "mpark")
        assert "[link](https://example.com)" in md

    def test_anchor_link_plain(self):
        soup = parse_html('<p><a href="#section">section</a></p>')
        md = render_body(soup, "mpark")
        assert "section" in md
        assert "[" not in md

    def test_sub_sup_passthrough(self):
        soup = parse_html("<p>x<sub>2</sub> + y<sup>3</sup></p>")
        md = render_body(soup, "mpark")
        assert "<sub>2</sub>" in md
        assert "<sup>3</sup>" in md


class TestCollapseWhitespace:
    def test_collapses_spaces(self):
        md = render_body(parse_html("<p>hello   world</p>"), "mpark")
        assert "hello world" in md

    def test_strips_format_chars(self):
        md = render_body(parse_html("<p>hello\u200bworld</p>"), "mpark")
        assert "helloworld" in md

    def test_strips_and_trims(self):
        md = render_body(parse_html("<p>  hi  </p>"), "mpark")
        assert md.strip() == "hi"


class TestDocumentShell:
    def test_fragment_without_body(self):
        md = render_body(parse_html("<p>Frag</p>"), "mpark")
        assert "Frag" in md

    def test_full_document_with_body(self):
        html = "<html><head></head><body><p>In body</p></body></html>"
        md = render_body(parse_html(html), "mpark")
        assert "In body" in md


class TestStructuralTags:
    def test_hr(self):
        md = render_body(parse_html("<body><hr/><p>a</p></body>"), "mpark")
        assert "---" in md
        assert "a" in md

    def test_section_flattens(self):
        md = render_body(parse_html("<section><p>in</p></section>"), "mpark")
        assert "in" in md

    def test_main_article(self):
        md = render_body(parse_html("<main><p>m</p></main><article><p>a</p></article>"), "mpark")
        assert "m" in md and "a" in md


class TestHeadingEdgeCases:
    def test_secno_stripped_self_link_skipped(self):
        html = """<h2><span class="secno">3</span>Sec
        <a class="self-link" href="#x">#</a></h2>"""
        md = render_body(parse_html(html), "mpark")
        assert "## Sec" in md
        assert "self-link" not in md

    def test_heading_number_only_span_stripped(self):
        soup = parse_html('<h1><span class="header-section-number">1</span></h1>')
        md = render_body(soup, "mpark")
        assert md.strip() == "" or "# 1" not in md

    def test_inline_code_preserved_in_heading(self):
        soup = parse_html("<h2>The <code>foo_bar</code> section</h2>")
        md = render_body(soup, "mpark")
        assert "## The `foo_bar` section" in md

    def test_inline_code_preserved_with_skipped_number_span(self):
        soup = parse_html(
            '<h2><span class="header-section-number">3</span> '
            "<code>foo</code> bar</h2>"
        )
        md = render_body(soup, "mpark")
        assert "## `foo` bar" in md

    def test_link_preserved_in_heading(self):
        soup = parse_html(
            '<h2>See <a href="https://example.com/x">X</a> now</h2>'
        )
        md = render_body(soup, "mpark")
        assert "## See [X](https://example.com/x) now" in md


class TestCodeBlockExtended:
    def test_pre_without_code(self):
        md = render_body(parse_html("<pre>plain\nlines</pre>"), "mpark")
        assert "```" in md
        assert "plain" in md

    def test_language_hyphen_class(self):
        md = render_body(
            parse_html('<pre><code class="language-rust">let x;</code></pre>'),
            "mpark",
        )
        assert "```rust" in md

    def test_source_code_python_camel_class(self):
        md = render_body(
            parse_html('<pre><code class="sourceCodePython">x=1</code></pre>'),
            "mpark",
        )
        assert "```python" in md

    def test_source_code_on_parent_pre(self):
        md = render_body(
            parse_html(
                '<pre class="sourceCode cpp"><code>int y;</code></pre>'
            ),
            "mpark",
        )
        assert "```cpp" in md

    def test_bikeshed_no_default_lang_without_class(self):
        md = render_body(parse_html("<pre><code>x</code></pre>"), "bikeshed")
        assert md.startswith("```\n") or "\n```\n" in md
        assert "```cpp" not in md


class TestDivDispatch:
    def test_div_source_code_wraps_pre(self):
        html = (
            '<div class="sourceCode"><pre><code class="sourceCode cpp">z();'
            "</code></pre></div>"
        )
        md = render_body(parse_html(html), "mpark")
        assert "```cpp" in md

    def test_div_note_blockquote_style(self):
        md = render_body(
            parse_html('<div class="note"><p>Line one</p><p>Two</p></div>'),
            "mpark",
        )
        assert md.strip().startswith(">")
        assert "Line one" in md

    def test_div_example(self):
        md = render_body(parse_html('<div class="example"><p>ex</p></div>'), "mpark")
        assert "> ex" in md.replace("\n", " ") or "> ex" in md

    def test_plain_div_transparent(self):
        md = render_body(parse_html("<div><p>inner</p></div>"), "mpark")
        assert "inner" in md


class TestTableExtended:
    def test_nested_table_becomes_pipe(self):
        html = """
        <table>
        <tr><th>OuterA</th><th>OuterB</th></tr>
        <tr><td>1</td><td><table><tr><td>Inner</td></tr></table></td></tr>
        </table>
        """
        md = render_body(parse_html(html), "mpark")
        assert "<table>" not in md
        assert "OuterA" in md
        assert "Inner" in md

    def test_short_row_padding(self):
        html = """
        <table>
        <tr><th>A</th><th>B</th><th>C</th></tr>
        <tr><td>1</td><td>2</td></tr>
        </table>
        """
        md = render_body(parse_html(html), "mpark")
        assert "| 1 | 2 |" in md or "| 1 | 2 | |" in md


class TestDenormalizedTable:
    """Tables with rowspan/colspan are denormalized into flat pipe tables."""

    def test_rowspan_expanded(self):
        html = """
        <table>
        <tr><th>Day</th><th>Time</th></tr>
        <tr><td rowspan="2">Mon-Tue</td><td>09:00</td></tr>
        <tr><td>10:00</td></tr>
        </table>
        """
        md = render_body(parse_html(html), "mpark")
        assert "<table>" not in md
        assert "| Day | Time |" in md
        assert "| Mon-Tue | 09:00 |" in md
        assert "| Mon-Tue | 10:00 |" in md

    def test_colspan_expanded(self):
        html = """
        <table>
        <tr><th colspan="2">Header</th></tr>
        <tr><td>A</td><td>B</td></tr>
        </table>
        """
        md = render_body(parse_html(html), "mpark")
        assert "<table>" not in md
        assert "| Header | Header |" in md
        assert "| A | B |" in md

    def test_mixed_rowspan_colspan(self):
        html = """
        <table>
        <tr><th>X</th><th>Y</th><th>Z</th></tr>
        <tr><td rowspan="2">A</td><td colspan="2">B</td></tr>
        <tr><td>C</td><td>D</td></tr>
        </table>
        """
        md = render_body(parse_html(html), "mpark")
        assert "<table>" not in md
        lines = [l for l in md.splitlines() if l.startswith("|")]
        assert len(lines) == 4  # header + separator + 2 data rows
        assert "| A | B | B |" in md
        assert "| A | C | D |" in md

    def test_schedule_table_n5034(self):
        """Real-world schedule from N5034 with rowspan=5 and colspan=2."""
        html = """
        <table>
        <tr><th>Day</th><th>Start</th><th>Break</th><th>End</th></tr>
        <tr><td>Monday</td><td>9:00 AM</td><td rowspan="3">10:15</td><td rowspan="3">5:30 PM</td></tr>
        <tr><td>Tuesday</td><td rowspan="2">8:30 AM</td></tr>
        <tr><td>Wednesday</td></tr>
        <tr><td>Saturday</td><td>8:30 AM</td><td colspan="2">No breaks</td></tr>
        </table>
        """
        md = render_body(parse_html(html), "mpark")
        assert "<table>" not in md
        assert "| Monday | 9:00 AM | 10:15 | 5:30 PM |" in md
        assert "| Tuesday | 8:30 AM | 10:15 | 5:30 PM |" in md
        assert "| Wednesday | 8:30 AM | 10:15 | 5:30 PM |" in md
        assert "| Saturday | 8:30 AM | No breaks | No breaks |" in md

    def test_simple_table_no_spans_still_pipe(self):
        """Tables without spans should still render as pipe tables."""
        html = """
        <table>
        <tr><th>A</th><th>B</th></tr>
        <tr><td>1</td><td>2</td></tr>
        </table>
        """
        md = render_body(parse_html(html), "mpark")
        assert "<table>" not in md
        assert "| A | B |" in md
        assert "| 1 | 2 |" in md

    def test_br_in_cell_becomes_space(self):
        """Cells with <br> should collapse to single-line pipe table cells."""
        html = """
        <table>
        <tr><th>Col</th></tr>
        <tr><td>Line1<br>Line2</td></tr>
        </table>
        """
        md = render_body(parse_html(html), "mpark")
        assert "<table>" not in md
        assert "Line1 Line2" in md

    def test_pipe_in_cell_escaped(self):
        """Pipe characters in cell content must be escaped."""
        html = """
        <table>
        <tr><th>Op</th></tr>
        <tr><td rowspan="2">a|b</td></tr>
        <tr></tr>
        </table>
        """
        md = render_body(parse_html(html), "mpark")
        assert r"a\|b" in md

    def test_nested_table_becomes_pipe(self):
        """Nested tables are reconstructed as pipe tables via flat path."""
        html = """
        <table>
        <tr><td><table><tr><td>Inner</td></tr></table></td></tr>
        </table>
        """
        md = render_body(parse_html(html), "mpark")
        assert "<table>" not in md
        assert "Inner" in md

    def test_block_content_in_cell_becomes_pipe(self):
        """Tables with block content (lists) in cells become pipe tables."""
        html = """
        <table>
        <tr><td rowspan="2">X</td><td><ul><li>Item</li></ul></td></tr>
        <tr><td>Y</td></tr>
        </table>
        """
        md = render_body(parse_html(html), "mpark")
        assert "<table>" not in md
        assert "X" in md

    def test_bikeshed_unclosed_tags_become_pipe(self):
        """Bikeshed-style tables with unclosed <td>/<th> become pipe tables."""
        html = """
        <table>
        <tr><th>Poll<th>SF<th>WF<th>Outcome
        <tr><td>Poll 1<td>11<td>4<td>Consensus
        <tr><td>Poll 2<td>5<td>3<td>No consensus
        </table>
        """
        md = render_body(parse_html(html), "mpark")
        assert "<table>" not in md
        assert "Poll" in md
        assert "SF" in md
        assert "Consensus" in md
        pipe_lines = [l for l in md.splitlines() if l.strip().startswith("|")]
        assert len(pipe_lines) >= 4  # header + sep + 2 data rows


class TestLossyTableMarker:
    """Lossy table rendering paths emit <!-- tomd:lossy-table --> markers."""

    def test_rowspan_emits_marker(self):
        html = """
        <table>
        <tr><th>Day</th><th>Time</th></tr>
        <tr><td rowspan="2">Mon</td><td>09:00</td></tr>
        <tr><td>10:00</td></tr>
        </table>
        """
        md = render_body(parse_html(html), "mpark")
        assert "<!-- tomd:lossy-table -->" in md

    def test_colspan_emits_marker(self):
        html = """
        <table>
        <tr><th colspan="2">Header</th></tr>
        <tr><td>A</td><td>B</td></tr>
        </table>
        """
        md = render_body(parse_html(html), "mpark")
        assert "<!-- tomd:lossy-table -->" in md

    def test_simple_table_no_marker(self):
        html = """
        <table>
        <tr><th>A</th><th>B</th></tr>
        <tr><td>1</td><td>2</td></tr>
        </table>
        """
        md = render_body(parse_html(html), "mpark")
        assert "<!-- tomd:lossy-table -->" not in md

    def test_nested_table_emits_marker(self):
        html = """
        <table>
        <tr><td><table><tr><td>Inner</td></tr></table></td></tr>
        </table>
        """
        md = render_body(parse_html(html), "mpark")
        assert "<!-- tomd:lossy-table -->" in md

    def test_code_table_emits_marker(self):
        html = """
        <table>
        <tr><td><pre><code>int x = 1;</code></pre></td></tr>
        </table>
        """
        md = render_body(parse_html(html), "mpark")
        assert "<!-- tomd:lossy-table -->" in md

    def test_multiple_lossy_tables_multiple_markers(self):
        html = """
        <table>
        <tr><th colspan="2">T1</th></tr>
        <tr><td>A</td><td>B</td></tr>
        </table>
        <table>
        <tr><th>X</th></tr>
        <tr><td rowspan="2">Y</td></tr>
        <tr></tr>
        </table>
        """
        md = render_body(parse_html(html), "mpark")
        assert md.count("<!-- tomd:lossy-table -->") == 2


class TestDefinitionList:
    def test_dl_dt_dd(self):
        html = "<dl><dt>Term</dt><dd>Def</dd></dl>"
        md = render_body(parse_html(html), "mpark")
        assert "**Term**" in md
        assert ": Def" in md


class TestLinksExtended:
    def test_mailto_link(self):
        md = render_body(
            parse_html('<p><a href="mailto:a@b.co">Mail me</a></p>'),
            "mpark",
        )
        assert "[Mail me](mailto:a@b.co)" in md

    def test_disallowed_scheme_plain_text(self):
        md = render_body(
            parse_html('<p><a href="ftp://x.com">ftp</a></p>'),
            "mpark",
        )
        assert "ftp" in md
        assert "](" not in md

    def test_anchor_no_href_text_only(self):
        md = render_body(parse_html("<p><a>nohref</a></p>"), "mpark")
        assert "nohref" in md


class TestBlockquoteExtended:
    def test_nested_paragraphs(self):
        md = render_body(
            parse_html("<blockquote><p>First</p><p>Second</p></blockquote>"),
            "mpark",
        )
        assert "> First" in md
        assert "Second" in md

    def test_empty_blockquote_omitted(self):
        md = render_body(parse_html("<blockquote></blockquote><p>x</p>"), "mpark")
        assert md.strip() == "x"


class TestListExtended:
    def test_ol_with_nested_ul(self):
        html = "<ol><li>Outer<ul><li>Inner</li></ul></li></ol>"
        md = render_body(parse_html(html), "mpark")
        assert "1. Outer" in md
        assert "  - Inner" in md


class TestTransparentInline:
    def test_mark_kbd_passthrough(self):
        md = render_body(
            parse_html("<p><mark>m</mark> <kbd>k</kbd></p>"),
            "mpark",
        )
        assert "m" in md and "k" in md


class TestSchultkeCustomElements:
    """Tests for Jan Schultke's custom HTML generator elements."""

    def test_code_block_fenced(self):
        html = '<code-block><h- data-h="kw">const</h-> <h- data-h="kw_type">int</h-> x = 42;</code-block>'
        md = render_body(parse_html(html), "schultke")
        assert "```cpp" in md
        assert "const int x = 42;" in md
        assert "```" in md.split("```cpp")[1]

    def test_code_block_multiline(self):
        html = (
            "<code-block>"
            '<h- data-h="kw">void</h-> <h- data-h="id">foo</h->() {\n'
            '  <h- data-h="kw">return</h->;\n'
            "}"
            "</code-block>"
        )
        md = render_body(parse_html(html), "schultke")
        assert "```cpp" in md
        assert "void foo()" in md
        assert "return" in md

    def test_code_block_works_with_any_generator(self):
        html = "<code-block>int x;</code-block>"
        md = render_body(parse_html(html), "unknown")
        assert "```cpp" in md
        assert "int x;" in md

    def test_example_block_becomes_blockquote(self):
        html = "<example-block><p>Example text</p></example-block>"
        md = render_body(parse_html(html), "schultke")
        assert "> Example text" in md

    def test_note_block_becomes_blockquote(self):
        html = "<note-block><p>Note content</p></note-block>"
        md = render_body(parse_html(html), "schultke")
        assert "> Note content" in md

    def test_bug_block_becomes_blockquote(self):
        html = "<bug-block><p>Bug report</p></bug-block>"
        md = render_body(parse_html(html), "schultke")
        assert "> Bug report" in md

    def test_tt_becomes_inline_code(self):
        html = "<p>Use <tt->std::vector</tt-> here</p>"
        md = render_body(parse_html(html), "schultke")
        assert "`std::vector`" in md

    def test_h_inline_passthrough(self):
        html = '<p>The <h- data-h="kw">const</h-> keyword</p>'
        md = render_body(parse_html(html), "schultke")
        assert "const" in md

    def test_f_serif_passthrough(self):
        html = "<p><f-serif>Some text</f-serif></p>"
        md = render_body(parse_html(html), "schultke")
        assert "Some text" in md


class TestDascandyFietsCodeBlock:
    """Tests for dascandy/fiets generator div.code pattern."""

    def test_div_code_fenced(self):
        html = '<div class="code">int main() { return 0; }</div>'
        md = render_body(parse_html(html), "dascandy/fiets")
        assert "```cpp" in md
        assert "int main()" in md

    def test_div_code_with_spans(self):
        html = (
            '<div class="code">'
            '<span class="keyword">const</span> '
            '<span class="special">&amp;</span>x'
            "</div>"
        )
        md = render_body(parse_html(html), "dascandy/fiets")
        assert "```cpp" in md
        assert "const" in md

    def test_code_wrapping_div_code(self):
        """dascandy/fiets uses <code><div class='code'>...</div></code>."""
        html = '<code><div class="code">void f() {}</div></code>'
        md = render_body(parse_html(html), "dascandy/fiets")
        assert "```cpp" in md
        assert "void f()" in md


class TestCodeTable:
    """Tables containing <pre> blocks should extract code, not build pipe tables."""

    def test_table_with_pre_extracts_code_blocks(self):
        html = (
            "<table><tr>"
            '<td><pre class="highlight">int x = 1;</pre></td>'
            '<td><pre class="highlight">mov eax, 1</pre></td>'
            "</tr></table>"
        )
        md = render_body(parse_html(html), "bikeshed")
        assert "```cpp" in md
        assert "int x = 1;" in md
        assert "mov eax, 1" in md
        assert "|" not in md

    def test_code_table_preserves_all_pre_blocks(self):
        """Every <pre> in a table is emitted, even if content is identical."""
        html = (
            "<table><tr>"
            '<td><pre class="highlight">void f();</pre></td>'
            '<td><pre class="highlight">void f();</pre></td>'
            "</tr></table>"
        )
        md = render_body(parse_html(html), "bikeshed")
        assert md.count("void f();") == 2

    def test_table_without_pre_stays_pipe(self):
        html = "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
        md = render_body(parse_html(html), "bikeshed")
        assert "| A | B |" in md


class TestBikeshedInlineElements:
    """Bikeshed <c-> syntax highlight spans should pass through inline."""

    def test_c_dash_inside_code(self):
        html = '<p><code class="highlight"><c->std</c-><c->::</c-><c->vector</c-></code></p>'
        md = render_body(parse_html(html), "bikeshed")
        assert "`std::vector`" in md


class TestHtmlComments:
    def test_comment_content_not_rendered(self):
        """HTML comments must never appear in Markdown output."""
        html = "<p>Visible text.</p><!-- This comment should be invisible -->"
        md = render_body(parse_html(html), "mpark")
        assert "Visible text." in md
        assert "comment" not in md
        assert "invisible" not in md

    def test_comment_with_html_tags_not_rendered(self):
        """Commented-out HTML blocks (e.g. draft sections) must not leak into output."""
        html = (
            "<p>Before.</p>"
            "<!-- <h2>Draft Section</h2><p>Draft content</p> -->"
            "<p>After.</p>"
        )
        md = render_body(parse_html(html), "mpark")
        assert "Before." in md
        assert "After." in md
        assert "Draft Section" not in md
        assert "Draft content" not in md

    def test_comment_with_entities_not_rendered(self):
        """Entities inside comments (e.g. &lt; in commented-out code) must not appear."""
        html = (
            "<p>Intro.</p>"
            "<!-- <pre>template &lt;class T&gt; void f();</pre> -->"
            "<p>Body.</p>"
        )
        md = render_body(parse_html(html), "mpark")
        assert "Intro." in md
        assert "Body." in md
        assert "&lt;" not in md
        assert "&gt;" not in md
        assert "template" not in md

    def test_comment_between_inline_elements_not_rendered(self):
        """Comments inline between spans must not insert text into the output."""
        html = "<p>Hello<!-- drop this --> world</p>"
        md = render_body(parse_html(html), "mpark")
        assert "Hello world" in md
        assert "drop this" not in md

    def test_comment_in_heading_not_rendered(self):
        """Comments inside headings are stripped."""
        html = "<h2>Real Title<!-- draft annotation --></h2>"
        md = render_body(parse_html(html), "mpark")
        assert "## Real Title" in md
        assert "draft annotation" not in md


class TestListCodeExtraction:
    """<pre> and <code-block> inside <li> should be fenced, not flattened."""

    def test_li_with_pre_emits_fenced_code(self):
        html = "<ul><li>Description<pre>int x = 42;</pre></li></ul>"
        md = render_body(parse_html(html), "bikeshed")
        assert "- Description" in md
        assert "```" in md
        assert "int x = 42;" in md

    def test_li_with_code_block_emits_fenced_code(self):
        html = "<ul><li>Example<code-block>void f();</code-block></li></ul>"
        md = render_body(parse_html(html), "schultke")
        assert "- Example" in md
        assert "```cpp" in md
        assert "void f();" in md

    def test_li_without_code_stays_inline(self):
        html = "<ul><li>Plain text only</li></ul>"
        md = render_body(parse_html(html), "mpark")
        assert "- Plain text only" in md
        assert "```" not in md


class TestDlCodeExtraction:
    """<pre> and <code-block> inside <dd> should be fenced, not flattened."""

    def test_dd_with_pre_emits_fenced_code(self):
        html = "<dl><dt>Term</dt><dd>Def<pre>int x = 1;</pre></dd></dl>"
        md = render_body(parse_html(html), "bikeshed")
        assert "**Term**" in md
        assert ": Def" in md
        assert "```" in md
        assert "int x = 1;" in md

    def test_dd_with_code_block_emits_fenced_code(self):
        html = "<dl><dt>API</dt><dd>Usage:<code-block>f();</code-block></dd></dl>"
        md = render_body(parse_html(html), "schultke")
        assert "**API**" in md
        assert ": Usage:" in md
        assert "```cpp" in md
        assert "f();" in md

    def test_dd_without_code_stays_inline(self):
        html = "<dl><dt>Key</dt><dd>Value</dd></dl>"
        md = render_body(parse_html(html), "mpark")
        assert "**Key**" in md
        assert ": Value" in md
        assert "```" not in md


class TestCodeTableWithCodeBlock:
    """Tables with <code-block> should extract code like <pre> tables."""

    def test_table_with_code_block_extracts(self):
        html = (
            "<table><tr>"
            "<td><code-block>int x = 1;</code-block></td>"
            "<td><code-block>int y = 2;</code-block></td>"
            "</tr></table>"
        )
        md = render_body(parse_html(html), "schultke")
        assert "```cpp" in md
        assert "int x = 1;" in md
        assert "int y = 2;" in md
        assert "|" not in md


class TestCodeParagraphDetection:
    """<p> with only <span class="code"> children -> fenced code block."""

    def test_span_code_only_paragraph_becomes_fenced(self):
        html = (
            '<p><span class="code">'
            '<span class="keyword">constexpr</span> '
            'basic_cstring_view() noexcept;'
            '</span></p>'
        )
        md = render_body(parse_html(html), "dascandy/fiets")
        assert "```cpp" in md
        assert "constexpr basic_cstring_view() noexcept;" in md

    def test_mixed_content_paragraph_stays_prose(self):
        html = '<p>Use <span class="code">std::vector</span> here.</p>'
        md = render_body(parse_html(html), "dascandy/fiets")
        assert "```" not in md
        assert "std::vector" in md

    def test_multiple_span_code_children(self):
        html = (
            '<p>'
            '<span class="code">template&lt;class T&gt;</span> '
            '<span class="code">auto foo() -&gt; T;</span>'
            '</p>'
        )
        md = render_body(parse_html(html), "dascandy/fiets")
        assert "```cpp" in md

    def test_code_element_not_matched(self):
        """<code> is inline formatting, not dascandy/fiets span.code."""
        html = '<p><code class="highlight">std::vector</code></p>'
        md = render_body(parse_html(html), "bikeshed")
        assert "```cpp" not in md

    def test_empty_paragraph_not_matched(self):
        html = "<p>   </p>"
        md = render_body(parse_html(html), "dascandy/fiets")
        assert md.strip() == "" or "```" not in md


class TestMisnestedBlockCascade:
    """Block elements nested multiple inline levels deep are promoted out."""

    def test_block_two_inline_levels_deep_promotes_to_sibling(self):
        # p <- span <- ol: the first repair promotes the ol into the span's
        # wrapper context; the cascade must re-queue the enclosing p so the
        # list reaches block level instead of flattening to prose.
        md = render_body(parse_html(
            "<p><span>intro text"
            "<ol><li>first item</li><li>second item</li></ol>"
            "trailing text</span></p>"
        ), "unknown")
        assert "intro text" in md
        assert "1. first item" in md
        assert "2. second item" in md
        assert "trailing text" in md


# Empty margin div: trips the eel.is gate without contributing content.
_MARGIN_GATE = "<div class='marginalizedparent'></div>"

# Minimal eel.is-style paragraph: number column + source link + sentence div.
_EELIS_PARA = (
    "<div class='para' id='general-1'>"
    "<div class='marginalizedparent'>"
    "<a class='marginalized' href='#general-1'>1</a></div>"
    "<div class='sourceLinkParent'>"
    "<a class='sourceLink' href='http://github.com/x/y#L6'>#</a></div>"
    "<div class='texpara'><div class='sentence'>"
    "This Clause describes components for memory management"
    "<a class='hidden_link' href='#general-1.sentence-1'>.</a>"
    "</div></div>"
    "</div>"
)


def _render_eelis(html: str) -> str:
    """Parse and render an eel.is-style HTML fragment."""
    return render_body(parse_html(html), "unknown")


class TestEelisWording:
    """eel.is-style standardese markup normalizes to coherent paragraphs."""

    def test_para_renders_as_one_numbered_paragraph(self):
        md = _render_eelis(_EELIS_PARA)
        assert (
            "1 This Clause describes components for memory management."
            in md
        )

    def test_source_link_glyph_suppressed(self):
        md = _render_eelis(_EELIS_PARA)
        assert "#" not in md

    def test_sentence_with_inline_span_does_not_fragment(self):
        md = _render_eelis(
            "<div class='para'><div class='marginalizedparent'>"
            "<a class='marginalized' href='#g-2'>2</a></div>"
            "<div class='texpara'><div class='sentence'>"
            "smart pointers, <span class='added'>pointer tagging, </span>"
            "memory resources, as summarized in Table <a href='#tab'>47</a>"
            "<a class='hidden_link' href='#s'>.</a>"
            "</div></div></div>"
        )
        assert (
            "2 smart pointers, pointer tagging, memory resources, "
            "as summarized in Table 47." in md
        )

    def test_texttt_becomes_code_span(self):
        md = _render_eelis(
            _MARGIN_GATE
            + "<p><span class='texttt'>"
            "<span class='anglebracket'>&lt;</span>memory"
            "<span class='anglebracket'>&gt;</span></span></p>"
        )
        assert "`<memory>`" in md

    def test_texttt_keeps_cooccurring_classes(self):
        # Only the matched class token is removed; other tokens stay on
        # the renamed element.
        md = _render_eelis(
            _MARGIN_GATE
            + "<p><span class='texttt special'>free</span></p>"
        )
        assert "`free`" in md

    def test_table_cell_link_glyph_suppressed_and_texttt_coded(self):
        md = _render_eelis(
            "<table><tr><td>"
            "<div class='marginalizedparent'>"
            "<a class='itemDeclLink' href='#r1'>\U0001f517</a></div>"
            "<div class='texpara'><div class='sentence'>"
            "<a href='#memory'>[memory]</a></div></div>"
            "</td><td><div class='texpara'><div class='sentence'>"
            "<span class='texttt'><span class='anglebracket'>&lt;</span>"
            "cstdlib<span class='anglebracket'>&gt;</span></span>"
            "</div></div></td></tr></table>"
        )
        assert "\U0001f517" not in md
        assert "| [memory] | `<cstdlib>` |" in md

    def test_numbered_table_caption_is_one_paragraph(self):
        md = _render_eelis(
            _MARGIN_GATE
            + "<div class='numberedTable' id='tab:x'>"
            "Table <a href='#tab:x'>47</a> &mdash; Summary"
            "&emsp;<a href='./tab:x'>[tab:x]</a><br>"
            "<table><tr><th>A</th></tr><tr><td>b</td></tr></table>"
            "</div>"
        )
        assert "Table 47 \u2014 Summary [tab:x]" in md
        assert "| A |" in md

    def test_table_inside_texpara_survives_as_pipe_table(self):
        # Regression: renaming texpara to <p> must not let _inline_text
        # flatten a block table that lives inside it.
        md = _render_eelis(
            _MARGIN_GATE
            + "<div class='texpara'>"
            "<table><tr><th>H</th></tr><tr><td>v</td></tr></table>"
            "</div>"
        )
        assert "| H |" in md
        assert "| v |" in md

    def test_gate_document_without_marginalizedparent_untouched(self):
        md = _render_eelis(
            "<p><span class='texttt'>&lt;memory&gt;</span></p>"
            "<div class='para'><div class='texpara'>"
            "<div class='sentence'>One</div><div class='sentence'>Two</div>"
            "</div></div>"
        )
        assert "`" not in md

    def test_codeblock_span_becomes_cpp_fenced_block(self):
        md = _render_eelis(
            _MARGIN_GATE
            + "<div class='texpara'><span><span class='codeblock'>"
            "<span class='keyword'>namespace</span> std {\n"
            "  <span class='keyword'>class</span> exception;\n"
            "}</span></span></div>"
        )
        assert "```cpp\nnamespace std {\n  class exception;\n}\n```" in md

    def test_texttt_inside_codeblock_not_collapsed_to_code_fragment(self):
        # The codeblock's content moves wholesale into one code.cpp child;
        # texttt spans inside it must not become competing <code> elements
        # or the synopsis shrinks to a single fragment.
        md = _render_eelis(
            _MARGIN_GATE
            + "<div class='texpara'><span class='codeblock'>"
            "using exception_ptr = <span class='texttt'>unspecified</span>;\n"
            "void rethrow_exception(exception_ptr p);</span></div>"
        )
        assert "using exception_ptr = unspecified;" in md
        assert "void rethrow_exception(exception_ptr p);" in md

    def test_block_list_inside_sentence_survives(self):
        # Cascade repair: <ol> sits two inline levels deep
        # (p <- span.sentence <- ol) after the renames and must still be
        # promoted out instead of being flattened to prose.
        md = _render_eelis(
            "<div class='para'><div class='marginalizedparent'>"
            "<a class='marginalized' href='#g-1'>1</a></div>"
            "<div class='texpara'><div class='sentence'>"
            "does not find any"
            "<ol><li>declaration of a class member, or</li>"
            "<li>function declaration</li></ol>"
            "then lookup proceeds"
            "</div></div></div>"
        )
        assert "1 does not find any" in md
        assert "1. declaration of a class member, or" in md
        assert "then lookup proceeds" in md

    def test_para_without_margin_child_gets_no_number_prefix(self):
        # A top-level margin (outside li / div.para) loses its number with
        # the chrome; no phantom number-only paragraph appears.
        md = _render_eelis(
            "<div class='marginalizedparent'>"
            "<a class='marginalized' href='#g-1'>1</a></div>"
            "<div class='para'><div class='texpara'>"
            "<div class='sentence'>Standalone text</div>"
            "</div></div>"
        )
        assert "Standalone text" in md
        assert "1 Standalone text" not in md
        assert not any(
            line.strip() == "1" for line in md.splitlines()
        )

    def test_list_item_sub_number_folds_inline(self):
        # Real eel.is shape (p4167r0): the li text is a bare sibling of
        # the margin div, not wrapped in a texpara.
        md = _render_eelis(
            "<div class='para'><div class='marginalizedparent'>"
            "<a class='marginalized' href='#g-1'>1</a></div>"
            "<div class='texpara'><div class='sentence'>does not find any"
            "<ul class='itemize'>"
            "<li id='1.1'><div class='marginalizedparent'>"
            "<a class='marginalized' href='#1.1'>(1.1)</a></div>"
            "declaration of a class member, or</li>"
            "</ul></div></div></div>"
        )
        assert "(1.1) declaration of a class member, or" in md

    def test_margin_does_not_number_texpara_inside_following_table(self):
        # The fold is sibling-scoped: a paragraph number must not land in
        # a texpara nested inside a table that precedes the body text.
        md = _render_eelis(
            "<div class='para'><div class='marginalizedparent'>"
            "<a class='marginalized'>3</a></div>"
            "<table><tr><td><div class='texpara'>"
            "<div class='sentence'>cell</div></div></td></tr></table>"
            "<div class='texpara'><div class='sentence'>Body text</div>"
            "</div></div>"
        )
        assert "3 Body text" in md
        assert "3 cell" not in md

    def test_double_margin_same_texpara_keeps_numbers_in_order(self):
        # Two numbered margins before one texpara (split sub-paragraph):
        # the texpara takes the first number once, the second falls back
        # inline instead of producing a reversed "2 1 text" prefix.
        md = _render_eelis(
            "<div class='para'>"
            "<div class='marginalizedparent'>"
            "<a class='marginalized'>1</a></div>"
            "<div class='marginalizedparent'>"
            "<a class='marginalized'>2</a></div>"
            "<div class='texpara'><div class='sentence'>text</div></div>"
            "</div>"
        )
        assert "1 text" in md
        assert "2 1 text" not in md
        assert "1 2 text" not in md

    def test_wording_headings_demote_two_levels(self):
        md = _render_eelis(
            "<div class='wording'>"
            + _MARGIN_GATE
            + "<h1>20 Memory management library <span>[mem]</span></h1>"
            "<h2>20.1 General <span>[mem.general]</span></h2>"
            "<h5>Deep heading</h5>"
            "<div class='texpara'><div class='sentence'>Body</div></div>"
            "</div>"
        )
        assert "### 20 Memory management library [mem]" in md
        assert "#### 20.1 General [mem.general]" in md
        assert "###### Deep heading" in md
        assert "####### " not in md

    def test_heading_outside_wording_div_not_demoted(self):
        md = _render_eelis(
            _MARGIN_GATE
            + "<h2>Proposed changes to wording</h2>"
            "<div class='wording'><h1>Clause</h1></div>"
        )
        assert "## Proposed changes to wording" in md
        assert "### Clause" in md

    def test_nested_hana_wording_heading_demotes_once(self):
        # div.hana_wording inside div.wording: the heading matches both
        # containers but must demote only once (h2 -> h4, not h6).
        md = _render_eelis(
            "<div class='wording'>"
            + _MARGIN_GATE
            + "<div class='hana_wording'><h2>Sub clause</h2></div>"
            "</div>"
        )
        assert "#### Sub clause" in md

    def test_chrome_div_rescues_non_anchor_content(self):
        # Fidelity guard: a texpara trapped inside a chrome div is rescued
        # to a following sibling before the chrome drops, where the number
        # fold picks it up as a regular target.
        md = _render_eelis(
            "<div class='para'>"
            "<div class='marginalizedparent'>"
            "<a class='marginalized'>1</a>"
            "<div class='texpara'><div class='sentence'>Trapped</div></div>"
            "</div>"
            "</div>"
        )
        assert "1 Trapped" in md

    def test_chrome_div_rescues_bare_text(self):
        # The rescue covers text nodes, not only elements: stray prose
        # inside a chrome div must not vanish with the chrome.
        md = _render_eelis(
            "<div class='para'><div class='marginalizedparent'>"
            "stray note <a class='marginalized'>1</a></div>"
            "<div class='texpara'><div class='sentence'>Body</div></div>"
            "</div>"
        )
        assert "stray note" in md
        assert "1 Body" in md

    def test_number_anchors_after_leading_block_in_texpara(self):
        # A texpara that opens with a table: the number must attach to the
        # prose sentence, not strand in its own wrapper before the table.
        md = _render_eelis(
            "<div class='para'><div class='marginalizedparent'>"
            "<a class='marginalized'>3</a></div>"
            "<div class='texpara'>"
            "<table><tr><th>H</th></tr><tr><td>v</td></tr></table>"
            "<div class='sentence'>Body text</div></div></div>"
        )
        assert "3 Body text" in md
        assert not any(line.strip() == "3" for line in md.splitlines())

    def test_codeblock_br_becomes_newline(self):
        # _render_pre reads the fence content via get_text(), which drops
        # <br>; the rename must materialize them as newlines first.
        md = _render_eelis(
            _MARGIN_GATE
            + "<div class='texpara'><span class='codeblock'>"
            "int a;<br>int b;</span></div>"
        )
        assert "int a;\nint b;" in md

    def test_empty_codeblock_span_emits_no_fence(self):
        md = _render_eelis(
            _MARGIN_GATE
            + "<div class='texpara'><span class='codeblock'></span>"
            "<div class='sentence'>After</div></div>"
        )
        assert "```" not in md
        assert "After" in md

    def test_texttt_wrapping_codeblock_keeps_fence(self):
        # The texttt rename must not produce a <code> ancestor around a
        # renamed pre, or the fence flattens into nested backticks.
        md = _render_eelis(
            _MARGIN_GATE
            + "<p><span class='texttt'>prefix <span class='codeblock'>"
            "int a;\nint b;</span></span></p>"
        )
        assert "```cpp\nint a;\nint b;\n```" in md

    def test_trailing_margin_number_keeps_separating_space(self):
        # eel.is places margins first, but a margin after its content must
        # not glue the number to the last word.
        md = _render_eelis(
            _MARGIN_GATE
            + "<ul><li>declaration of a class member"
            "<div class='marginalizedparent'>"
            "<a class='marginalized'>(1.1)</a></div></li></ul>"
        )
        assert "declaration of a class member (1.1)" in md
