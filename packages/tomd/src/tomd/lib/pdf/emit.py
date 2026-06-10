"""Markdown and companion prompts file generation."""

import html as _html
import logging
import re

from ..metadata_yaml.format import format_front_matter
from .. import dedup_paragraphs, strip_redundant_body_meta, strip_orphan_toc_list, strip_leading_h1, DEFAULT_FENCE_LANG
from ..shared import _find_front_matter_end
from .cleanup import normalize_whitespace
from .glyphs import (
    GLYPH_PLACEHOLDER_MARKER_TEMPLATE,
    UNKNOWN_GLYPH,
    GlyphPassStats,
)
from .images import TRUNCATION_MARKER_TEMPLATE, VectorUncertaintyStats
from .types import Line, Span, Section, SectionKind, BULLET_CHARS, FigureGraph
from .vector_images import format_uncertainty_marker, should_emit_marker

_log = logging.getLogger(__name__)


def _render_span(span: Span, skip_bold: bool = False) -> str:
    """Render a single non-monospace span with inline Markdown formatting.

    Monospace spans are handled by _render_line_spans via group merging.
    """
    text = span.text
    if not text.strip():
        return text

    stripped = text.strip()
    leading = text[:len(text) - len(text.lstrip())]
    trailing = text[len(text.rstrip()):]

    bold = span.bold and not skip_bold

    if span.link_url:
        inner = f"[{stripped}]({span.link_url})"
        if bold and span.italic:
            inner = f"***{inner}***"
        elif bold:
            inner = f"**{inner}**"
        elif span.italic:
            inner = f"*{inner}*"
        return f"{leading}{inner}{trailing}"

    if bold and span.italic:
        return f"{leading}***{stripped}***{trailing}"
    if bold:
        return f"{leading}**{stripped}**{trailing}"
    if span.italic:
        return f"{leading}*{stripped}*{trailing}"

    return text


def _render_line_spans(line: Line, in_code_section: bool = False,
                       suppress_bold: bool = False) -> str:
    """Render all spans in a line with inline formatting applied.

    Merges consecutive monospace spans into a single backtick pair
    to avoid fragmented output like `std``::``:stop_token`.
    """
    if in_code_section:
        return "".join(s.text for s in line.spans)

    groups: list[tuple[bool, list[Span]]] = []
    for span in line.spans:
        is_mono = span.monospace and span.text.strip()
        if groups and groups[-1][0] == is_mono:
            groups[-1][1].append(span)
        else:
            groups.append((is_mono, [span]))

    parts = []
    for is_mono, spans in groups:
        if is_mono:
            merged_text = "".join(s.text for s in spans)
            stripped = merged_text.strip()
            if stripped:
                leading = merged_text[:len(merged_text) - len(merged_text.lstrip())]
                trailing = merged_text[len(merged_text.rstrip()):]
                parts.append(f"{leading}`{stripped}`{trailing}")
            else:
                parts.append(merged_text)
        else:
            for span in spans:
                if suppress_bold:
                    parts.append(_render_span(span, skip_bold=True))
                else:
                    parts.append(_render_span(span))
    return "".join(parts)


_EMDASH_BULLET_RE = re.compile(r"^[\u2013\u2014]\s")


def _render_paragraph_spans(sec: Section) -> str:
    """Render a paragraph section using span-level formatting, then unwrap.

    Preserves line breaks when every non-empty line starts with an
    em-dash or en-dash bullet marker so that bullet lists extracted
    as PARAGRAPH sections render as separate items instead of being
    collapsed into a single prose line.
    """
    if not sec.lines:
        return " ".join(ln.strip() for ln in sec.text.split("\n") if ln.strip())
    rendered_lines = []
    for line in sec.lines:
        rendered_lines.append(_render_line_spans(line))
    text = "\n".join(rendered_lines)
    text = normalize_whitespace(text)
    lines = text.split("\n")
    non_empty = [ln.strip() for ln in lines if ln.strip()]
    if non_empty and all(_EMDASH_BULLET_RE.match(ln) for ln in non_empty):
        # Rewrite em/en-dash bullets as markdown list items so that
        # renderers display them as a proper list instead of joining
        # consecutive lines into a single paragraph.
        # indent_level > 0 indicates a nested sub-list (set by
        # _assign_emdash_nesting in the emit pre-pass).
        prefix = "  " * sec.indent_level
        return "\n".join(
            prefix + _EMDASH_BULLET_RE.sub("- ", ln, count=1)
            for ln in non_empty
        )
    return " ".join(ln.strip() for ln in lines if ln.strip())


_BARE_HEADING_NUM_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*|[A-Z]+)\.?\s*$"
)

_CONTINUATION_WORDS = frozenset({
    "a", "an", "the", "of", "for", "in", "to", "with",
    "from", "and", "or", "by", "at", "on",
})


def _heading_line_continues(text: str) -> bool:
    """True when heading text is clearly cut mid-phrase by line wrapping.

    Signals: trailing comma/semicolon, unbalanced open parenthesis, or
    ending with an article/preposition/conjunction.
    """
    stripped = text.rstrip()
    if not stripped:
        return False
    if stripped[-1] in (",", ";"):
        return True
    if stripped.count("(") > stripped.count(")"):
        return True
    words = stripped.split()
    if words:
        last = words[-1].lower().rstrip(".,;:")
        if last in _CONTINUATION_WORDS:
            return True
    return False


