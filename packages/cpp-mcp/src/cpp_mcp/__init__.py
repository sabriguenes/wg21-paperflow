#
# Copyright (c) 2026 Mungo Gill
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""cpp-mcp: MCP server for the C++ standard."""

from __future__ import annotations

from cpp_mcp.backend import (
    DefinedTermRow,
    DraftInfo,
    GrammarRuleRow,
    IndexTermRow,
    LibraryDeclRow,
    MechanismRow,
    ParagraphRow,
    SectionRow,
    StandardBackend,
)
from cpp_mcp.sqlite_backend import SqliteStandardBackend

__all__ = [
    "DefinedTermRow",
    "DraftInfo",
    "GrammarRuleRow",
    "IndexTermRow",
    "LibraryDeclRow",
    "MechanismRow",
    "ParagraphRow",
    "SectionRow",
    "SqliteStandardBackend",
    "StandardBackend",
]

__version__ = "0.2.0"
