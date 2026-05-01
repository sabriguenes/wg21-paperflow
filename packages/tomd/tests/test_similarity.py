"""Tests for lib.similarity."""

from tomd.lib.similarity import (
    _MAX_COMPARE_LENGTH,
    _symmetric_similarity,
    fuzzy_match_label,
    similar,
)


def test_similar_identical():
    assert similar("hello world", "hello world")


def test_similar_minor_difference():
    assert similar("hello world", "hello worlds")


def test_similar_unrelated():
    assert not similar("hello world", "xyzzy foobar quux")


def test_similar_empty_strings():
    assert similar("", "")


def test_similar_one_empty():
    assert not similar("hello", "")


def test_similar_circuit_breaker():
    assert not similar("a" * (_MAX_COMPARE_LENGTH + 1), "b" * (_MAX_COMPARE_LENGTH + 1))


def test_similar_long_identical():
    assert similar("a" * (_MAX_COMPARE_LENGTH + 50), "a" * (_MAX_COMPARE_LENGTH + 50))


def test_similar_short_identical():
    assert similar("test", "test")


def test_similar_disjoint_words():
    assert not similar("aaa bbb", "ccc ddd")


# -- fuzzy_match_label tests --------------------------------------------------

_KNOWN = [
    "document number", "doc no", "title", "date", "audience", "subgroup",
    "reply-to", "reply to", "author", "authors", "editor", "editors",
    "co-author", "co-authors", "revision date",
]


def test_fuzzy_match_repy_to():
    assert fuzzy_match_label("repy-to", _KNOWN) == "reply-to"


def test_fuzzy_match_auther():
    assert fuzzy_match_label("auther", _KNOWN) == "author"


def test_fuzzy_match_editros():
    assert fuzzy_match_label("editros", _KNOWN) == "editors"


def test_fuzzy_match_documnet_number():
    assert fuzzy_match_label("documnet number", _KNOWN) == "document number"


def test_fuzzy_match_exact_returns_exact():
    assert fuzzy_match_label("reply-to", _KNOWN) == "reply-to"


def test_fuzzy_match_rejects_garbage():
    assert fuzzy_match_label("foobar", _KNOWN) is None


def test_fuzzy_match_rejects_unrelated_short():
    assert fuzzy_match_label("note", _KNOWN) is None


def test_fuzzy_match_empty_candidate():
    assert fuzzy_match_label("", _KNOWN) is None


def test_fuzzy_match_empty_known():
    assert fuzzy_match_label("reply-to", []) is None


def test_fuzzy_match_case_insensitive():
    assert fuzzy_match_label("Reply-To", _KNOWN) == "reply-to"


def test_symmetric_similarity_is_symmetric():
    a, b = "repy-to", "reply-to"
    assert _symmetric_similarity(a, b) == _symmetric_similarity(b, a)