def _render_heading_spans(sec: Section) -> str:
    """Render a heading using span-level formatting for the first line.

    Bold is suppressed because the ATX prefix already conveys heading weight.
    When line 0 is a bare section number (e.g. "1", "3.2", "I."), subsequent
    lines at the same font size are joined to recover the title text that
    MuPDF split onto a separate line.

    If the heading section contains additional body lines (e.g. table cells
    merged into a single section), those are emitted as a paragraph below
    the heading so content is not lost.
    """
    prefix = "#" * sec.heading_level
    remainder_lines: list[str] = []
    if sec.lines:
        first = _render_line_spans(sec.lines[0], suppress_bold=True).strip()
        if len(sec.lines) > 1 and _BARE_HEADING_NUM_RE.match(first):
            head_fs = sec.lines[0].font_size
            parts = [first]
            consumed = 1
            for line in sec.lines[1:]:
                rendered_part = _render_line_spans(
                    line, suppress_bold=True).strip()
                # Always join short title lines after a bare number, even
                # when font size differs (Kretz-style: 19.9pt number +
                # 11.6pt ALL-CAPS title). Only break on font mismatch once
                # the title has been consumed (i.e. after the first join).
                if abs(line.font_size - head_fs) > 0.5:
                    if consumed == 1 and len(line.text.split()) <= 8:
                        parts.append(rendered_part)
                        consumed += 1
                    break
                parts.append(rendered_part)
                consumed += 1
            text = " ".join(p for p in parts if p)
            for line in sec.lines[consumed:]:
                r = _render_line_spans(line).strip()
                if r:
                    remainder_lines.append(r)
        else:
            text = first
            head_fs = sec.lines[0].font_size
            if _heading_line_continues(first):
                parts = [first]
                consumed = 1
                for line in sec.lines[1:]:
                    if abs(line.font_size - head_fs) > 0.5:
                        break
                    part = _render_line_spans(
                        line, suppress_bold=True).strip()
                    if part:
                        parts.append(part)
                    consumed += 1
                text = " ".join(p for p in parts if p)
                for line in sec.lines[consumed:]:
                    r = _render_line_spans(line).strip()
                    if r:
                        remainder_lines.append(r)
            else:
                for line in sec.lines[1:]:
                    r = _render_line_spans(line).strip()
                    if r:
                        remainder_lines.append(r)
    else:
        text = sec.text.split("\n")[0]
    clean_text = text.strip()
    if not clean_text:
        return ""
    heading = f"{prefix} {clean_text}"
    if remainder_lines:
        # Filter out lines that duplicate the heading text (bold-overlay
        # artifacts in some PDFs produce 3x repeated heading spans).
        head_norm = re.sub(r"[^a-z0-9\s]", "", clean_text.lower()).strip()
        filtered = []
        for rl in remainder_lines:
            rl_norm = re.sub(r"[^a-z0-9\s]", "", rl.lower()).strip()
            if rl_norm and rl_norm != head_norm:
                filtered.append(rl)
        if filtered:
            body = " ".join(filtered)
            return f"{heading}\n\n{body}"
    return heading


def _normalize_bullet(char: str) -> str:
    """Replace Unicode bullet characters with *."""
    if char in BULLET_CHARS:
        return "*"
    return char


def _normalize_bullets(text: str) -> str:
    """Replace Unicode bullet characters with * throughout text."""
    return "".join(_normalize_bullet(ch) for ch in text)


def _render_list_spans(sec: Section) -> str:
    """Render a list section with span formatting and normalized bullets."""
    if sec.lines:
        result_lines = []
        for line in sec.lines:
            rendered = _render_line_spans(line).rstrip()
            if rendered:
                result_lines.append(_normalize_bullets(rendered))
        return "\n".join(result_lines)

    return _normalize_bullets(sec.text.rstrip())


_DEFAULT_CHAR_WIDTH = 6.0


def _estimate_char_width(sec_lines: list) -> float:
    """Estimate monospace character width from span bbox and text length."""
    for line in sec_lines:
        for span in line.spans:
            n = len(span.text.replace(" ", ""))
            if n >= 2:
                w = span.bbox[2] - span.bbox[0]
                if w > 0:
                    return w / n
    return _DEFAULT_CHAR_WIDTH


def _render_code_block(sec: Section) -> str:
    """Render a code section as a fenced code block.

    Uses glyph x-positions to calculate indentation: the offset
    of each line's first character from the block's left margin,
    divided by the monospace character width.
    """
    lang = sec.fence_lang or DEFAULT_FENCE_LANG
    if not sec.lines:
        return f"```{lang}\n{sec.text}\n```"

    char_w = _estimate_char_width(sec.lines)
    content_x = [
        ln.spans[0].bbox[0] for ln in sec.lines
        if ln.spans and ln.spans[0].text.strip()
    ]
    base_x = min(content_x) if content_x else 0.0

    lines = []
    for line in sec.lines:
        raw = _render_line_spans(line, in_code_section=True)
        if line.spans:
            first_text = line.spans[0].text
            text_indent = len(first_text) - len(first_text.lstrip())

            first_nonspace = line.spans[0]
            for sp in line.spans:
                if sp.text.strip():
                    first_nonspace = sp
                    break
            x0 = first_nonspace.bbox[0]
            x_indent = round((x0 - base_x) / char_w) if char_w > 0 else 0
            x_indent = max(x_indent, 0)

            if text_indent > 0 and x_indent > 0 and text_indent == x_indent:
                indent = text_indent
            else:
                indent = x_indent if x_indent > 0 else text_indent

            lines.append(" " * indent + raw.lstrip())
        else:
            lines.append(raw)
    code = "\n".join(lines)
    return f"```{lang}\n{code}\n```"


def _render_wording_line(line: Line) -> str:
    """Render a wording line, merging consecutive same-role spans.

    Whitespace-only spans between two same-role spans are absorbed into
    the group; whitespace between different roles is emitted as-is.
    Both ins and del use the role name directly as the HTML tag.
    """
    def _render_group(role: str | None, spans: list[Span]) -> str:
        text = "".join(s.text for s in spans)
        if role in ("ins", "del"):
            s = text.strip()
            lead = text[:len(text) - len(text.lstrip())]
            trail = text[len(text.rstrip()):]
            return f"{lead}<{role}>{s}</{role}>{trail}"
        return "".join(
            f"`{s.text.strip()}`" if s.monospace and s.text.strip() else s.text
            for s in spans
        )

    parts: list[str] = []
    group: list[Span] = []
    group_role: str | None = None
    ws_buf: list[Span] = []

    for span in line.spans:
        role = span.wording_role if span.text.strip() else None
        if role is None:
            ws_buf.append(span)
        elif role == group_role:
            group.extend(ws_buf)
            ws_buf.clear()
            group.append(span)
        else:
            if group:
                parts.append(_render_group(group_role, group))
            parts.extend(s.text for s in ws_buf)
            ws_buf.clear()
            group_role, group = role, [span]

    if group:
        parts.append(_render_group(group_role, group))
    parts.extend(s.text for s in ws_buf)
    return "".join(parts)


