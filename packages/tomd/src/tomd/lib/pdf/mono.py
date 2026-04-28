"""Triple-signal monospace font detection.

Three independent signals:
  1. Font name decomposition (strip modifiers, split camelCase, check keywords)
  2. Glyph bounding box widths are uniform (low coefficient of variation)
  3. Glyph x-origin spacing is uniform (measures advance width directly)

Acceptance rules:
  - Two or more signals agreeing: accept (high confidence)
  - Signal 3 alone: accept (measures the defining property of monospace)
  - Signal 1 alone: accept (fallback when no glyph data is available)
  - Signal 2 alone: reject (weakest signal, bbox widths are noisy)

The spatial extraction path (rawdict) provides per-character data for signals
2 and 3. The MuPDF dict path does not. The pipeline orchestrator propagates
spatial glyph-width decisions to MuPDF spans of the same font after extraction.
"""

import math
import re

from .types import Block

_FONT_MODIFIERS = frozenset({
    "thin", "hairline", "extralight", "ultralight", "light",
    "regular", "normal", "book", "medium",
    "semibold", "demibold", "bold", "extrabold", "ultrabold",
    "black", "heavy",
    "italic", "oblique", "roman", "upright",
    "condensed", "narrow", "extended", "expanded", "wide", "compressed",
    "display", "text", "caption", "subhead", "headline", "mt",
})

# WG21 paper fonts by framework:
#   LaTeX (mpark/wg21, tcbrindle, cplusplus/draft): LMMono*, LMTT* (Latin Modern)
#   LaTeX (plain/legacy): CMTT* (Computer Modern Typewriter)
#   mpark/wg21 override: DejaVu Sans Mono
#   Bikeshed (HTML->PDF via browser): Menlo (macOS), Consolas (Win), Monaco (macOS)
#   XeLaTeX users: Source Code Pro, Inconsolata, Fira Code
#   Microsoft: Cascadia Code/Mono
#
# Keywords that are family prefixes (liberation, dejavu, fira, ubuntu) are
# omitted because their proportional siblings (LiberationSans, DejaVuSerif,
# FiraSans, Ubuntu) would false-positive.  The monospace variants of those
# families already match via "mono" or "code".
_MONO_KEYWORDS = frozenset({
    "mono", "courier", "code", "consolas", "menlo",
    "inconsolata", "iosevka", "hack",
    "jetbrains", "lmtt", "monaco",
    "cmtt", "cascadia",
})

_CAMEL_SPLIT_RE = re.compile(
    r"(?<=[a-z])(?=[A-Z])"
    r"|(?<=[A-Z])(?=[A-Z][a-z])"
    r"|(?<=[A-Za-z])(?=\d)"
    r"|(?<=\d)(?=[A-Za-z])"
)

_GLYPH_CV_THRESHOLD = 0.15
_FAT_THIN_REJECT_RATIO = 1.3

_MIN_CHARS_FOR_METRICS = 3
_FAT_CHARS = frozenset("MWmw@%")
_THIN_CHARS = frozenset("Iil1|!.,;:' ")


def _strip_modifiers(font_name: str) -> str:
    """Remove separators and known style/weight/width modifiers."""
    name = font_name.replace("-", " ").replace("_", " ")
    tokens = name.split()
    kept = [t for t in tokens if t.lower() not in _FONT_MODIFIERS]
    return "".join(kept)


def _split_camel(name: str) -> list[str]:
    """Split a PascalCase/camelCase string into lowercase tokens."""
    parts = _CAMEL_SPLIT_RE.sub(" ", name).split()
    return [p.lower() for p in parts if p]


_TRAILING_DIGITS_RE = re.compile(r"\d+$")


def _font_name_is_monospace(font_name: str) -> bool:
    """Signal 1: font name contains a monospace keyword after decomposition.

    Strips style/weight modifiers, splits camelCase, strips trailing
    digits from each token (e.g. LMTT10 -> lmtt), then checks for
    known monospace family keywords.
    """
    family = _strip_modifiers(font_name)
    tokens = _split_camel(family)
    stripped = [_TRAILING_DIGITS_RE.sub("", t) for t in tokens]
    return bool(_MONO_KEYWORDS & set(t for t in stripped if t))


def _coefficient_of_variation(values: list[float]) -> float:
    """Compute coefficient of variation (stddev / mean). Lower = more uniform.

    Uses population variance (divides by N, not N-1).
    """
    if len(values) < 2:
        return -1.0
    if not all(math.isfinite(v) for v in values):
        return -1.0
    mean = sum(values) / len(values)
    if mean == 0:
        return -1.0
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance) / mean


