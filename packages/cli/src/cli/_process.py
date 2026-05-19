#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""Shared process_paper driver for all CLI verb commands."""

from __future__ import annotations

import argparse
import asyncio
import sys

from paperstore.backend import StorageBackend
from paperstore.errors import MissingMetaError
from paperstore.stages import STAGE_NAMES

from cli.targets import MONTH_RE, resolve_pid


def _parse_service_overrides(raw: list[str] | None) -> dict[str, str] | None:
    """Parse ``--service`` flag values into a slot -> service-name dict.

    A bare ``NAME`` (no ``=``) applies to all default slots.
    ``SLOT=NAME`` overrides one slot.
    """
    if not raw:
        return None
    overrides: dict[str, str] = {}
    for item in raw:
        if "=" in item:
            slot, name = item.split("=", 1)
            overrides[slot.strip()] = name.strip()
        else:
            overrides["fast"] = item
            overrides["default"] = item
            overrides["tool"] = item
    return overrides


def _parse_classifier_overrides(raw: list[str] | None) -> dict[str, str] | None:
    """Parse ``--classifier`` flag values into a slot -> classifier-name dict.

    A bare ``NAME`` (no ``=``) applies to the default ``selector`` slot.
    ``SLOT=NAME`` overrides one slot. Mirrors :func:`_parse_service_overrides`.
    """
    if not raw:
        return None
    overrides: dict[str, str] = {}
    for item in raw:
        if "=" in item:
            slot, name = item.split("=", 1)
            overrides[slot.strip()] = name.strip()
        else:
            overrides["selector"] = item
    return overrides