def _render_wording_section(sec: Section) -> str:
    """Render a wording section with Pandoc fenced div markers."""
    div_class = sec.kind.value
    rendered_lines = []
    for line in sec.lines:
        rendered_lines.append(_render_wording_line(line))
    text = normalize_whitespace("\n".join(rendered_lines))
    lines = [ln for ln in text.split("\n") if ln.strip()]
    has_code = any(s.monospace for ln in sec.lines for s in ln.spans if s.text.strip())
    if has_code:
        inner = "\n".join(lines)
    else:
        inner = " ".join(ln.strip() for ln in lines)
    return f":::{div_class}\n\n{inner}\n\n:::"


_DINGBATS_MAP: dict[int, str] = {
    0x14: "✓",
    0x18: "✗",
}


def _decode_dingbats(span: Span) -> Span:
    """Replace Dingbats-encoded control chars with Unicode equivalents.

    MuPDF passes through raw byte values for ZapfDingbats glyphs
    (e.g. 0x14 → ✓, 0x18 → ✗).  These are C0 control characters in
    Unicode and would be invisible or stripped.  Only fires for spans
    with font_name containing "Dingbats".
    """
    if "Dingbats" not in (span.font_name or ""):
        return span
    out: list[str] = []
    changed = False
    for ch in span.text:
        mapped = _DINGBATS_MAP.get(ord(ch))
        if mapped:
            out.append(mapped)
            changed = True
        else:
            out.append(ch)
    if not changed:
        return span
    return Span(
        text="".join(out),
        font_name=span.font_name,
        font_size=span.font_size,
        bold=span.bold,
        italic=span.italic,
        monospace=span.monospace,
        bbox=span.bbox,
        origin=span.origin,
        color=span.color,
        link_url=span.link_url,
        wording_role=span.wording_role,
    )


def _render_cell_spans(spans: list, suppress_bold: bool = False) -> str:
    """Render a table cell's spans with inline formatting.

    When every non-whitespace span is monospace, all spans are merged into a
    single backtick pair to avoid fragmented output like `t``<``U`.
    Newline marker spans (from table.py line-merge) become spaces in pipe tables.
    """
    if not spans:
        return ""
    # Decode Dingbats font control chars (✓/✗) before rendering
    spans = [_decode_dingbats(s) for s in spans]
    # Replace newline markers with spaces for pipe-table rendering
    flat_spans = []
    for s in spans:
        if s.text == "\n":
            if flat_spans and flat_spans[-1].text.endswith(" "):
                continue
            flat_spans.append(Span(text=" "))
        else:
            flat_spans.append(s)
    text_spans = [s for s in flat_spans if s.text.strip()]
    if text_spans and all(s.monospace for s in text_spans):
        merged = "".join(s.text for s in flat_spans).strip()
        if merged:
            return f"`{merged}`"
    line = Line(spans=flat_spans)
    result = _render_line_spans(line, suppress_bold=suppress_bold).strip()
    return result.replace("|", "\\|")


def _spans_to_code_lines(spans: list) -> str:
    """Convert cell spans to multi-line code text.

    Newline marker spans (text="\\n") from table.py line-merge are
    converted to actual newlines. All other spans are concatenated as raw text.
    """
    if not spans:
        return ""
    spans = [_decode_dingbats(s) for s in spans]
    parts: list[str] = []
    for sp in spans:
        if sp.text == "\n":
            parts.append("\n")
        else:
            parts.append(sp.text)
    return "".join(parts).strip()


def _render_code_comparison(sec: Section) -> str:
    """Render a code-comparison table as side-by-side fenced code blocks.

    Each cell may contain multiple logical lines marked by newline spans
    (text="\\n") inserted by table.py during cell merge.
    If the first row contains short labels (e.g. "Before" / "After"),
    they are used as headings above each code block.
    """
    if not sec.columns:
        return sec.text

    rows = sec.columns
    num_cols = max(len(row) for row in rows) if rows else 0

    # Detect header row: first row with only short text (<=3 words per cell)
    headers: list[str] = []
    data_start = 0
    if rows:
        first_row = rows[0]
        first_row_texts = []
        for cell in first_row:
            t = "".join(_decode_dingbats(s).text for s in cell).strip()
            first_row_texts.append(t)
        if all(len(t.split()) <= 3 for t in first_row_texts) and any(first_row_texts):
            headers = first_row_texts
            data_start = 1

    blocks_per_col: list[list[str]] = [[] for _ in range(num_cols)]
    for row in rows[data_start:]:
        for col_idx in range(num_cols):
            if col_idx < len(row):
                cell_spans = row[col_idx]
                cell_text = _spans_to_code_lines(cell_spans)
            else:
                cell_text = ""
            blocks_per_col[col_idx].append(cell_text)

    parts = []
    for col_idx, col_lines in enumerate(blocks_per_col):
        content = "\n".join(col_lines).strip()
        if content:
            label = ""
            if headers and col_idx < len(headers) and headers[col_idx]:
                label = f"// {headers[col_idx]}\n"
            parts.append(f"```cpp\n{label}{content}\n```")

    return "\n\n".join(parts)


def _render_table_as_text(sec: Section) -> str:
    """Render a false-positive table as plain paragraphs."""
    if not sec.columns:
        return sec.text

    parts = []
    for row in sec.columns:
        row_parts = []
        for cell_spans in row:
            cell_text = "".join(
                _decode_dingbats(s).text for s in cell_spans).strip()
            if cell_text:
                row_parts.append(cell_text)
        if row_parts:
            parts.append(" ".join(row_parts))

    return "\n\n".join(parts)


