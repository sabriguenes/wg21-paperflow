"""Wording section detection via multi-signal HSV color + drawing analysis.

Detects ins/del markup in WG21 PDF papers by combining three signals:
  1. Block-level color contamination filter - blocks containing non-wording
     chromatic colors (purple, orange, cyan) are syntax-highlighted code
     and are skipped entirely.
  2. Line-level wording detection - lines where the majority of non-link
     characters are green/red, or where green/red spans appear on an
     otherwise-black line (partial-line wording pattern).
  3. Span-level classification - green spans become ins; red spans with
     a confirmed strikethrough drawing become del.

Hyperlinks (span.link_url set) are excluded from all checks and fractions.
Insertions require no drawing decoration. Deletions require a strikethrough
drawing whose width overlaps at least 30% of the span to avoid matching
table borders and decorative rules.

Known WG21 framework diff colors (for reference):
  mpark/wg21:     add=#006e28 (H=142), rm=#bf0303 (H=1)
  tcbrindle:      add=#008019 (H=150), rm=#ff0000 (H=0)
  cplusplus/draft: add=#009999 (H=180, teal), rm=#ff0000 (H=0)
  schultke:       ins=#17752d (H=138), del=#be1621 (H=355)
All fall within our HSV ranges: green 90-180, red <=30 or >=330.
"""

import colorsys
import logging
from .types import Block

_log = logging.getLogger(__name__)

_HORIZ_LINE_Y_TOL = 1.0
_HORIZ_LINE_MIN_WIDTH = 5.0
_CONTEXT_LIGHTNESS_MIN = 0.25
_CONTEXT_LIGHTNESS_MAX = 0.65
_BLACK_LIGHTNESS_MAX = 0.15

_SATURATION_THRESHOLD = 0.15
_GREEN_HUE_MIN = 90
_GREEN_HUE_MAX = 180
_RED_HUE_WRAP = 30
_BLUE_HUE_MIN = 210
_BLUE_HUE_MAX = 270
_STRIKETHROUGH_Y_TOLERANCE = 2.0
_STRIKETHROUGH_OVERLAP_MIN = 0.3
_WORDING_LINE_MAJORITY = 0.5
_MIN_WORDING_SPANS = 5


def _color_int_to_rgb(color_int: int) -> tuple[float, float, float]:
    """Convert MuPDF integer color to (r, g, b) in 0-1 range."""
    if color_int == 0:
        return (0.0, 0.0, 0.0)
    r = ((color_int >> 16) & 0xFF) / 255.0
    g = ((color_int >> 8) & 0xFF) / 255.0
    b = (color_int & 0xFF) / 255.0
    return (r, g, b)


def _hsv(color_int: int) -> tuple[float, float, float]:
    """Convert MuPDF integer color to (hue 0-360, saturation 0-1, value 0-1)."""
    r, g, b = _color_int_to_rgb(color_int)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    return h * 360.0, s, v


def is_green_ins(color_int: int) -> bool:
    """True if color is in the green hue range with sufficient saturation."""
    h, s, _ = _hsv(color_int)
    return s >= _SATURATION_THRESHOLD and _GREEN_HUE_MIN <= h <= _GREEN_HUE_MAX


def is_red_del(color_int: int) -> bool:
    """True if color is in the red hue range with sufficient saturation."""
    h, s, _ = _hsv(color_int)
    return s >= _SATURATION_THRESHOLD and (h <= _RED_HUE_WRAP or h >= 360 - _RED_HUE_WRAP)


def _is_blue_link(color_int: int) -> bool:
    """True if color is in the blue hue range (hyperlink)."""
    h, s, _ = _hsv(color_int)
    return s >= _SATURATION_THRESHOLD and _BLUE_HUE_MIN <= h <= _BLUE_HUE_MAX


def _is_chromatic(color_int: int) -> bool:
    """True if color has enough saturation to be a chromatic signal."""
    _, s, _ = _hsv(color_int)
    return s >= _SATURATION_THRESHOLD


def _is_black(color_int: int) -> bool:
    """True if color is achromatic and dark (near-black)."""
    r, g, b = _color_int_to_rgb(color_int)
    lightness = (r + g + b) / 3.0
    return lightness <= _BLACK_LIGHTNESS_MAX


def _is_wording_color(color_int: int) -> bool:
    """True if color is green (ins) or red (del)."""
    return is_green_ins(color_int) or is_red_del(color_int)


def _is_foreign_chromatic(color_int: int) -> bool:
    """True if color is chromatic but not green, red, or blue."""
    if not _is_chromatic(color_int):
        return False
    return not (_is_wording_color(color_int) or _is_blue_link(color_int))


def _match_strikethrough(span_bbox, drawings: list) -> bool:
    """True if a horizontal drawing crosses the vertical center of the span.

    Requires the drawing to overlap at least _STRIKETHROUGH_OVERLAP_MIN of
    the span width, rejecting full-page table borders and decorative rules.
    """
    if not drawings:
        return False
    sx0, sy0, sx1, sy1 = span_bbox
    y_center = (sy0 + sy1) / 2.0
    span_w = max(sx1 - sx0, 1.0)
    for dy, dx0, dx1, _ in drawings:
        if abs(dy - y_center) > _STRIKETHROUGH_Y_TOLERANCE:
            continue
        overlap = min(dx1, sx1) - max(dx0, sx0)
        if overlap / span_w >= _STRIKETHROUGH_OVERLAP_MIN:
            return True
    return False


