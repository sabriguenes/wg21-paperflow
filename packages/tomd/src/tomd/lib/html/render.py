"""DOM-to-Markdown rendering for WG21 HTML papers."""

import re
import urllib.parse

from bs4 import BeautifulSoup, Comment, Tag, NavigableString

from .. import strip_format_chars, SECTION_NUM_PREFIX_RE, ALLOWED_LINK_SCHEMES

_BOLD_WRAP_RE = re.compile(r"^\*\*(.+)\*\*$")
_LOSSY_TABLE_MARKER = "<!-- tomd:lossy-table -->"
_COLLAPSE_WS_RE = re.compile(r"\s+")

_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_LIST_CONTAINER_TAGS = frozenset({"ul", "ol"})
_BLOCK_TAGS = frozenset({
    "p", "pre", "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "ul", "ol", "blockquote", "div", "section",
    "dl", "hr", "figure",
})


_INLINE_PARENT_TAGS = frozenset({
    "p", "span", "a", "em", "i", "strong", "b",
}) | _HEADING_TAGS


def _fix_misnested_blocks(soup: BeautifulSoup) -> None:
    """Repair block elements wrongly nested inside inline parents by html.parser.

    html.parser does not auto-close inline-context tags (``<p>``, ``<h3>``,
    ``<em>``, etc.) when it encounters a block element. This pulls block
    children out to siblings, preserving surrounding inline content in
    wrapper elements of the same type.  Runs iteratively until stable.
    """
    changed = True
    while changed:
        changed = False
        for parent_tag in list(soup.find_all(_INLINE_PARENT_TAGS)):
            if not any(
                isinstance(c, Tag) and c.name in _BLOCK_TAGS
                for c in parent_tag.children
            ):
                continue
            outer = parent_tag.parent
            if outer is None:
                continue
            changed = True
            tag_name = parent_tag.name
            tag_attrs = dict(parent_tag.attrs) if parent_tag.attrs else {}
            collected_inline: list = []

            def _flush_inline():
                if not collected_inline:
                    return
                if not any(
                    (isinstance(n, Tag) and n.get_text(strip=True))
                    or (isinstance(n, NavigableString) and str(n).strip())
                    for n in collected_inline
                ):
                    collected_inline.clear()
                    return
                wrapper = soup.new_tag(tag_name, **tag_attrs)
                for node in collected_inline:
                    wrapper.append(node.extract())
                parent_tag.insert_before(wrapper)
                collected_inline.clear()

            children = list(parent_tag.children)
            for child in children:
                if isinstance(child, Tag) and child.name in _BLOCK_TAGS:
                    _flush_inline()
                    parent_tag.insert_before(child.extract())
                else:
                    collected_inline.append(child)
            _flush_inline()
            parent_tag.decompose()



def _fix_misnested_list_items(soup: BeautifulSoup) -> None:
    """Promote <li> elements nested inside other <li> to siblings.

    html.parser does not auto-close <li> when it encounters a new <li>,
    causing successive list items to nest inside the first one. This walks
    all <li> tags and moves any child <li> to the correct sibling position.
    """
    changed = True
    while changed:
        changed = False
        for li in list(soup.find_all("li")):
            nested = li.find_all("li", recursive=False)
            if not nested:
                for child in li.children:
                    if isinstance(child, Tag) and child.name in ("ul", "ol"):
                        nested.extend(child.find_all("li", recursive=False))
                continue
            parent = li.parent
            if parent is None:
                continue
            for nested_li in nested:
                changed = True
                parent.append(nested_li.extract())


def render_body(soup: BeautifulSoup, generator: str) -> str:
    """Render the HTML body to Markdown.

    Warning: this function may mutate the soup tree (extracting nested
    list elements). Do not reuse the soup object after calling this.
    """
    _fix_misnested_blocks(soup)
    _fix_misnested_list_items(soup)
    body = soup.find("body") or soup
    parts: list[str] = []
    _render_children(body, parts, generator)
    return "\n\n".join(p for p in parts if p.strip())