def _cell_text(row: list, ci: int) -> str:
    """Extract plain text from a cell's span list."""
    if ci >= len(row):
        return ""
    return "".join(s.text for s in row[ci]).strip()


def _render_html_table(sec: Section) -> str:
    """Render a table as HTML with <pre> blocks for multi-line code cells.

    Used for code-comparison tables (e.g. "Tony Tables") where each cell
    contains multi-line code that would be flattened by a pipe table.

    When ``sec.table_continuation`` is true the section is a cross-page
    continuation whose duplicate header has been stripped.  All rows are
    rendered as ``<td>`` data cells (no ``<th>`` header row).
    """
    if not sec.columns:
        return sec.text

    rows = sec.columns
    is_continuation = getattr(sec, "table_continuation", False)
    num_cols = max(len(row) for row in rows)
    col_w = f"{100 // num_cols}%" if num_cols else "50%"
    _S = (f"border: 1px solid #999; padding: 6px 10px; "
          f"vertical-align: top; width: {col_w};")
    parts: list[str] = [
        '<table border="1" rules="all" cellpadding="6" cellspacing="0"'
        ' style="border-collapse: collapse; width: 100%;">',
    ]

    # NB-ballot cells: newlines are MuPDF line-wrapping artifacts from
    # narrow PDF columns, not semantic breaks. Collapse to spaces.
    is_nb_ballot = getattr(sec, "table_kind", None) == "nb_ballot"
    collapse_newlines = is_nb_ballot

    # Pre-compute rowspan for col-0 in NB-ballot tables: when consecutive
    # data rows have an empty col-0 they are continuations of the same NB
    # number, so the first row's col-0 cell spans them all (like the PDF).
    col0_rowspan: dict[int, int] = {}  # ri -> span count (only for starters)
    col0_skip: set[int] = set()        # ri values to skip col-0 rendering
    if is_nb_ballot:
        ri = 1 if not is_continuation else 0  # skip header row
        while ri < len(rows):
            span_start = ri
            span_count = 1
            while (ri + span_count < len(rows)
                   and _cell_text(rows[ri + span_count], 0) == ""):
                span_count += 1
            if span_count > 1:
                col0_rowspan[span_start] = span_count
                for k in range(span_start + 1, span_start + span_count):
                    col0_skip.add(k)
            ri += span_count

    for ri, row in enumerate(rows):
        parts.append("<tr>")
        is_header = (ri == 0 and not is_continuation)
        tag = "th" if is_header else "td"
        for ci in range(num_cols):
            if ci == 0 and ri in col0_skip:
                continue
            cell_spans = row[ci] if ci < len(row) else []
            cell_spans = [_decode_dingbats(s) for s in cell_spans]
            cell_lines: list[str] = []
            current_line: list[str] = []
            for span in cell_spans:
                if span.text == "\n":
                    cell_lines.append("".join(current_line))
                    current_line = []
                else:
                    current_line.append(span.text)
            if current_line:
                cell_lines.append("".join(current_line))
            if collapse_newlines:
                text = " ".join(
                    part for line in cell_lines
                    for part in [line.strip()] if part
                ).strip()
            else:
                text = "\n".join(cell_lines).strip()
            escaped = _html.escape(text)
            rs_attr = ""
            if ci == 0 and ri in col0_rowspan:
                rs_attr = f' rowspan="{col0_rowspan[ri]}"'
            if is_header or not text:
                parts.append(
                    f'<{tag} style="{_S}"{rs_attr}>{escaped}</{tag}>')
            else:
                parts.append(
                    f'<{tag} style="{_S}"{rs_attr}>'
                    f'<pre style="margin: 0;">{escaped}</pre></{tag}>')
        parts.append("</tr>")

    parts.append("</table>")
    return "\n".join(parts)


def _render_table(sec: Section) -> str:
    """Render a table section according to its assigned strategy."""
    if sec.table_strategy == "code_blocks":
        return _render_code_comparison(sec)
    if sec.table_strategy == "html_table":
        return _render_html_table(sec)
    if sec.table_strategy == "skip":
        return _render_table_as_text(sec)

    if not sec.columns:
        return sec.text

    rows = sec.columns
    is_continuation = getattr(sec, "table_continuation", False)
    num_cols = max(len(row) for row in rows)

    lines = []
    if is_continuation:
        data_rows = rows
    else:
        header = rows[0]
        header_cells = [
            _render_cell_spans(cell, suppress_bold=True).replace("\n", " ")
            for cell in header
        ]
        while len(header_cells) < num_cols:
            header_cells.append("")
        lines.append("| " + " | ".join(header_cells) + " |")
        lines.append("| " + " | ".join(["---"] * num_cols) + " |")
        data_rows = rows[1:]

    for row in data_rows:
        cells = [
            _render_cell_spans(cell).replace("\n", " ")
            for cell in row
        ]
        while len(cells) < num_cols:
            cells.append("")
        lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)


_FIGURE_HORIZONTAL_Y_THRESHOLD = 5.0


_MERMAID_SPECIAL_RE = re.compile(r"[^\w]")


def _mermaid_participant(name: str) -> str:
    """Declare a sequence-diagram participant, quoting when needed."""
    safe = name.replace('"', "#quot;")
    if _MERMAID_SPECIAL_RE.search(safe):
        return f'    participant "{safe}"'
    return f"    participant {safe}"


def _mermaid_actor(name: str) -> str:
    """Reference a participant in a message line."""
    safe = name.replace('"', "#quot;")
    if _MERMAID_SPECIAL_RE.search(safe):
        return f'"{safe}"'
    return safe


def _mermaid_message_label(label: str) -> str:
    return label.replace('"', "#quot;").replace("\n", " ").strip()


