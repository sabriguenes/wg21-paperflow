#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

"""Canonical token estimation for LLM context budgets.

All token-to-character conversion in the repository uses the constant
and functions defined here. Do not use words, lines, or ad-hoc
multipliers elsewhere.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipeline.agents import AgentBackend

CHARS_PER_TOKEN: float = 3.25
"""Conservative characters-per-token for modern BPE tokenizers.

Lower than the empirical ratios so that token estimates never overfill
a model's context window. Calibrated against WG21 papers (English prose
with embedded C++ code) via study/token-ratio/:

  Claude Opus 4.6:    3.51 measured (use 3.25 conservative)
  Qwen3 (32B/235B):  4.05 measured (use 4.0 conservative)
  DeepSeek-R1-70B:   4.20 measured (use 4.0 conservative)

Open-weight models show stdev ~0.35-0.70 across 256-token windows,
with code-heavy chunks dropping to ~2.5 chars/token and prose-heavy
sections reaching ~5.5. Per-model values are set in SERVICES.toml
via chars_per_token; this constant is the fallback when no agent is
available.
"""


def est_tokens(text: str, *, agent: "AgentBackend | None" = None) -> int:
    """Estimate token count from text length.

    Uses the agent's calibrated chars_per_token if provided,
    otherwise falls back to the global CHARS_PER_TOKEN constant.
    """
    cpt = agent.chars_per_token if agent else CHARS_PER_TOKEN
    return max(1, int(len(text) / cpt))


def tokens_to_chars(tokens: int, *, agent: "AgentBackend | None" = None) -> int:
    """Convert a token budget to a character budget.

    Uses the agent's calibrated chars_per_token if provided,
    otherwise falls back to the global CHARS_PER_TOKEN constant.
    """
    cpt = agent.chars_per_token if agent else CHARS_PER_TOKEN
    return int(tokens * cpt)
