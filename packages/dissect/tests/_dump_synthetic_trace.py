"""Run the synthetic pipeline once and write the full trace to disk.

Not a pytest module (leading underscore keeps it out of collection).
Run with::

    uv run python packages/dissect/tests/_dump_synthetic_trace.py

Writes ``packages/dissect/tests/fixtures/synthetic_paper.trace.md``
(gitignored via the fixtures directory) so the trace survives the
tempdir cleanup that the integration test relies on.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

import anyio

sys.path.insert(0, str(Path(__file__).parent))

from test_synthetic_pipeline import (  # noqa: E402
    PAPER_ID,
    _seed_backend,
)

from dissect.models import PipelineState  # noqa: E402
from dissect.pdf_extract import extract_pdf_text  # noqa: E402
from dissect.pipeline import _HOOKS  # noqa: E402
from dissect.render import render_trace  # noqa: E402
from paperstore.tools import PaperstoreTools  # noqa: E402
from pipeline import (  # noqa: E402
    DEFAULT_MODEL_SLOTS,
    StepContext,
    WebResearcher,
    build_pipeline,
    dispatch,
    load_sections,
)


_TRACE_PATH = Path(__file__).parent / "fixtures" / "synthetic_paper.trace.md"


async def _main() -> None:
    with tempfile.TemporaryDirectory(
        prefix="dissect-trace-", ignore_cleanup_errors=True,
    ) as tmp:
        backend = _seed_backend(Path(tmp))
        try:
            slots = dict(DEFAULT_MODEL_SLOTS)
            secs = dict(load_sections("dissect", "dissect.md"))
            pipeline = build_pipeline(secs, _HOOKS)

            paper_md = backend.get_paper_md(PAPER_ID)
            backend.clear_dissect(PAPER_ID)
            state = PipelineState(paper_source=paper_md)
            meta = backend.get_meta(PAPER_ID)

            async with WebResearcher(
                binary_extractors={"application/pdf": extract_pdf_text},
            ) as researcher:
                ps_tools = PaperstoreTools(backend)
                tool_reg: dict[str, Any] = {
                    "paper_meta": ps_tools.paper_meta,
                    "paper_meta_latest": ps_tools.paper_meta_latest,
                    "read_file": ps_tools.read_file,
                    "deep_search": researcher.deep_search,
                    "web_search": researcher.web_search,
                    "web_fetch": researcher.web_fetch,
                }
                ctx = StepContext(
                    sections=secs,
                    model_slots=slots,
                    researcher=researcher,
                    backend=backend,
                    debug=False,
                    pid=PAPER_ID,
                    tool_registry=tool_reg,
                )

                await dispatch(
                    pipeline, state, ctx,
                    trace_path=_TRACE_PATH,
                    render_trace_fn=lambda st, step: render_trace(st, meta, step),
                )
        finally:
            backend.close()

    print(f"Wrote {_TRACE_PATH}")


if __name__ == "__main__":
    anyio.run(_main)