def _render_children(element, parts: list[str], generator: str):
    """Render each child of element, appending Markdown strings to parts."""
    for child in element.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            text = str(child).strip()
            if text:
                parts.append(text)
        elif isinstance(child, Tag):
            rendered = _render_element(child, generator)
            if rendered is not None:
                parts.append(rendered)


def _render_element(el: Tag, generator: str) -> str | None:
    """Render a single HTML element to Markdown."""
    tag = el.name

    if tag in ("style", "script", "link", "meta", "head"):
        return None

    if tag in _HEADING_TAGS:
        return _render_heading(el)

    if tag == "p":
        return _render_paragraph(el)

    if tag == "pre":
        return _render_pre(el, generator)

    if tag == "code-block":
        return _render_code_block_custom(el)

    if tag == "div":
        return _render_div(el, generator)

    if tag == "ul":
        return _render_list(el, "-", generator)

    if tag == "ol":
        return _render_list(el, "1.", generator)

    if tag == "table":
        return _render_table(el)

    if tag == "blockquote":
        return _render_blockquote(el, generator)

    if tag == "dl":
        return _render_dl(el, generator)

    if tag == "hr":
        return "---"

    if tag == "section":
        parts: list[str] = []
        _render_children(el, parts, generator)
        return "\n\n".join(p for p in parts if p.strip())

    if tag in ("main", "article", "aside", "figure", "figcaption",
               "header", "footer", "nav", "details", "summary"):
        parts = []
        _render_children(el, parts, generator)
        return "\n\n".join(p for p in parts if p.strip())

    if tag in ("example-block", "note-block", "bug-block"):
        parts = []
        _render_children(el, parts, generator)
        inner = "\n\n".join(p for p in parts if p.strip())
        if inner:
            return "> " + inner.replace("\n", "\n> ")
        return None

    if tag == "tt-":
        text = el.get_text()
        return f"`{text}`" if text.strip() else None

    if tag == "code":
        code_div = el.find("div", class_="code")
        if code_div:
            text = code_div.get_text()
            text = text.strip("\n")
            return f"```cpp\n{text}\n```"

    if tag in ("span", "a", "code", "em", "strong", "b", "i", "sub", "sup",
               "ins", "del", "mark", "small", "s", "u", "abbr", "cite",
               "dfn", "var", "kbd", "samp", "time", "data", "wbr",
               "h-", "f-serif"):
        return _render_inline(el)

    parts = []
    _render_children(el, parts, generator)
    result = "\n\n".join(p for p in parts if p.strip())
    return result if result else None


_HEADING_SKIP_CLASSES = frozenset({"header-section-number", "secno", "self-link"})


def _render_heading(el: Tag) -> str | None:
    """Render a heading element to ATX Markdown."""
    level = int(el.name[1])
    text = _inline_text_excluding(el, _HEADING_SKIP_CLASSES).strip()
    if not text:
        return None
    text = text.replace("\n", " ")
    text = re.sub(r"  +", " ", text)
    text = SECTION_NUM_PREFIX_RE.sub("", text)
    text = _BOLD_WRAP_RE.sub(r"\1", text)
    return f"{'#' * level} {text}"


def _is_code_paragraph(el: Tag) -> bool:
    """True if <p> contains only <span class="code"> children.

    The dascandy/fiets generator uses this pattern for standalone code
    declarations (e.g. constructor signatures). These should be emitted
    as fenced code blocks, not flattened to prose paragraphs.

    Targets <span class="code"> specifically, NOT <code> which is inline
    formatting in Bikeshed and other generators.
    """
    has_code_span = False
    for child in el.children:
        if isinstance(child, NavigableString):
            if child.strip():
                return False
        elif child.name == "span" and "code" in (child.get("class") or []):
            has_code_span = True
        else:
            return False
    return has_code_span


def _render_paragraph(el: Tag) -> str | None:
    """Render a paragraph to a single unwrapped line."""
    if _is_code_paragraph(el):
        text = el.get_text().strip()
        return f"```cpp\n{text}\n```" if text else None
    text = _collapse_whitespace(_inline_text(el))
    return text if text else None


