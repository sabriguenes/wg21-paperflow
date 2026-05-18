"""Re-exports from tomd.lib.shared and tomd.lib.metadata_yaml for backward compatibility."""

from tomd.lib.shared import (  # noqa: F401
    ALLOWED_LINK_SCHEMES,
    DATE_RE,
    DEFAULT_FENCE_LANG,
    DOC_NUM_PATTERN,
    DOC_NUM_RE,
    EMAIL_RE,
    FORMAT_CHARS,
    SECTION_NUM_PATTERN,
    SECTION_NUM_PREFIX_RE,
    ascii_escape,
    dedup_paragraphs,
    deobfuscate_email,
    enrich_reply_to_names,
    normalize_date,
    parse_author_lines,
    strip_format_chars,
    strip_leading_h1,
    strip_freeform_metadata_lines,
    strip_orphan_toc_list,
    strip_redundant_body_meta,
)

from tomd.lib.metadata_yaml.format import (  # noqa: F401
    FRONT_MATTER_ORDER,
    format_front_matter,
    sanitize_metadata,
)
