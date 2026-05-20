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
with inline CSS, ready to drop into an iframe.

Two image-inlining passes run together:

- **Pre-scrivener**, paper-content image references in the markdown
  (``![alt](<pid>-fig{page}-{n}.{ext})``) are rewritten to raw HTML
  ``<img src="data:..." alt="...">`` tags. Scrivener strips ``src``
  and ``alt`` from markdown-generated ``<img>`` tags, so we must
  hand it inline HTML for paper images.
- **Post-scrivener**, scrivener's own bundled images (the WG21 logo,
  emitted as ``<img src="/abs/path/to/scrivener/images/...">``) get
  rewritten to ``data:`` URLs too, since the absolute filesystem path
  is meaningless in the iframe.

Both passes share :func:`_image_data_url`, keyed on ``(path, mtime_ns)``
so a re-convert that overwrites an image file invalidates the cached
base64 - long-running preview sessions can't serve stale bytes.
"""

from __future__ import annotations

import base64
import html
import mimetypes
import re
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from paperstore import StorageBackend
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


def render_markdown(
    md_text: str,
    *,
    backend: StorageBackend | None = None,
    pid: str | None = None,
    style: str = _DEFAULT_STYLE,
) -> str:
    """Render a paperflow markdown string to a complete Scrivener HTML page.

    Returns a standalone HTML document (head + body, inline CSS) suitable
    for serving as the source of an iframe.

    When ``backend`` and ``pid`` are provided, paper-content image
    references are inlined as ``data:`` URLs before scrivener runs.
    Without them, those references would survive markdown but scrivener
    would strip them, so the iframe shows broken images.
    """
    style_dict = dict(_load_style(style))

    if backend is not None and pid is not None:
        md_text = _rewrite_paper_image_refs(md_text or "", backend, pid)

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
        rendered = out_path.read_text(encoding="utf-8")

    rendered = _inline_scrivener_images(rendered)

    # Scrivener's emitted CSS only styles <article>; without a body rule the
    # iframe inherits the OS dark-mode background, leaving scrivener's dark
    # article text unreadable. Inject a sibling <style> after </head> so it
    # wins on source order.
    head_close = "</head>"
    idx = rendered.find(head_close)
    if idx == -1:
        return _LIGHT_SCHEME_OVERRIDE + rendered
    return rendered[:idx] + _LIGHT_SCHEME_OVERRIDE + rendered[idx:]


_IMG_SRC_RE = re.compile(r'(<img\b[^>]*\bsrc=")([^"]+)(")', re.IGNORECASE)

# Matches a markdown image reference whose filename has the paperstore
# convention `<pid>-fig{page}-{index}.{ext}`. Anchored to the closing
# paren of the markdown ``![alt](filename)`` form so we don't get
# confused by ``](`` substrings appearing inside alt text.
_PAPER_IMG_MD_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]"
    r"\((?P<filename>[a-z]\d+(?:r\d+)?-fig\d+-\d+\.[a-z0-9]+)\)"
)

# Used to break a stored filename back into its (pid, page, index, ext)
# tuple so we can route through ``backend.get_paper_image_path`` rather
# than poke at ``workspace_dir`` directly.
_PAPER_IMG_FILENAME_RE = re.compile(
    r"^(?P<pid>[a-z]\d+(?:r\d+)?)"
    r"-fig(?P<page>\d+)-(?P<index>\d+)\.(?P<ext>[a-z0-9]+)$"
)


def _image_data_url(path: Path) -> str | None:
    """Return ``data:<mime>;base64,...`` for ``path``, or None if unreadable.

    Wraps :func:`_image_data_url_cached` so the cache key includes
    ``mtime_ns``: a re-convert that overwrites the file at the same
    path produces a new key, and the old base64 falls out of cache
    instead of being served to a still-open preview tab.
    """
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return None
    return _image_data_url_cached(str(path), mtime_ns)


@lru_cache(maxsize=64)
def _image_data_url_cached(path_str: str, mtime_ns: int) -> str | None:
    p = Path(path_str)
    if not p.is_file():
        return None
    mime, _ = mimetypes.guess_type(p.name)
    mime = mime or "application/octet-stream"
    data = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def _rewrite_paper_image_refs(
    md_text: str,
    backend: StorageBackend,
    pid: str,
) -> str:
    """Inline paper-image refs as raw HTML ``<img>`` so scrivener preserves them.

    Scrivener strips ``src`` and ``alt`` from ``<img>`` tags generated
    from markdown image syntax, regardless of whether the src is a
    relative path, an absolute path, a URL, or a ``data:`` URI. Raw
    HTML ``<img>`` tags in the markdown body pass through intact.
    This pre-pass converts every ``![alt](<pid>-fig{page}-{n}.{ext})``
    in the markdown to an HTML ``<img src="data:..." alt="...">``.

    Refs whose filename does not match the paperstore convention, or
    whose pid does not match the current paper, are left as-is and
    will end up stripped by scrivener (same as today, no regression).
    """
    pid_lower = pid.strip().lower()

    def replace(match: re.Match[str]) -> str:
        alt = match.group("alt")
        filename = match.group("filename")
        parts = _PAPER_IMG_FILENAME_RE.match(filename)
        if parts is None or parts.group("pid") != pid_lower:
            return match.group(0)
        path = backend.get_paper_image_path(
            pid,
            int(parts.group("page")),
            int(parts.group("index")),
            parts.group("ext"),
        )
        data_url = _image_data_url(path)
        if data_url is None:
            return match.group(0)
        alt_attr = html.escape(alt, quote=True)
        return f'<img src="{data_url}" alt="{alt_attr}">'

    return _PAPER_IMG_MD_RE.sub(replace, md_text)


def _inline_scrivener_images(html_text: str) -> str:
    """Replace absolute-path <img src> under scrivener's images dir with data URLs.

    Scrivener emits the resolved filesystem path directly for its own
    bundled images (the WG21 logo, etc.). The browser treats that as a
    URL and 404s. The rewrite is confined to scrivener's package
    images directory so unrelated absolute paths can't be turned into
    data URLs.
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
        data_url = _image_data_url(resolved)
        if data_url is None:
            return match.group(0)
        return f"{match.group(1)}{data_url}{match.group(3)}"

    return _IMG_SRC_RE.sub(replace, html_text)