def _block_has_foreign_colors(block: Block) -> bool:
    """True if the block contains chromatic colors outside green/red/blue.

    Indicates syntax-highlighted code. Hyperlink spans are excluded.
    """
    for line in block.lines:
        for span in line.spans:
            if span.link_url or not span.text.strip():
                continue
            if _is_foreign_chromatic(span.color):
                return True
    return False


def _line_wording_fraction(line) -> float:
    """Fraction of non-link non-whitespace characters that are green or red."""
    total = colored = 0
    for span in line.spans:
        if span.link_url:
            continue
        n = len(span.text.replace(" ", ""))
        if n == 0:
            continue
        total += n
        if _is_wording_color(span.color):
            colored += n
    return colored / total if total else 0.0


def _line_has_wording_on_black(line) -> bool:
    """True if a line has green/red spans with the rest being black.

    Catches partial-line wording where only the new keyword is colored
    (e.g. green `constexpr` prepended to a black function declaration).
    Returns False if any non-link span is a foreign chromatic color or
    a non-black achromatic color.
    """
    has_colored = False
    for span in line.spans:
        if span.link_url or not span.text.strip():
            continue
        if _is_wording_color(span.color):
            has_colored = True
        elif _is_blue_link(span.color):
            continue
        elif _is_chromatic(span.color):
            return False
        elif not _is_black(span.color):
            return False
    return has_colored


def collect_line_drawings(page) -> list[tuple[float, float, float, tuple]]:
    """Collect horizontal line drawings from a page for decoration detection.

    Returns list of (y, x0, x1, color_rgb) for horizontal lines.
    """
    lines = []
    try:
        for drawing in page.get_drawings():
            items = drawing.get("items", [])
            color = drawing.get("color")
            if not color or not isinstance(color, (tuple, list)):
                continue
            for item in items:
                if item[0] != "l":
                    continue
                p1 = item[1]
                p2 = item[2]
                if abs(p1.y - p2.y) < _HORIZ_LINE_Y_TOL:
                    y = (p1.y + p2.y) / 2.0
                    x0 = min(p1.x, p2.x)
                    x1 = max(p1.x, p2.x)
                    if x1 - x0 > _HORIZ_LINE_MIN_WIDTH:
                        lines.append((y, x0, x1, tuple(color)))
    except Exception:
        _log.debug("get_drawings() failed", exc_info=True)
    return lines


def classify_wording(blocks: list[Block],
                     page_drawings: dict[int, list]) -> list[str]:
    """Classify spans as ins/del/context using multi-signal analysis.

    Three-layer filter:
      1. Blocks with foreign chromatic colors (not green/red/blue) are
         skipped — they are syntax-highlighted code, not wording markup.
      2. Lines qualify if either the majority (>50%) of non-link characters
         are green/red, or if any green/red spans appear with the remaining
         text being black (partial-line wording pattern).
      3. Green spans on qualifying lines become ins (no drawing required).
         Red spans become del only with a confirmed strikethrough drawing.

    Sets span.wording_role on matching spans.
    Returns an empty list (reserved for future diagnostic messages).
    """
    candidates: list[tuple] = []

    for block in blocks:
        if _block_has_foreign_colors(block):
            continue

        drawings = page_drawings.get(block.page_num, [])

        for line in block.lines:
            is_majority = _line_wording_fraction(line) > _WORDING_LINE_MAJORITY
            is_partial = not is_majority and _line_has_wording_on_black(line)
            if not is_majority and not is_partial:
                continue

            for span in line.spans:
                if not span.text.strip() or span.link_url:
                    continue

                if is_green_ins(span.color):
                    candidates.append((span, "ins"))
                elif is_red_del(span.color):
                    if _match_strikethrough(span.bbox, drawings):
                        candidates.append((span, "del"))
                    else:
                        candidates.append((span, "del_unconfirmed"))
                elif not _is_chromatic(span.color) and span.color != 0:
                    r, g, b = _color_int_to_rgb(span.color)
                    lightness = (r + g + b) / 3.0
                    if _CONTEXT_LIGHTNESS_MIN < lightness < _CONTEXT_LIGHTNESS_MAX:
                        candidates.append((span, "context"))

    ins_count = sum(1 for _, r in candidates if r == "ins")
    confirmed_del = sum(1 for _, r in candidates if r == "del")
    unconfirmed_del = sum(1 for _, r in candidates if r == "del_unconfirmed")

    if ins_count >= _MIN_WORDING_SPANS:
        candidates = [
            (s, "del" if r == "del_unconfirmed" else r)
            for s, r in candidates
        ]
    else:
        candidates = [(s, r) for s, r in candidates if r != "del_unconfirmed"]

    ins_del = [c for c in candidates if c[1] in ("ins", "del")]
    if len(ins_del) < _MIN_WORDING_SPANS:
        _log.debug("Too few wording candidates (%d < %d), skipping",
                    len(ins_del), _MIN_WORDING_SPANS)
        return []

    for span, role in candidates:
        span.wording_role = role

    del_count = sum(1 for _, r in candidates if r == "del")
    ctx_count = sum(1 for _, r in candidates if r == "context")
    _log.info("Wording detected: %d ins, %d del (%d promoted), %d context",
               ins_count, del_count, unconfirmed_del if del_count > confirmed_del else 0,
               ctx_count)

    return []
