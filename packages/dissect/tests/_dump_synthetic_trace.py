"""Run the synthetic pipeline once and write the full trace to disk.

Not a pytest module (leading underscore keeps it out of collection).
Run with::

    uv run python packages/dissect/tests/_dump_synthetic_trace.py

Preconditions (changed from the pre-f8f6aa0 version):

* ``SERVICES.toml`` at the repo root must declare ``[services.NAME]``,
  ``[transformer_providers.NAME]``, and ``[classifiers.NAME]`` slots
  that resolve. ``load_services`` raises if the file is missing or
  references unset env-var API keys.
* First run downloads the configured embedding model
  (``BAAI/bge-small-en-v1.5``, ~120 MB) and classifier weights to the
  HF cache. Subsequent runs are offline.
* Transformer-provider auto-detection (cuda > mps > cpu) runs at
  startup; on a CPU-only box this is a no-op but on a CUDA host the
  script binds a device context.

Runs all 17 dissect steps including Step 1 (Tag Sentences); the trace
captures every step's per-LLM and per-classifier output. Writes
``packages/dissect/tests/fixtures/synthetic_paper.trace.md``
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
from dissect.pipeline import _build_hooks  # noqa: E402
from dissect.render import render_trace  # noqa: E402
from paperstore.tools import PaperstoreTools  # noqa: E402
from pipeline import (  # noqa: E402
    AgentBackend,
    StepContext,
    WebResearcher,
    build_pipeline,
    dispatch,
    load_classifiers,
    load_sections,
    load_services,
    load_transformer_providers,
    resolve_classifier_slots,
    resolve_slots,
    resolve_transformer_provider,
)


_TRACE_PATH = Path(__file__).parent / "fixtures" / "synthetic_paper.trace.md"


async def _main() -> None:
    with tempfile.TemporaryDirectory(
        prefix="dissect-trace-", ignore_cleanup_errors=True,
    ) as tmp:
        backend = _seed_backend(Path(tmp))
        try:
            services, defaults = load_services()
            slots = resolve_slots(services, defaults)

            providers, provider_defaults = load_transformer_providers()
            provider = resolve_transformer_provider(providers, provider_defaults)

            classifiers, classifier_defaults = load_classifiers(provider=provider)
            classifier_slots = resolve_classifier_slots(classifiers, classifier_defaults)

            extraction_agent = AgentBackend(
                slots.get("fast", slots["default"]), thinking_budget=2048,
            )
            synthesis_agent = AgentBackend(slots["default"], thinking_budget=4096)
            research_agent = AgentBackend(slots.get("tool", slots["default"]))
            agents = {
                "fast": extraction_agent,
                "default": synthesis_agent,
                "tool": research_agent,
            }

            secs = dict(load_sections("dissect", "dissect.md"))
            hooks = _build_hooks(extraction_agent, synthesis_agent, research_agent)
            pipeline = build_pipeline(secs, hooks)

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
                    agents=agents,
                    classifiers=classifier_slots,
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
