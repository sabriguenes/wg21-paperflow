#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""WG21 paper assay pipeline (two-pass structural analysis)."""

from assay.chunker import Section, chunk_paper
from assay.locs import format_numbered_lines
from assay.pipeline import assay_paper

__all__ = [
    "Section",
    "assay_paper",
    "chunk_paper",
    "format_numbered_lines",
]
