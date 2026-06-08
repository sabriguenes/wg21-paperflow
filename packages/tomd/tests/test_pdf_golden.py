"""Golden regression: full PDF papers vs committed expected Markdown."""

import difflib
import json
from pathlib import Path

import pytest

from tomd.lib.pdf import run_pipeline

_GOLDEN = Path(__file__).resolve().parent / "fixtures" / "golden"

_GOLDEN_STEMS = (
    "p0533r9",
    "p0957r8",
    "p1068r11",
    "p3556r0",
    "p1122r3",
    "p2040r0",
    "p3714r0",
    "p1112r4",
)


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _diff_head(actual: str, golden: str, limit: int = 120) -> str:
    a_lines = _normalize_newlines(actual).splitlines(keepends=True)
    b_lines = _normalize_newlines(golden).splitlines(keepends=True)
    diff = difflib.unified_diff(
        b_lines, a_lines, fromfile="golden", tofile="actual", n=3,
    )
    return "".join(list(diff)[:limit])


@pytest.mark.parametrize("stem", _GOLDEN_STEMS)
def test_run_pipeline_matches_golden(stem: str):
    pdf_path = _GOLDEN / f"{stem}.pdf"
    if not pdf_path.is_file():
        pytest.skip(f"missing PDF fixture: {pdf_path}")

    result = run_pipeline(pdf_path)
    md, prompts = result.md, result.prompts
    golden_md = _GOLDEN / f"{stem}.golden.md"
    assert golden_md.is_file(), f"missing golden: {golden_md}"
    expected_md = golden_md.read_text(encoding="utf-8")
    got_md = _normalize_newlines(md)
    exp_md = _normalize_newlines(expected_md)
    if got_md != exp_md:
        pytest.fail(
            f"Markdown mismatch for {stem}\n{_diff_head(md, expected_md)}",
        )

    golden_prompts = _GOLDEN / f"{stem}.golden.prompts.json"
    if golden_prompts.is_file():
        assert prompts is not None, f"expected prompts for {stem}"
        expected = json.loads(golden_prompts.read_text(encoding="utf-8"))
        assert isinstance(expected, list)
        got = [_normalize_newlines(p) for p in prompts]
        exp = [_normalize_newlines(p) for p in expected]
        if got != exp:
            joined_got = "\n---\n".join(got)
            joined_exp = "\n---\n".join(exp)
            pytest.fail(
                f"Prompts mismatch for {stem}\n{_diff_head(joined_got, joined_exp)}",
            )
    else:
        assert prompts is None, f"unexpected prompts for {stem}: {prompts}"
