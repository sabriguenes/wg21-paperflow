"""PDF to Markdown converter."""

from tomd.lib.pdf.pipeline import (
    convert_pdf,
    PipelineResult,
    _enrich_pdf_reply_to,
    _is_slide_deck,
    _is_standards_draft,
    _toc_structural_hints,
    _TOC_X_TOLERANCE,
)

__all__ = ["convert_pdf", "PipelineResult"]