def _render_sequence_figure(graph: FigureGraph, sec: Section) -> str:
    """Render a sequence diagram as a Mermaid ``sequenceDiagram`` block.

    Orphan labels (text between lifelines) are assigned to the nearest
    edge by y-position to serve as call labels.
    """
    orphans = _populate_graph_texts(graph, sec)
    _assign_orphan_labels_to_edges(graph, orphans)

    lines = ["sequenceDiagram"]
    for node in graph.nodes:
        lines.append(_mermaid_participant(node.text or "?"))

    for e in graph.edges:
        src = _mermaid_actor(graph.nodes[e.source_idx].text or "?")
        tgt = _mermaid_actor(graph.nodes[e.target_idx].text or "?")
        arrow = "-->>" if e.dashed else "->>"
        if e.label:
            label = _mermaid_message_label(e.label)
            lines.append(f"    {src}{arrow}{tgt}: {label}")
        else:
            lines.append(f"    {src}{arrow}{tgt}")

    return "```mermaid\n" + "\n".join(lines) + "\n```"


def _assign_orphan_labels_to_edges(
    graph: FigureGraph,
    orphans: list[tuple[tuple[float, float, float, float], str]],
) -> None:
    """Assign orphan text labels to sequence diagram edges by y-proximity.

    Each edge in a sequence diagram corresponds to a horizontal arrow at
    a specific y-position.  Orphan labels near that y-position and
    between the source/target x-columns become edge labels.
    """
    if not orphans or not graph.edges:
        return

    edge_y: list[float] = []
    for e in graph.edges:
        if e.y_position > 0:
            edge_y.append(e.y_position)
        else:
            src_bbox = graph.nodes[e.source_idx].bbox
            tgt_bbox = graph.nodes[e.target_idx].bbox
            ey = (src_bbox[1] + src_bbox[3] + tgt_bbox[1] + tgt_bbox[3]) / 4
            edge_y.append(ey)

    used: set[int] = set()
    for ei, e in enumerate(graph.edges):
        src_bbox = graph.nodes[e.source_idx].bbox
        tgt_bbox = graph.nodes[e.target_idx].bbox
        min_x = min(src_bbox[0], tgt_bbox[0]) - 10
        max_x = max(src_bbox[2], tgt_bbox[2]) + 10

        best_idx = None
        best_dist = 40.0
        for oi, (bbox, _text) in enumerate(orphans):
            if oi in used:
                continue
            ox = (bbox[0] + bbox[2]) / 2
            oy = (bbox[1] + bbox[3]) / 2
            if not (min_x <= ox <= max_x):
                continue
            d = abs(oy - edge_y[ei])
            if d < best_dist:
                best_dist = d
                best_idx = oi

        if best_idx is not None:
            used.add(best_idx)
            e.label = orphans[best_idx][1]


def _render_graph_figure(graph: FigureGraph, sec: Section) -> str:
    """Render a FIGURE section using extracted graph topology.

    Populates graph node texts from the section's lines by matching
    bbox overlap, then walks the edges to produce topologically correct
    output with proper arrow notation.
    """
    is_sequence = getattr(graph, "_is_sequence", False)
    if is_sequence:
        return _render_sequence_figure(graph, sec)

    orphans = _populate_graph_texts(graph, sec)

    non_empty = [n for n in graph.nodes if n.text]
    if not non_empty:
        return _render_positional_figure(sec)

    is_vertical = _is_vertical_layout(graph)
    has_bidir = any(e.bidirectional for e in graph.edges)

    if has_bidir:
        parts = []
        for e in graph.edges:
            src = graph.nodes[e.source_idx].text or "?"
            tgt = graph.nodes[e.target_idx].text or "?"
            arrow = " <-> " if e.bidirectional else " -> "
            parts.append(f"{src}{arrow}{tgt}")
        body = "\n> ".join(parts)
        return f"> **[Figure: Flow Diagram (bidirectional)]**\n> {body}"

    if graph.is_linear:
        chain = _linearize_graph_with_labels(graph, orphans)
        if chain:
            if is_vertical:
                steps = [f"> {i}. {t}" for i, t in enumerate(chain, 1)]
                return "> **[Figure: Flow Diagram]**\n" + "\n".join(steps)
            body = " -> ".join(chain)
            return f"> **[Figure: Concept Chain]**\n> {body}"

    parts = []
    for e in graph.edges:
        src = graph.nodes[e.source_idx].text or "?"
        tgt = graph.nodes[e.target_idx].text or "?"
        parts.append(f"{src} -> {tgt}")
    body = "\n> ".join(parts)
    return f"> **[Figure: Flow Diagram]**\n> {body}"


def _populate_graph_texts(
    graph: FigureGraph,
    sec: Section,
) -> list[tuple[tuple[float, float, float, float], str]]:
    """Fill graph node texts by matching section lines to node bboxes.

    Returns a list of ``(bbox, text)`` for lines that did not match any
    node (orphan labels sitting between boxes, e.g. edge labels).
    """
    orphans: list[tuple[tuple[float, float, float, float], str]] = []
    for ln in sec.lines:
        t = ln.text.strip()
        if not t:
            continue
        lx = (ln.bbox[0] + ln.bbox[2]) / 2
        ly = (ln.bbox[1] + ln.bbox[3]) / 2
        best_idx = None
        best_dist = float("inf")
        for i, node in enumerate(graph.nodes):
            nx0, ny0, nx1, ny1 = node.bbox
            if nx0 - 5 <= lx <= nx1 + 5 and ny0 - 5 <= ly <= ny1 + 5:
                cx = (nx0 + nx1) / 2
                cy = (ny0 + ny1) / 2
                d = abs(lx - cx) + abs(ly - cy)
                if d < best_dist:
                    best_dist = d
                    best_idx = i
        if best_idx is not None:
            existing = graph.nodes[best_idx].text
            if existing:
                if t != existing:
                    graph.nodes[best_idx].text = existing + " " + t
            else:
                graph.nodes[best_idx].text = t
        else:
            orphans.append((ln.bbox, t))
    return orphans


def _is_vertical_layout(graph: FigureGraph) -> bool:
    """True if graph nodes are arranged vertically (y-spread > x-spread)."""
    if len(graph.nodes) < 2:
        return False
    ys = [(n.bbox[1] + n.bbox[3]) / 2 for n in graph.nodes]
    xs = [(n.bbox[0] + n.bbox[2]) / 2 for n in graph.nodes]
    y_spread = max(ys) - min(ys)
    x_spread = max(xs) - min(xs)
    return y_spread > x_spread