def _collapse_whitespace(text: str) -> str:
    """Collapse runs of whitespace to single spaces, strip format chars."""
    text = strip_format_chars(text)
    return _COLLAPSE_WS_RE.sub(" ", text).strip()


def _render_pre(el: Tag, generator: str) -> str:
    """Render a preformatted block as a fenced code block."""
    code_el = el.find("code")
    if code_el:
        lang = _detect_code_language(code_el, generator)
        text = code_el.get_text()
    else:
        lang = ""
        text = el.get_text()
    text = text.strip("\n")
    return f"```{lang}\n{text}\n```"


def _render_code_block_custom(el: Tag) -> str:
    """Render a <code-block> custom element (Jan Schultke's generator) as fenced code."""
    text = el.get_text()
    text = text.strip("\n")
    return f"```cpp\n{text}\n```"


def _detect_code_language(code_el: Tag, generator: str) -> str:
    """Detect the programming language from code element classes."""
    classes = code_el.get("class", [])
    for cls in classes:
        if cls.startswith("sourceCode"):
            lang = cls[len("sourceCode"):]
            if lang:
                return lang.lower()
        if cls.startswith("language-"):
            return cls[len("language-"):].lower()
        if cls in ("cpp", "c", "python", "javascript", "rust", "go",
                    "java", "bash", "shell", "json", "yaml", "xml"):
            return cls
    parent = code_el.parent
    if parent and parent.name == "pre":
        for cls in parent.get("class", []):
            if cls.startswith("sourceCode"):
                lang = cls[len("sourceCode"):]
                if lang:
                    return lang.lower()
    if generator == "mpark":
        return "cpp"
    return ""


def _render_div(el: Tag, generator: str) -> str | None:
    """Render a div - dispatch by class."""
    classes = el.get("class", [])

    if "sourceCode" in classes:
        pre = el.find("pre")
        if pre:
            return _render_pre(pre, generator)

    if "code" in classes:
        text = el.get_text()
        text = text.strip("\n")
        return f"```cpp\n{text}\n```"

    if any(c in classes for c in ("note", "example", "advisement")):
        parts = []
        _render_children(el, parts, generator)
        inner = "\n\n".join(p for p in parts if p.strip())
        if inner:
            return "> " + inner.replace("\n", "\n> ")

    if any(c in classes for c in ("wording", "wording-add", "wording-remove")):
        return _render_wording_div(el, generator)

    parts = []
    _render_children(el, parts, generator)
    result = "\n\n".join(p for p in parts if p.strip())
    return result if result else None


def _render_wording_div(el: Tag, generator: str) -> str:
    """Render a wording section with Pandoc fenced div markers."""
    classes = el.get("class", [])
    if "wording-add" in classes:
        fence = ":::wording-add"
    elif "wording-remove" in classes:
        fence = ":::wording-remove"
    else:
        fence = ":::wording"
    parts = []
    _render_children(el, parts, generator)
    inner = "\n\n".join(p for p in parts if p.strip())
    return f"{fence}\n\n{inner}\n\n:::"


_CODE_BLOCK_TAGS = frozenset({"pre", "code-block"})


def _render_list(el: Tag, marker: str, generator: str) -> str | None:
    """Render an ordered or unordered list."""
    items = []
    for i, li in enumerate(el.find_all("li", recursive=False)):
        prefix = f"{i + 1}." if marker == "1." else "-"
        # Detach nested sublists before capturing inline text so they are not
        # walked into by _inline_text (which would duplicate their contents).
        subs = [sub.extract()
                for sub in li.find_all(_LIST_CONTAINER_TAGS, recursive=False)]
        nested_parts = []
        for sub in subs:
            sub_rendered = _render_element(sub, generator)
            if sub_rendered:
                indented = "\n".join("  " + line for line in sub_rendered.split("\n"))
                nested_parts.append(indented)

        # Extract code blocks before inlining so they are rendered as
        # fenced blocks rather than flattened to inline text.
        code_parts = []
        for cb in li.find_all(_CODE_BLOCK_TAGS, recursive=False):
            rendered = _render_element(cb.extract(), generator)
            if rendered:
                code_parts.append(rendered)

        text = _collapse_whitespace(_inline_text(li))
        if text:
            items.append(f"{prefix} {text}")
        for cp in code_parts:
            items.append(cp)
        for np in nested_parts:
            items.append(np)
    return "\n".join(items) if items else None


