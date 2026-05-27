#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

from __future__ import annotations

from pipeline.tools import inject_untrusted, _random_tag


def test_inject_untrusted_wraps_content():
    tag = "TESTTAG"
    result = inject_untrusted("body", tag)
    assert result == "<<<TESTTAG>>>\nbody\n<<<END_TESTTAG>>>"


def test_inject_untrusted_escapes_forged_delimiters():
    tag = "TEST"
    result = inject_untrusted("a <<<TEST>>> b <<<END_TEST>>> c", tag)
    assert result.startswith("<<<TEST>>>\n")
    assert result.endswith("\n<<<END_TEST>>>")
    inner = result.removeprefix("<<<TEST>>>\n").removesuffix("\n<<<END_TEST>>>")
    assert "<<<TEST>>>" not in inner
    assert "<<<END_TEST>>>" not in inner


def test_random_tag_is_alphanumeric():
    tag = _random_tag()
    assert len(tag) == 8
    assert tag.isalnum()


def test_random_tag_varies():
    tags = {_random_tag() for _ in range(10)}
    assert len(tags) > 1