def _linearize_graph_with_labels(
    graph: FigureGraph,
    orphans: list[tuple[tuple[float, float, float, float], str]],
) -> list[str] | None:
    """Walk edges to produce a linear chain, inserting orphan labels
    between connected nodes when they fall spatially between them.
    """
    if not graph.edges:
        return None

    out_map: dict[int, int] = {}
    in_set: set[int] = set()
    for e in graph.edges:
        if e.source_idx in out_map:
            return None
        out_map[e.source_idx] = e.target_idx
        if e.target_idx in in_set:
            return None
        in_set.add(e.target_idx)

    starts = [i for i in range(len(graph.nodes))
              if i in out_map and i not in in_set]
    if len(starts) != 1:
        return None

    chain: list[str] = []
    current = starts[0]
    visited: set[int] = set()
    while current is not None:
        if current in visited:
            return None
        visited.add(current)
        chain.append(graph.nodes[current].text or "?")
        next_idx = out_map.get(current)
        if next_idx is not None and orphans:
            label = _find_label_between(
                graph.nodes[current].bbox,
                graph.nodes[next_idx].bbox,
                orphans,
            )
            if label:
                chain.append(label)
        current = next_idx

    return chain if len(chain) >= 2 else None


def _find_label_between(
    src_bbox: tuple[float, float, float, float],
    tgt_bbox: tuple[float, float, float, float],
    orphans: list[tuple[tuple[float, float, float, float], str]],
) -> str | None:
    """Find an orphan label positioned between two node bboxes."""
    sx = (src_bbox[0] + src_bbox[2]) / 2
    sy = (src_bbox[1] + src_bbox[3]) / 2
    tx = (tgt_bbox[0] + tgt_bbox[2]) / 2
    ty = (tgt_bbox[1] + tgt_bbox[3]) / 2
    mid_x = (sx + tx) / 2
    mid_y = (sy + ty) / 2

    best_dist = float("inf")
    best_idx = None
    for i, (bbox, _text) in enumerate(orphans):
        ox = (bbox[0] + bbox[2]) / 2
        oy = (bbox[1] + bbox[3]) / 2
        d = abs(ox - mid_x) + abs(oy - mid_y)
        x_between = min(sx, tx) - 20 <= ox <= max(sx, tx) + 20
        y_between = min(sy, ty) - 20 <= oy <= max(sy, ty) + 20
        if (x_between or y_between) and d < best_dist:
            best_dist = d
            best_idx = i

    if best_idx is not None:
        _, text = orphans.pop(best_idx)
        return text
    return None


def _render_positional_figure(sec: Section) -> str:
    """Fallback: render figure using positional sorting (no graph data)."""
    if not sec.lines:
        texts = [t.strip() for t in sec.text.split("\n") if t.strip()]
        if not texts:
            return "> **[Figure]**"
        body = "\n> ".join(texts)
        return f"> **[Figure]**\n> {body}"

    live = [(ln.bbox, ln.text.strip()) for ln in sec.lines if ln.text.strip()]
    if not live:
        return "> **[Figure]**"

    y_coords = [bbox[1] for bbox, _ in live]
    y_spread = max(y_coords) - min(y_coords)

    if y_spread < _FIGURE_HORIZONTAL_Y_THRESHOLD:
        sorted_items = sorted(live, key=lambda pair: pair[0][0])
        body = " -> ".join(t for _, t in sorted_items)
        return f"> **[Figure: Concept Chain]**\n> {body}"

    sorted_items = sorted(live, key=lambda pair: (pair[0][1], pair[0][0]))
    steps = [f"> {i}. {t}" for i, (_, t) in enumerate(sorted_items, 1)]
    return "> **[Figure: Flow Diagram]**\n" + "\n".join(steps)


def _render_figure_placeholder(sec: Section) -> str:
    """Render a FIGURE section as an LLM-readable blockquote.

    When a FigureGraph is available (Tier 3 arrow extraction), renders
    using extracted topology for correct flow direction.  Otherwise
    falls back to positional sorting (Tier 2).
    """
    if sec.figure_graph is not None and sec.figure_graph.edges:
        return _render_graph_figure(sec.figure_graph, sec)
    return _render_positional_figure(sec)


_ALT_TEXT_ESCAPE_RE = re.compile(r"([\[\]\\])")


def _escape_alt_text(text: str) -> str:
    """Escape ``[``, ``]``, and ``\\`` so they survive inside ``![alt](...)``.

    Markdown image alt-text grammar is permissive but it does break on
    unbalanced brackets and unescaped backslashes. Captions like
    ``Figure 1 [revised]: ...`` would otherwise truncate the alt at
    the literal ``]``.
    """
    return _ALT_TEXT_ESCAPE_RE.sub(r"\\\1", text)


def _render_image(sec: Section) -> str:
    """Render a :class:`SectionKind.IMAGE` section as ``![alt](filename)``.

    Reads the alt text from :attr:`Section.image_ref.suggested_alt`
    (not :attr:`Section.text` - IMAGE sections carry empty text by
    design, see types.py). The filename is the stable on-disk basename
    assigned by :func:`finalize_extraction`, kept on
    :attr:`ExtractedImage.stored_filename` so the markdown reference
    matches what the CLI will write via
    :meth:`StorageBackend.write_paper_image`.
    """
    if sec.image_ref is None:
        return ""
    alt = _escape_alt_text(sec.image_ref.suggested_alt)
    return f"![{alt}]({sec.image_ref.stored_filename})"