def _has_spans(el: Tag) -> bool:
    """Return True if any cell uses colspan or rowspan."""
    for cell in el.find_all(["th", "td"]):
        if cell.get("colspan") or cell.get("rowspan"):
            return True
    return False


def _has_mangled_cells(el: Tag) -> bool:
    """Return True if any cell contains nested cells (parser artifact).

    html.parser does not auto-close <td>/<th> tags, creating nested cell
    structures. These need the descendant-walking flat reconstruction path.
    """
    for cell in el.find_all(["th", "td"]):
        if cell.find(["th", "td"]):
            return True
    return False


def _needs_flat_reconstruction(el: Tag) -> bool:
    """Return True for tables that need the descendant-walking flat path.

    This covers: nested <table> elements, parser-mangled nested cells,
    and block-level content inside cells.
    """
    if el.find("table"):
        return True
    for cell in el.find_all(["th", "td"]):
        if cell.find(["th", "td"]):
            return True
        if cell.find(["pre", "ol", "ul", "blockquote"]):
            return True
        if cell.find("p") and len(cell.find_all("p")) > 1:
            return True
    return False



def _has_br_in_cells(el: Tag) -> bool:
    """Return True if any cell contains a <br> tag."""
    for cell in el.find_all(["th", "td"]):
        if cell.find("br"):
            return True
    return False


def _cell_own_text(cell: Tag) -> str:
    """Get text directly owned by a cell, excluding nested cells/rows."""
    parts = []
    for child in cell.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag) and child.name not in ("td", "th", "tr",
                                                            "thead", "tbody",
                                                            "tfoot", "table"):
            parts.append(child.get_text(" ", strip=True))
    return " ".join(parts).strip()


def _denormalize_table(el: Tag) -> list[list[str]]:
    """Expand rowspan/colspan into a flat rectangular grid of cell texts.

    Two-pass algorithm: builds a None-initialized 2D matrix, then fills it
    by walking <tr> elements and tracking pending rowspans per column.
    """
    trs = []
    containers = el.find_all(["thead", "tbody", "tfoot"], recursive=False)
    if containers:
        for c in containers:
            trs.extend(c.find_all("tr", recursive=False))
    else:
        trs = el.find_all("tr", recursive=False)

    if not trs:
        return []

    # First pass: determine grid dimensions
    max_cols = 0
    for tr in trs:
        col_count = 0
        for cell in tr.find_all(["th", "td"], recursive=False):
            col_count += int(cell.get("colspan", 1))
        if col_count > max_cols:
            max_cols = col_count
    num_rows = len(trs)

    if max_cols == 0:
        return []

    grid: list[list[str | None]] = [[None] * max_cols for _ in range(num_rows)]

    # Second pass: fill the grid
    for row_idx, tr in enumerate(trs):
        col_idx = 0
        for cell in tr.find_all(["th", "td"], recursive=False):
            # Skip columns already filled by previous rowspans
            while col_idx < max_cols and grid[row_idx][col_idx] is not None:
                col_idx += 1
            if col_idx >= max_cols:
                break

            text = _inline_text(cell).strip().replace("|", "\\|")
            text = _COLLAPSE_WS_RE.sub(" ", text)
            rs = int(cell.get("rowspan", 1))
            cs = int(cell.get("colspan", 1))

            for dr in range(rs):
                for dc in range(cs):
                    r, c = row_idx + dr, col_idx + dc
                    if r < num_rows and c < max_cols:
                        grid[r][c] = text

            col_idx += cs

    # Replace any remaining None with empty string
    return [[cell if cell is not None else "" for cell in row] for row in grid]