def run_process_command(
    args: argparse.Namespace,
    backend: StorageBackend,
    *,
    through: int,
) -> int:
    """Run process_paper for the given target through the given stage."""
    from pipeline import process_paper, PipelineError
    from cli.progress import make_progress_handler
    import pydantic_ai.exceptions

    target = args.targets[0]
    debug = getattr(args, "debug", False)
    trace = getattr(args, "trace", False)
    step_val = getattr(args, "step", None)
    if step_val is not None:
        trace = True
    stop_after = step_val
    chunk_index = getattr(args, "chunk", None)
    force = getattr(args, "force", False)
    keep_downstream = getattr(args, "keep_downstream", False)
    skip_prompt = getattr(args, "yes", False)
    service_overrides = _parse_service_overrides(getattr(args, "service", None))
    classifier_overrides = _parse_classifier_overrides(getattr(args, "classifier", None))
    provider_override = getattr(args, "provider", None)

    verb = STAGE_NAMES.get(through - 1, "process")

    if MONTH_RE.match(target):
        papers = backend.list_papers_since(target)
    elif target.isdigit() and len(target) == 4:
        papers = backend.list_papers_for_year(target)
    else:
        pid = resolve_pid(target, backend)
        try:
            paper = backend.get_meta(pid)
        except MissingMetaError as exc:
            print(f"{verb} failed: {exc}", file=sys.stderr)
            return 1
        papers = [paper]

    if not force:
        papers = [p for p in papers if p.status < through]

    if not papers:
        print("No papers need processing.")
        return 0

    papers.sort(key=lambda p: (p.mailing_date or ""), reverse=True)
    papers.sort(key=lambda p: -(p.status or 0))

    # Pre-batch confirmation gate (plan section 3.1). For multi-paper
    # convert invocations, count how many papers would have their
    # downstream artifacts invalidated. The trigger is the upper bound
    # "has paper.md AND has dissect/advocatus/agora"; we can't predict
    # whether the convert will produce different markdown bytes, so we
    # treat all existing-markdown papers as "might change". --yes /
    # non-TTY / single-paper / --keep-downstream all skip the prompt.
    if (
        verb == "convert"
        and len(papers) > 1
        and not keep_downstream
        and not skip_prompt
        and sys.stdin.isatty()
    ):
        dissect_n = sum(1 for p in papers if p.markdown_path and p.dissect_path)
        advocatus_n = sum(
            1 for p in papers if p.markdown_path and p.advocatus_path
        )
        agora_n = sum(1 for p in papers if p.markdown_path and p.agora_path)
        affected = sum(
            1 for p in papers
            if p.markdown_path and (
                p.dissect_path or p.advocatus_path or p.agora_path
            )
        )
        if affected > 0:
            print(
                f"convert: this batch will invalidate downstream artifacts "
                f"for {affected} paper(s)"
            )
            if dissect_n:
                print(f"  - dissect outputs:   {dissect_n} papers")
            if advocatus_n:
                print(f"  - advocatus outputs: {advocatus_n} papers")
            if agora_n:
                print(f"  - agora outputs:     {agora_n} papers")
            print("re-run those pipelines after convert finishes to refresh.")
            try:
                answer = input("Continue? [y/N] ").strip().lower()
            except EOFError:
                answer = ""
            if answer != "y":
                print("aborted.")
                return 0

    progress_ctx, on_progress = make_progress_handler(verb.capitalize())

    from paperstore.progress import ProgressEvent

    failed = 0
    total = len(papers)
    last_result = None
    # Per-paper convert telemetry, rolled up into the end-of-batch
    # summaries below. (pid, ConvertReport) pairs - one entry per
    # paper whose convert stage actually ran.
    convert_reports: list = []
    with progress_ctx:
        for i, paper in enumerate(papers):
            if on_progress:
                on_progress(ProgressEvent(
                    step=i, total=total,
                    name=f"{paper.paper_id} - {verb}",
                    pct=i / total if total else 1.0,
                ))
            try:
                last_result = asyncio.run(
                    process_paper(
                        paper.paper_id, backend,
                        through=through,
                        debug=debug,
                        trace=trace,
                        stop_after=stop_after,
                        chunk_index=chunk_index,
                        service_overrides=service_overrides,
                        classifier_overrides=classifier_overrides,
                        provider_override=provider_override,
                        force=force,
                        keep_downstream=keep_downstream,
                        on_progress=on_progress,
                    )
                )
                if last_result is not None and last_result.convert_report is not None:
                    convert_reports.append((paper.paper_id, last_result.convert_report))
            except PipelineError as exc:
                print(f"{paper.paper_id}: {exc}", file=sys.stderr)
                failed += 1
            except pydantic_ai.exceptions.UsageLimitExceeded as exc:
                print(f"{paper.paper_id}: LLM usage limit ({exc})", file=sys.stderr)
                failed += 1
            except Exception as exc:
                msg = f"{paper.paper_id}: {type(exc).__name__}: {exc}"
                cause = exc.__cause__
                while cause:
                    msg += f"\n  Caused by: {type(cause).__name__}: {cause}"
                    cause = cause.__cause__
                print(msg, file=sys.stderr)
                failed += 1

    if on_progress:
        on_progress(ProgressEvent(
            step=total, total=total, name="done", pct=1.0,
        ))

    # End-of-batch convert summaries (plan section 3.1). Two
    # independently-printed blocks: papers truncated to the 20-image
    # cap, and papers whose downstream artifacts were invalidated.
    # Suppressed for verbs other than convert (other stages don't
    # produce ConvertReport).
    truncated = [(pid, r) for pid, r in convert_reports if r.images_truncated]
    if truncated:
        print(
            f"\nconvert: {len(truncated)} paper(s) truncated to the "
            f"{truncated[0][1].images_kept}-image cap:"
        )
        for pid, r in truncated:
            print(f"  {pid} (kept {r.images_kept} of {r.source_image_count})")
        print(
            "  hint: vector diagrams and scanned-page PDFs are not handled "
            "in v1 - see `improvements.md` section 4.\n"
            "  The dropped images are noted in each paper.md as an HTML comment."
        )
    invalidated = [(pid, r) for pid, r in convert_reports if r.downstream_cleared]
    if invalidated:
        print(
            f"\nconvert: {len(invalidated)} paper(s) had downstream artifacts "
            f"invalidated by re-convert:"
        )
        for pid, r in invalidated:
            print(f"  {pid} ({', '.join(r.downstream_cleared)})")
        print(
            "  re-run `paperflow dissect` / `paperflow advocatus` / "
            "`paperflow agora` to refresh."
        )

    ok = total - failed
    if total > 1:
        print(f"{ok} succeeded, {failed} failed out of {total} papers")
    elif failed == 0 and total == 1:
        pid = papers[0].paper_id
        stage_label = STAGE_NAMES.get(through - 1, "done")
        # last_result may be missing only when process_paper itself
        # raised; that path goes through ``failed += 1`` above, so we
        # are guaranteed a result here. Fall back defensively anyway.
        ran = getattr(last_result, "stages_run", None)
        if ran is not None and not ran:
            print(f"{pid}: already at {stage_label} (nothing to do)")
        else:
            print(f"{pid}: {stage_label}")

    return 1 if failed else 0