def _render_section_md(sec: Section) -> str:
    """Render a single section to Markdown."""
    if sec.kind in (SectionKind.TITLE, SectionKind.HEADING):
        return _render_heading_spans(sec)

    if sec.kind == SectionKind.TABLE:
        return _render_table(sec)

    if sec.kind == SectionKind.IMAGE:
        return _render_image(sec)

    if sec.kind == SectionKind.CODE:
        return _render_code_block(sec)

    if sec.kind == SectionKind.LIST:
        return _render_list_spans(sec)

    if sec.kind in (SectionKind.WORDING, SectionKind.WORDING_ADD,
                    SectionKind.WORDING_REMOVE):
        return _render_wording_section(sec)

    if sec.kind == SectionKind.FIGURE:
        return _render_figure_placeholder(sec)

    if sec.kind == SectionKind.PARAGRAPH:
        return _render_paragraph_spans(sec)

    return sec.text


_EMDASH_NESTING_X_THRESHOLD = 6.0


def _is_emdash_bullet_section(sec: Section) -> bool:
    """True when every non-empty raw line starts with an em/en-dash bullet."""
    if sec.kind != SectionKind.PARAGRAPH or not sec.lines:
        return False
    for line in sec.lines:
        raw = "".join(s.text for s in line.spans).strip()
        if raw and not _EMDASH_BULLET_RE.match(raw):
            return False
    return bool(sec.lines)


def _section_min_x0(sec: Section) -> float:
    """Leftmost x-coordinate across all lines in the section."""
    x0s = [ln.bbox[0] for ln in sec.lines if ln.bbox]
    return min(x0s) if x0s else 0.0


def _assign_emdash_nesting(sections: list[Section]) -> None:
    """Pre-pass: set indent_level on consecutive em-dash bullet sections.

    Groups of adjacent em-dash PARAGRAPH sections are identified.
    Within each group, the leftmost x0 is the base level (indent 0).
    Sections whose x0 is shifted right by >= _EMDASH_NESTING_X_THRESHOLD
    get increasing indent_level values.
    """
    n = len(sections)
    i = 0
    while i < n:
        if not _is_emdash_bullet_section(sections[i]):
            i += 1
            continue
        group_start = i
        while i < n and _is_emdash_bullet_section(sections[i]):
            i += 1
        group = sections[group_start:i]
        if len(group) < 2:
            continue
        x0s = [_section_min_x0(s) for s in group]
        base_x0 = min(x0s)
        unique_x = sorted(set(x0s))
        level_map: dict[float, int] = {}
        for ux in unique_x:
            if ux - base_x0 < _EMDASH_NESTING_X_THRESHOLD:
                level_map[ux] = 0
            else:
                level_map[ux] = len(
                    [v for v in level_map.values() if v > 0]
                ) + 1
        for sec, x0 in zip(group, x0s):
            sec.indent_level = level_map.get(x0, 0)


def _annotate_wrap(rendered: str, sec: Section) -> str:
    """Wrap rendered markdown in a colored HTML div for highlight mode."""
    from .highlight import KIND_COLORS, CONFIDENCE_BORDERS
    bg = KIND_COLORS.get(sec.kind.value, "transparent")
    border = CONFIDENCE_BORDERS.get(sec.confidence.value, "3px solid #ccc")
    kind_label = sec.kind.value.replace("-", " ")
    return (
        f'<div style="background:{bg};border-left:{border};'
        f'padding:4px 10px;margin:2px 0;border-radius:3px" '
        f'title="{kind_label} | {sec.confidence.value} | page {sec.page_num}">\n\n'
        f'{rendered}\n\n'
        f'</div>'
    )