def _render_denormalized_table(el: Tag) -> str | None:
    """Render a table with rowspan/colspan as a flat denormalized pipe table."""
    rows = _denormalize_table(el)
    if not rows:
        return None

    num_cols = max(len(r) for r in rows)
    for r in rows:
        while len(r) < num_cols:
            r.append("")

    headers = [_BOLD_WRAP_RE.sub(r"\1", cell) for cell in rows[0]]

    lines = [_LOSSY_TABLE_MARKER]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * num_cols) + " |")
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _render_table_flat(el: Tag) -> str:
    """Render a table as a pipe table, handling parser-mangled DOM.

    When html.parser has mangled the tree (nested cells due to missing
    closing tags), we collect ALL <td>/<th> descendants in document order,
    extract their direct text via _cell_own_text, and use <tr> boundaries
    to reconstruct rows. Output is a standard Markdown pipe table.
    """
    all_cells = el.find_all(["td", "th"])

    if not all_cells:
        return el.get_text(" ", strip=True)

    rows: list[list[str]] = []
    current_row: list[str] = []

    seen: set[int] = set()
    for node in el.descendants:
        if not isinstance(node, Tag):
            continue
        if node.name == "tr" and current_row:
            rows.append(current_row)
            current_row = []
        elif node.name in ("td", "th"):
            nid = id(node)
            if nid in seen:
                continue
            seen.add(nid)
            text = _cell_own_text(node)
            text = _COLLAPSE_WS_RE.sub(" ", text).strip().replace("|", "\\|")
            current_row.append(text)
    if current_row:
        rows.append(current_row)

    if not rows:
        return el.get_text(" ", strip=True)

    num_cols = max(len(r) for r in rows)
    for r in rows:
        while len(r) < num_cols:
            r.append("")

    headers = [_BOLD_WRAP_RE.sub(r"\1", cell) for cell in rows[0]]

    lines = [_LOSSY_TABLE_MARKER]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * num_cols) + " |")
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _render_table(el: Tag) -> str | None:
    """Render a table as a Markdown pipe table.

    Tables whose cells contain <pre> or <code-block> elements cannot be
    represented as pipe tables. For those, extract the code blocks as
    fenced code and skip the table structure.

    Tables with rowspan/colspan are denormalized into flat pipe tables.
    Tables with parser-mangled DOM (nested cells from unclosed tags) are
    reconstructed via descendant walking. Only tables with nested <table>
    elements or block-level cell content (pre, lists) fall back to the
    flat reconstruction path.
    """
    if el.find(_CODE_BLOCK_TAGS):
        return _render_code_table(el)

    if _needs_flat_reconstruction(el):
        return _render_table_flat(el)

    if _has_spans(el):
        return _render_denormalized_table(el)

    rows: list[list[str]] = []
    containers = el.find_all(["thead", "tbody", "tfoot"], recursive=False)
    if containers:
        tr_sources = containers
    else:
        tr_sources = [el]
    for src in tr_sources:
        for tr in src.find_all("tr", recursive=False):
            cells = []
            for td in tr.find_all(["th", "td"], recursive=False):
                cell_text = _inline_text(td).strip()
                cell_text = _COLLAPSE_WS_RE.sub(" ", cell_text)
                cells.append(cell_text.replace("|", "\\|"))
            if cells:
                rows.append(cells)

    if not rows:
        return None

    num_cols = max(len(r) for r in rows)
    for r in rows:
        while len(r) < num_cols:
            r.append("")

    headers = [_BOLD_WRAP_RE.sub(r"\1", cell) for cell in rows[0]]

    lines = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * num_cols) + " |")
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _render_code_table(el: Tag) -> str | None:
    """Extract fenced code blocks from a table containing <pre> or <code-block>.

    Some generators (dascandy/fiets, Bikeshed, Schultke) wrap code inside
    table cells. Emit every non-empty block as its own fenced block so
    before/after comparisons and multi-snippet tables are preserved.
    """
    blocks: list[str] = []
    for cb in el.find_all(_CODE_BLOCK_TAGS):
        text = cb.get_text().strip()
        if text:
            blocks.append(f"```cpp\n{text}\n```")
    if not blocks:
        return None
    return _LOSSY_TABLE_MARKER + "\n\n" + "\n\n".join(blocks)


