#
# Copyright (c) 2026 Vinnie Falco (vinnie.falco@gmail.com)
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

"""CLI command module for 'paperflow status'."""

from __future__ import annotations


def command(args, backend):
    from paperstore.stages import STAGE_NAMES, failed_stage
    from cli.targets import MONTH_RE, resolve_pid

    target = args.targets[0] if args.targets else None

    if target is None:
        papers = [p for p in _all_papers(backend) if p.status < 6]
    elif MONTH_RE.match(target):
        papers = backend.list_papers_since(target)
    elif target.isdigit() and len(target) == 4:
        papers = backend.list_papers_for_year(target)
    else:
        pid = resolve_pid(target, backend)
        papers = [backend.get_meta(pid)]

    if not papers:
        print("No papers found.")
        return 0

    for p in sorted(papers, key=lambda p: (p.mailing_date or "", p.paper_id)):
        if p.status < 0:
            stage = failed_stage(p.status)
            label = f"FAILED at {STAGE_NAMES.get(stage, str(stage))}"
            if p.error:
                label += f": {p.error[:80]}"
        elif p.status >= 6:
            label = "ready"
        else:
            label = STAGE_NAMES.get(p.status, str(p.status))
        print(f"  {p.paper_id:<12} {label}")

    ready = sum(1 for p in papers if p.status >= 6)
    failed = sum(1 for p in papers if p.status < 0)
    active = len(papers) - ready - failed
    print(f"\n{len(papers)} papers: {ready} ready, {active} in progress, {failed} failed")
    return 0


def _all_papers(backend):
    ids = backend.list_all_paper_ids()
    return [backend.get_meta(pid) for pid in ids]