def _glyph_widths_uniform(char_widths: list[float]) -> float:
    """Signal 2: coefficient of variation of character bbox widths.

    Lower value = more uniform = more likely monospace.
    Returns CV, or -1.0 if not enough data.
    """
    widths = [w for w in char_widths if w > 0]
    if len(widths) < _MIN_CHARS_FOR_METRICS:
        return -1.0
    return _coefficient_of_variation(widths)


def _glyph_spacing_uniform(char_x_origins: list[float]) -> float:
    """Signal 3: coefficient of variation of inter-glyph x-origin spacing.

    Measures the advance width directly - the defining property of
    monospace fonts. Strongest signal.
    Returns CV, or -1.0 if not enough data.
    """
    if len(char_x_origins) < _MIN_CHARS_FOR_METRICS:
        return -1.0
    spacings = []
    for i in range(1, len(char_x_origins)):
        dx = char_x_origins[i] - char_x_origins[i - 1]
        if dx > 0:
            spacings.append(dx)
    if len(spacings) < 2:
        return -1.0
    return _coefficient_of_variation(spacings)


def classify_monospace(
    font_name: str,
    char_widths: list[float] | None = None,
    char_x_origins: list[float] | None = None,
    chars: list[str] | None = None,
) -> bool:
    """Accept or reject monospace classification from available signals.

    Called during extraction when raw character data is available
    (all three signals), or later with just font_name (signal 1 only).
    When chars and char_x_origins are provided, compares advance widths
    (x-origin spacings) of fat characters (M, W) against thin characters
    (i, l) to reject proportional fonts early. Advance widths are uniform
    in monospace fonts regardless of glyph body width, so the ratio of fat
    to thin advance widths is close to 1.0 for monospace and much larger
    for proportional fonts.
    """
    if chars and char_x_origins and len(chars) == len(char_x_origins) and len(chars) >= 2:
        fat_adv: list[float] = []
        thin_adv: list[float] = []
        for i in range(len(chars) - 1):
            dx = char_x_origins[i + 1] - char_x_origins[i]
            if dx > 0:
                if chars[i] in _FAT_CHARS:
                    fat_adv.append(dx)
                elif chars[i] in _THIN_CHARS:
                    thin_adv.append(dx)
        if fat_adv and thin_adv:
            avg_fat = sum(fat_adv) / len(fat_adv)
            avg_thin = sum(thin_adv) / len(thin_adv)
            if avg_thin > 0 and avg_fat / avg_thin > _FAT_THIN_REJECT_RATIO:
                return False

    s1 = _font_name_is_monospace(font_name)

    s2_cv = _glyph_widths_uniform(char_widths) if char_widths else -1.0
    s3_cv = _glyph_spacing_uniform(char_x_origins) if char_x_origins else -1.0

    s2 = 0.0 <= s2_cv <= _GLYPH_CV_THRESHOLD
    s3 = 0.0 <= s3_cv <= _GLYPH_CV_THRESHOLD

    signals = sum([s1, s2, s3])

    if signals >= 2:
        return True

    if s3:
        return True

    if s1:
        return True

    return False


_PROPAGATE_MONO_MAJORITY = 0.5


def propagate_monospace(mupdf_blocks: list[Block], spatial_blocks: list[Block],
                        dominant_font: str) -> None:
    """Apply spatial path's glyph-width monospace decisions to MuPDF spans.

    Only propagates fonts whose spatial spans are mostly classified
    monospace (by character count). Short spans of digits or thin
    characters can false-positive the per-glyph signal, so requiring a
    majority keeps proportional fonts (e.g. a regular text font with a
    handful of "1" or "3.1" spans) out of the mono set.

    The dominant_font is still discarded unless it passes a
    name-based mono check, to avoid body text leaking into code blocks
    when metrics happen to agree across a majority.
    """
    from collections import Counter
    mono_chars: Counter[str] = Counter()
    total_chars: Counter[str] = Counter()
    for b in spatial_blocks:
        for ln in b.lines:
            for s in ln.spans:
                if not s.text.strip():
                    continue
                key = s.font_name.lower()
                total_chars[key] += len(s.text)
                if s.monospace:
                    mono_chars[key] += len(s.text)

    mono_fonts = {
        f for f, total in total_chars.items()
        if total > 0 and mono_chars[f] / total >= _PROPAGATE_MONO_MAJORITY
    }

    if dominant_font and not classify_monospace(dominant_font):
        mono_fonts.discard(dominant_font)
    if not mono_fonts:
        return
    for b in mupdf_blocks:
        for ln in b.lines:
            for s in ln.spans:
                if not s.monospace and s.font_name.lower() in mono_fonts:
                    s.monospace = True