def _render_blockquote(el: Tag, generator: str) -> str | None:
    """Render a blockquote with > prefix."""
    parts = []
    _render_children(el, parts, generator)
    inner = "\n\n".join(p for p in parts if p.strip())
    if not inner:
        return None
    return "> " + inner.replace("\n", "\n> ")


def _render_dl(el: Tag, generator: str) -> str | None:
    """Render a definition list."""
    items = []
    for child in el.children:
        if not isinstance(child, Tag):
            continue
        if child.name == "dt":
            text = _inline_text(child).strip()
            if text:
                items.append(f"**{text}**")
        elif child.name == "dd":
            code_parts = []
            for cb in child.find_all(_CODE_BLOCK_TAGS, recursive=False):
                rendered = _render_element(cb.extract(), generator)
                if rendered:
                    code_parts.append(rendered)
            text = _inline_text(child).strip()
            if text:
                items.append(f": {text}")
            items.extend(code_parts)
    return "\n".join(items) if items else None


def _render_inline(el: Tag) -> str:
    """Render an inline element."""
    return _inline_text(el)


def _inline_text_excluding(el: Tag, skip_classes: frozenset[str]) -> str:
    """Like _inline_text but skips child elements with any class in skip_classes."""
    parts = []
    for child in el.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag):
            child_classes = set(child.get("class", []))
            if child_classes & skip_classes:
                continue
            parts.append(_inline_text(child))
    return "".join(parts)


def _inline_text(el: Tag) -> str:
    """Convert an element's content to inline Markdown text."""
    parts = []
    for child in el.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag):
            tag = child.name

            if tag in ("style", "script"):
                continue

            if tag in ("table", "thead", "tbody", "tfoot", "tr"):
                parts.append(child.get_text(" ", strip=True))
                continue

            inner = _inline_text(child)

            if tag == "code":
                stripped = inner.strip()
                if stripped:
                    parts.append(f"`{stripped}`")
                continue

            if tag in ("strong", "b"):
                stripped = inner.strip()
                if stripped:
                    parts.append(f"**{stripped}**")
                continue

            if tag in ("em", "i"):
                stripped = inner.strip()
                if stripped:
                    parts.append(f"*{stripped}*")
                continue

            if tag == "a":
                href = child.get("href", "")
                text = inner.strip()
                if href and text:
                    if href.startswith("#"):
                        parts.append(text)
                    else:
                        scheme = urllib.parse.urlparse(href).scheme.lower()
                        if scheme in ALLOWED_LINK_SCHEMES:
                            parts.append(f"[{text}]({href})")
                        else:
                            parts.append(text)
                elif text:
                    parts.append(text)
                continue

            if tag == "br":
                parts.append("\n")
                continue

            if tag == "ins":
                parts.append(f"<ins>{inner}</ins>")
                continue

            if tag == "del":
                parts.append(f"<del>{inner}</del>")
                continue

            if tag == "sub":
                parts.append(f"<sub>{inner}</sub>")
                continue

            if tag == "sup":
                parts.append(f"<sup>{inner}</sup>")
                continue

            if tag == "tt-":
                stripped = inner.strip()
                if stripped:
                    parts.append(f"`{stripped}`")
                continue

            if tag in ("span", "div", "td", "th", "li", "dt", "dd",
                       "mark", "small", "s", "u", "abbr", "cite",
                       "dfn", "var", "kbd", "samp", "time", "data",
                       "wbr", "p", "figure", "figcaption",
                       "h-", "f-serif", "c-"):
                parts.append(inner)
                continue

            if tag in _HEADING_TAGS:
                parts.append(inner)
                continue

            parts.append(inner)

    return "".join(parts)
