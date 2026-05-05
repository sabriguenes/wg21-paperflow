"""Re-exports from tomd.lib.shared for backward compatibility."""

from tomd.lib.shared import (  # noqa: F401
    ALLOWED_LINK_SCHEMES,
    DATE_RE,
    DOC_NUM_PATTERN,
    DOC_NUM_RE,
    EMAIL_RE,
    FORMAT_CHARS,
    FRONT_MATTER_ORDER,
    SECTION_NUM_PATTERN,
    SECTION_NUM_PREFIX_RE,
    ascii_escape,
    dedup_paragraphs,
    extract_revision,
    format_front_matter,
    parse_author_lines,
    sanitize_metadata,
    strip_format_chars,
    strip_leading_h1,
    strip_redundant_body_meta,
)
