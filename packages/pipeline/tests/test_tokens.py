#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0.
#

from __future__ import annotations

from pipeline.tokens import CHARS_PER_TOKEN, est_tokens, tokens_to_chars


class TestCharsPerToken:
    def test_constant_is_conservative(self):
        assert CHARS_PER_TOKEN < 3.5

    def test_constant_is_positive(self):
        assert CHARS_PER_TOKEN > 0


class TestEstTokens:
    def test_empty_string(self):
        assert est_tokens("") == 1

    def test_short_text(self):
        result = est_tokens("hello")
        assert result == 1

    def test_known_length(self):
        text = "a" * 325
        assert est_tokens(text) == 100

    def test_never_zero(self):
        assert est_tokens("x") >= 1

    def test_scales_with_length(self):
        short = est_tokens("hello world")
        long = est_tokens("hello world " * 100)
        assert long > short

    def test_with_agent_override(self):
        class FakeAgent:
            chars_per_token = 4.0

        text = "a" * 400
        assert est_tokens(text, agent=FakeAgent()) == 100

    def test_without_agent_uses_default(self):
        text = "a" * 325
        assert est_tokens(text) == 100
        assert est_tokens(text, agent=None) == 100


class TestTokensToChars:
    def test_zero_tokens(self):
        assert tokens_to_chars(0) == 0

    def test_known_conversion(self):
        assert tokens_to_chars(2000) == 6500

    def test_small_value(self):
        assert tokens_to_chars(64) == 208

    def test_with_agent_override(self):
        class FakeAgent:
            chars_per_token = 4.0

        assert tokens_to_chars(100, agent=FakeAgent()) == 400

    def test_without_agent_uses_default(self):
        assert tokens_to_chars(100) == 325
        assert tokens_to_chars(100, agent=None) == 325

    def test_inverse_of_est_tokens(self):
        chars = tokens_to_chars(1000)
        text = "x" * chars
        tokens_back = est_tokens(text)
        assert tokens_back == 1000