def emit_markdown(
    metadata: dict,
    sections: list[Section],
    *,
    annotate: bool = False,
    images_truncated: bool = False,
    source_image_count: int = 0,
    vector_uncertainty: VectorUncertaintyStats | None = None,
    glyph_stats: GlyphPassStats | None = None,
) -> str:
    """Generate the output Markdown from structured sections.

    Confident sections are clean Markdown. Uncertain sections emit
    the MuPDF version marked with an HTML comment.

    When *annotate* is True, each section's rendered markdown is wrapped
    in a colored HTML ``<div>`` whose background indicates the
    ``SectionKind`` and whose left-border indicates ``Confidence``.
    The result is valid markdown+HTML that scrivener renders correctly,
    producing the same layout as the normal view but with colored
    section backgrounds.  Used by the preview highlight overlay.

    When ``images_truncated`` is True, an HTML comment is appended at
    end-of-body recording how many images were kept versus how many
    the source contained.

    When ``vector_uncertainty`` is non-None and
    :func:`should_emit_marker` returns True, a second HTML comment is
    appended with the per-paper vector-extraction accounting (pages
    scanned / skipped, candidates, kept, rejected, reasons dict). The
    marker exists so a reader can see why diagrams might be missing
    or surprisingly present.

    When ``glyph_stats`` is non-None and the glyph-placeholder pass
    placed any U+FFFD or skipped any coincident rect, a trailing
    ``tomd:glyph-placeholders`` comment is appended last - its
    ``placeholders`` count is taken from the finished body (after
    duplicate-paragraph collapse) so it matches a grep of the output.
    """
    _assign_emdash_nesting(sections)

    # Pre-pass: fold cross-page table continuations into the preceding
    # table so they render as a single HTML/pipe table.  The detection
    # layer marks the continuation with table_continuation=True and has
    # already stripped its duplicate header row.
    folded: set[int] = set()
    for i in range(len(sections) - 1, 0, -1):
        sec = sections[i]
        if (sec.kind == SectionKind.TABLE
                and getattr(sec, "table_continuation", False)
                and sec.columns):
            # Walk backwards to find the preceding TABLE section
            # (skipping any interleaved non-table sections like page
            # numbers or headings that the pipeline may have inserted).
            for j in range(i - 1, -1, -1):
                prev = sections[j]
                if prev.kind == SectionKind.TABLE and prev.columns:
                    prev.columns.extend(sec.columns)
                    prev.text = "\n".join(
                        " | ".join(
                            "".join(s.text for s in cell).strip()
                            for cell in row)
                        for row in prev.columns)
                    folded.add(i)
                    break
    if folded:
        sections = [s for i, s in enumerate(sections) if i not in folded]

    parts: list[str] = []

    fm = format_front_matter(metadata)
    if fm:
        parts.append(fm)

    line_num = fm.count("\n") + 3 if fm else 1

    for sec in sections:
        if sec.kind == SectionKind.UNCERTAIN:
            text = sec.text.rstrip()
            text_lines = text.count("\n") + 1
            text_start = line_num + 2
            comment = f"<!-- tomd:uncertain:L{text_start}-L{text_start + text_lines - 1} -->"
            if annotate:
                parts.append(_annotate_wrap(comment + "\n\n" + text, sec))
            else:
                parts.append(comment)
                parts.append(text)
            line_num += text_lines + 3
            continue

        rendered = _render_section_md(sec)
        if not rendered.strip():
            continue
        # Skip headings that render as bare ATX prefix with no text.
        if sec.kind in (SectionKind.TITLE, SectionKind.HEADING):
            if not rendered.lstrip("#").strip():
                continue
        if annotate:
            rendered = _annotate_wrap(rendered, sec)
        parts.append(rendered)
        line_num += rendered.count("\n") + 2

    if images_truncated and source_image_count > 0:
        kept_count = sum(
            1 for sec in sections if sec.kind == SectionKind.IMAGE
        )
        dropped = source_image_count - kept_count
        if dropped > 0:
            parts.append(TRUNCATION_MARKER_TEMPLATE.format(
                kept=kept_count,
                total=source_image_count,
                dropped=dropped,
            ))

    if vector_uncertainty is not None and should_emit_marker(vector_uncertainty):
        parts.append(format_uncertainty_marker(vector_uncertainty))

    md = "\n\n".join(parts)
    md = dedup_paragraphs(md)

    if fm:
        title = metadata.get("title", "")
        fm_end = _find_front_matter_end(md)
        if fm_end is not None:
            line_end = md.find("\n", fm_end)
            if line_end >= 0:
                body = md[line_end + 1:]
                body = strip_leading_h1(body, title)
                md = md[:line_end + 1] + body

    md = strip_redundant_body_meta(md)
    md = strip_orphan_toc_list(md)

    # Inject a <style> block for table borders when the document
    # contains HTML tables. VS Code / Cursor markdown preview strips
    # inline style attributes but honours embedded <style> tags.
    if "<table " in md:
        _TABLE_CSS = (
            "<style>\n"
            "table, th, td { border: 1px solid #999; "
            "border-collapse: collapse; padding: 6px 10px; }\n"
            "th { background: #f5f5f5; }\n"
            "</style>"
        )
        insert_after = None
        if fm:
            fm_end = _find_front_matter_end(md)
            if fm_end is not None:
                line_end = md.find("\n", fm_end)
                if line_end >= 0:
                    insert_after = line_end + 1
        if insert_after is not None:
            md = md[:insert_after] + "\n" + _TABLE_CSS + "\n" + md[insert_after:]
        else:
            md = _TABLE_CSS + "\n\n" + md

    if fm:
        fm_end = _find_front_matter_end(md)
        if fm_end is not None:
            line_end = md.find("\n", fm_end)
            if line_end >= 0:
                body = md[line_end + 1:]
                body = strip_leading_h1(body, title)
                md = md[:line_end + 1] + body

    # Glyph-placeholder marker, appended last so its ``placeholders``
    # count reflects the U+FFFD actually present in the finished body
    # (after duplicate-paragraph collapse), matching what a reader greps.
    # Fires when any placeholder survived or any coincident rect was
    # skipped (a pure-coincident paper still emits placeholders=0 for
    # traceability).
    if glyph_stats is not None:
        present = md.count(UNKNOWN_GLYPH)
        if (present or glyph_stats.skipped_coincident
                or glyph_stats.skipped_code_section):
            md = md.rstrip() + "\n\n" + GLYPH_PLACEHOLDER_MARKER_TEMPLATE.format(
                placeholders=present,
                skipped_coincident=glyph_stats.skipped_coincident,
                skipped_code_section=glyph_stats.skipped_code_section,
            )

    md = md.rstrip() + "\n"
    return md


def emit_prompts(sections: list[Section]) -> list[str] | None:
    """Generate self-contained LLM reconcile prompts for uncertain regions.

    Each returned element is a complete prompt the operator can paste into
    any LLM verbatim. Returns ``None`` when there are no uncertain regions.
    """
    uncertain = [(idx, s) for idx, s in enumerate(sections)
                 if s.kind == SectionKind.UNCERTAIN]
    if not uncertain:
        return None

    prompts: list[str] = []
    for idx, sec in uncertain:
        ctx_before = ""
        ctx_after = ""
        if idx > 0 and sections[idx - 1].kind != SectionKind.UNCERTAIN:
            ctx_before = sections[idx - 1].text[:200].strip()
        if idx + 1 < len(sections) and sections[idx + 1].kind != SectionKind.UNCERTAIN:
            ctx_after = sections[idx + 1].text[:200].strip()

        parts: list[str] = []
        parts.append(
            "You are reconciling text extracted from a PDF using two independent "
            "methods that produced different results. Reconcile them into clean "
            "Markdown."
        )
        parts.append("")
        parts.append(
            "CRITICAL: Keep ALL data verbatim. Do not summarize, omit, or paraphrase "
            "any text. Every word from the source must appear in your output. You are "
            "only fixing structure (paragraphs, headings, lists, formatting) - never "
            "content."
        )
        parts.append("")
        parts.append(f"This region is on page {sec.page_num}.")
        parts.append("")

        if ctx_before:
            parts.append("Context (preceding confident section):")
            parts.append(f"> {ctx_before}")
            parts.append("")

        parts.append("MuPDF extraction:")
        parts.append("```")
        parts.append(sec.mupdf_text or sec.text)
        parts.append("```")
        parts.append("")
        parts.append("Spatial extraction:")
        parts.append("```")
        parts.append(sec.spatial_text or sec.text)
        parts.append("```")

        if ctx_after:
            parts.append("")
            parts.append("Context (following confident section):")
            parts.append(f"> {ctx_after}")

        prompts.append("\n".join(parts))

    return prompts
