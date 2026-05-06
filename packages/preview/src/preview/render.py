#
# Copyright (c) 2026 Greg Kaleka (greg@gregkaleka.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#
# Official repository: https://github.com/cppalliance/wg21-paperflow
#

"""Render paperflow markdown to a complete HTML page via wg21-scrivener.

The renderer wraps :func:`scrivener.html_builder.build_html` so the caller
can hand in a markdown string and get back a self-contained HTML document
with inline CSS, ready to drop into an iframe. Image references that
scrivener emits as absolute filesystem paths under its bundled images
directory are rewritten to ``data:`` URLs so the browser can load them
without a static-file route.
"""

from __future__ import annotations

import base64
import mimetypes
import re
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from scrivener.config import IMAGES_DIR, load_style, resolve_style_path
from scrivener.html_builder import build_html


_DEFAULT_STYLE = "wg21"

_LIGHT_SCHEME_OVERRIDE = (
    '<meta name="color-scheme" content="light">\n'
    "<style>\n"
    "  html, body { background: #ffffff; color: #1f2328; }\n"
    "  body { margin: 0; padding: 24px; }\n"
    "</style>\n"
)


@lru_cache(maxsize=4)
def _load_style(style_name: str) -> dict[str, Any]:
    return load_style(resolve_style_path(style_name))


def render_markdown(md_text: str, *, style: str = _DEFAULT_STYLE) -> str:
    """Render a paperflow markdown string to a complete Scrivener HTML page.

    Returns a standalone HTML document (head + body, inline CSS) suitable
    for serving as the source of an iframe.
    """
    style_dict = dict(_load_style(style))

    with tempfile.TemporaryDirectory(prefix="paperflow-preview-") as tmp:
        tmp_path = Path(tmp)
        md_path = tmp_path / "paper.md"
        md_path.write_text(md_text or "", encoding="utf-8")
        out_path = tmp_path / "paper.html"
        build_html(
            md_path=md_path,
            output_path=out_path,
            cli_cfg={},
            style=style_dict,
            mode="full",
            inline_css=True,
        )
        html = out_path.read_text(encoding="utf-8")

    html = _inline_scrivener_images(html)

    # Scrivener's emitted CSS only styles <article>; without a body rule the
    # iframe inherits the OS dark-mode background, leaving scrivener's dark
    # article text unreadable. Inject a sibling <style> after </head> so it
    # wins on source order.
    head_close = "</head>"
    idx = html.find(head_close)
    if idx == -1:
        return _LIGHT_SCHEME_OVERRIDE + html
    return html[:idx] + _LIGHT_SCHEME_OVERRIDE + html[idx:]


_IMG_SRC_RE = re.compile(r'(<img\b[^>]*\bsrc=")([^"]+)(")', re.IGNORECASE)


@lru_cache(maxsize=64)
def _image_data_url(resolved_path: str) -> str | None:
    p = Path(resolved_path)
    if not p.is_file():
        return None
    mime, _ = mimetypes.guess_type(p.name)
    mime = mime or "application/octet-stream"
    data = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def _inline_scrivener_images(html: str) -> str:
    """Replace absolute-path <img src> under scrivener's images dir with data URLs.

    Scrivener emits the resolved filesystem path directly, which the browser
    treats as a URL and 404s. The rewrite is confined to scrivener's package
    images directory so unrelated absolute paths can't be turned into data URLs.
    """
    images_root = IMAGES_DIR.resolve()

    def replace(match: re.Match[str]) -> str:
        src = match.group(2)
        if not src.startswith("/"):
            return match.group(0)
        try:
            resolved = Path(src).resolve()
            resolved.relative_to(images_root)
        except (OSError, ValueError):
            return match.group(0)
        data_url = _image_data_url(str(resolved))
        if data_url is None:
            return match.group(0)
        return f"{match.group(1)}{data_url}{match.group(3)}"

    return _IMG_SRC_RE.sub(replace, html)
