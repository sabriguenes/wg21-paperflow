# Copyright 2026 The wg21-paperflow authors. All rights reserved.
# Use of this software is governed by the BSL-1.0 license found in LICENSE.
"""
Scan WG21 paperstore exports for Abstract section signals.

Prefer PDF (font + bbox via PyMuPDF). If no PDF, analyze Markdown body
and optionally HTML boilerplate.

Usage (from repo root):

  uv run --directory packages/tomd python scripts/abstract_section_inventory.py \\
      --paperstore "C:\\path\\to\\data\\paperstore" \\
      --output abstract_inventory_report.csv --workers 8

Writes CSV rows: one per paper id with available sources + detection fields.
Thread pool (--workers) parallelizes scans; deterministic for the same tree.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)

LABEL_STANDALONE = re.compile(
    r"^\s*(?P<body>abstract)\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
LABEL_NUMBERED_MD = re.compile(r"^\s*\d+\s*[\.)]\s*abstract\b", re.I)
LABEL_NUMBERED_HTML = LABEL_NUMBERED_MD
WORD_BOUNDARY_ABSTRACT = re.compile(r"\babstract\b", re.I)


@dataclass
class PdfHit:
    page: int
    line_kind: str
    full_line_preview: str
    span_text: str
    font: str
    size: float
    flags: int
    x0_frac: float | None
    y0_frac: float | None
    page_w: float
    page_h: float


def _classify_label_line(text: str) -> str | None:
    t = text.replace("\u200b", "").replace("\u00a0", " ").strip()
    if not t:
        return None
    if LABEL_STANDALONE.fullmatch(t):
        return "standalone_label"
    if re.match(r"^\s*\d+\s*[\.)]\s*abstract\s*:?\s*$", t, re.I):
        return "numbered_heading"
    if re.match(r"^\s*\d+\s+abstract\s*:?\s*$", t, re.I):
        return "numbered_heading"
    if re.match(r"^abstract\s*[:\.]?\s+\S", t, re.I):
        return "inline_with_text"
    if "abstract" in t.lower() and len(t) < 80:
        return "short_line_mentions_abstract"
    return None


def scan_pdf(path: Path, *, max_pages: int) -> tuple[list[PdfHit], bool]:
    import fitz

    hits: list[PdfHit] = []
    ok = False
    doc = fitz.open(path)
    try:
        ok = True
        for pno in range(min(max_pages, len(doc))):
            page = doc.load_page(pno)
            pw, ph = page.rect.width, page.rect.height
            blocks = page.get_text("dict").get("blocks") or []
            for blk in blocks:
                if blk.get("type") != 0:
                    continue
                for line in blk.get("lines") or []:
                    spans = line.get("spans") or []
                    if not spans:
                        continue
                    line_text = "".join(s.get("text", "") for s in spans)
                    kind = _classify_label_line(line_text)
                    if not kind:
                        continue
                    for sp in spans:
                        tx = (sp.get("text") or "").replace("\u200b", "")
                        if not WORD_BOUNDARY_ABSTRACT.search(tx):
                            continue
                        bbox = sp.get("bbox") or [0, 0, 0, 0]
                        x0, y0, x1, y1 = bbox
                        hits.append(
                            PdfHit(
                                page=pno,
                                line_kind=kind,
                                full_line_preview=line_text[:200].replace("\n", "\\n"),
                                span_text=(tx.strip())[:160],
                                font=sp.get("font") or "",
                                size=float(sp.get("size") or 0),
                                flags=int(sp.get("flags") or 0),
                                x0_frac=round(x0 / pw, 4) if pw else None,
                                y0_frac=round(y0 / ph, 4) if ph else None,
                                page_w=round(pw, 2),
                                page_h=round(ph, 2),
                            )
                        )
    finally:
        doc.close()
    return hits, ok


def scan_markdown(path: Path) -> dict[str, str | bool]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    fm = ""
    body = raw
    if raw.startswith("---"):
        sep = raw.find("\n---", 3)
        if sep != -1:
            fm_end = sep + 1 + 3  # newline + ---
            fm = raw[:fm_end]
            body = raw[fm_end:]
    patterns: list[str] = []
    b = body.lstrip("\n")
    if re.search(r"(?mi)^#\s+abstract\b", b):
        patterns.append("md_h1_abstract")
    if re.search(r"(?mi)^##\s+abstract\b", b):
        patterns.append("md_h2_abstract")
    if re.search(r"(?mi)^###\s+abstract\b", b):
        patterns.append("md_h3_abstract")
    # Standalone Abstract line before paragraph (classic WG21-ish)
    if re.search(r"(?mi)^abstract\s*:?\s*$", b):
        patterns.append("md_standalone_word")
    if re.search(r"(?mi)^\d+\s*[\.)]\s*abstract\b", b):
        patterns.append("md_numbered_heading")
    if re.search(r"(?mi)^\d+\s+abstract\b", b):
        patterns.append("md_numbered_heading_space")
    if "abstract:" in fm.lower():
        patterns.append("front_matter_has_abstract_key")
    snippet = ""
    preview = "\n".join(b.split("\n")[:30])
    m = WORD_BOUNDARY_ABSTRACT.search(preview or "")
    if m:
        s = max(0, m.start() - 40)
        e = min(len(preview), m.end() + 60)
        snippet = preview[s:e].replace("\n", " ")
    heading_like = (
        any(p.startswith("md_h") for p in patterns)
        or ("md_standalone_word" in patterns)
        or ("md_numbered_heading" in patterns)
        or ("md_numbered_heading_space" in patterns)
    )
    return {
        "md_patterns": ";".join(sorted(set(patterns))),
        "md_heading_signal": heading_like,
        "body_abstract_preview": snippet[:200],
        "has_word_abstract_in_body_opening": bool(
            WORD_BOUNDARY_ABSTRACT.search(preview or "")
        ),
    }


def scan_html(path: Path) -> dict[str, str]:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {"html_patterns": "", "html_error": "no_bs4"}

    html = path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    found: set[str] = set()
    for tag in soup.find_all(re.compile("^h[1-6]$", re.I)):
        t = tag.get_text(" ", strip=True)
        if re.match(r"^abstract$", t, re.I):
            found.add(f"html_{tag.name.lower()}_abstract")
        if LABEL_NUMBERED_HTML.match(t or ""):
            found.add(f"html_{tag.name.lower()}_numbered_abstract")
        if re.match(r"^\s*\d+\s+abstract\b", t or "", re.I):
            found.add(f"html_{tag.name.lower()}_numbered_abstract_space")
    for elem in soup.find_all(class_=re.compile(r"\babstract\b", re.I)):
        found.add("html_class_contains_abstract")
    for elem in soup.find_all(id=re.compile(r"abstract", re.I)):
        found.add("html_id_contains_abstract")
    snippet = ""
    raw_text = soup.get_text("\n")[:3500]
    m = WORD_BOUNDARY_ABSTRACT.search(raw_text or "")
    if m:
        s = max(0, m.start() - 30)
        e = min(len(raw_text), m.end() + 70)
        snippet = raw_text[s:e].replace("\n", " ")
    return {
        "html_patterns": ";".join(sorted(found)),
        "html_abstract_preview": snippet[:200],
    }


def collect_paper_inventory(store: Path) -> dict[str, dict[str, Path | None]]:
    inv: dict[str, dict[str, Path | None]] = defaultdict(lambda: {"pdf": None, "md": None, "html": None})
    for p in sorted(store.iterdir()):
        if not p.is_file():
            continue
        key = p.suffix.lower()
        if key not in (".pdf", ".md", ".html"):
            continue
        pid = p.stem.lower()
        kind = key.lstrip(".")
        inv[pid][kind] = p
    return dict(inv)


def analyze_paper_bundle(
    pid: str,
    sources: dict[str, Path | None],
    *,
    max_pdf_pages: int,
) -> dict[str, object]:
    pdf_hits: list[PdfHit] = []
    pdf_error = ""
    if sources.get("pdf") is not None:
        try:
            pdf_hits, _ = scan_pdf(sources["pdf"], max_pages=max_pdf_pages)
        except Exception as e:
            pdf_error = f"{type(e).__name__}: {e}"

    md_info: dict[str, str | bool] = {}
    if sources.get("md") is not None:
        try:
            md_info = scan_markdown(sources["md"])  # type: ignore[arg-type]
        except Exception as e:
            md_info = {
                "md_patterns": "",
                "md_heading_signal": False,
                "body_abstract_preview": "",
                "has_word_abstract_in_body_opening": False,
                "parse_error_md": repr(e),
            }

    html_info: dict[str, str] = {}
    if sources.get("html") is not None:
        try:
            html_info = scan_html(sources["html"])  # type: ignore[arg-type]
        except Exception as e:
            html_info = {
                "html_patterns": "",
                "html_abstract_preview": "",
                "parse_error_html": repr(e),
            }

    primary = "none"
    if sources.get("pdf"):
        primary = "pdf"
    elif sources.get("md"):
        primary = "markdown"
    elif sources.get("html"):
        primary = "html"

    standalone = [h for h in pdf_hits if h.line_kind == "standalone_label"]
    pick_pdf = standalone[0] if standalone else (pdf_hits[0] if pdf_hits else None)

    has_pdf_heading = any(
        h.line_kind in {"standalone_label", "numbered_heading", "inline_with_text"} for h in pdf_hits
    )
    has_md_heading = bool(md_info.get("md_heading_signal"))
    hp_tokens = [t for t in str(html_info.get("html_patterns", "")).split(";") if t]
    has_html_heading = bool(hp_tokens)

    consolidated = has_pdf_heading or has_md_heading or has_html_heading

    line_kinds_pdf = ";".join(sorted({h.line_kind for h in pdf_hits})) if pdf_hits else ""

    sources_bits = "".join(
        [
            ("P" if sources.get("pdf") else ""),
            ("M" if sources.get("md") else ""),
            ("H" if sources.get("html") else ""),
        ]
    )

    row: dict[str, object] = {
        "pid": pid,
        "sources_pmdh": sources_bits or "-",
        "primary_stored_source": primary,
        "pdf_span_hits": len(pdf_hits),
        "pdf_line_kind_set": line_kinds_pdf,
        "has_structured_heading": consolidated,
        "pdf_first_page": pick_pdf.page if pick_pdf else "",
        "pdf_label_font": pick_pdf.font if pick_pdf else "",
        "pdf_label_size_pt": round(pick_pdf.size, 3) if pick_pdf else "",
        "pdf_label_y_frac": pick_pdf.y0_frac if pick_pdf else "",
        "pdf_label_x_frac": pick_pdf.x0_frac if pick_pdf else "",
        "pdf_error": pdf_error,
        **{k: v for k, v in md_info.items()},
        **{k: v for k, v in html_info.items()},
    }
    return row


_STATIC_FIELDS_ORDER = (
    "pid",
    "sources_pmdh",
    "primary_stored_source",
    "pdf_span_hits",
    "pdf_line_kind_set",
    "has_structured_heading",
    "pdf_first_page",
    "pdf_label_font",
    "pdf_label_size_pt",
    "pdf_label_y_frac",
    "pdf_label_x_frac",
    "pdf_error",
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Abstract section inventory across paperstore media.")
    ap.add_argument(
        "--paperstore",
        type=Path,
        required=True,
        help="Paperstore directory (flat: <pid>.pdf|.md|.html).",
    )
    ap.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="CSV output path (default: paperstore/../abstract_inventory_report.csv)",
    )
    ap.add_argument("--max-pdf-pages", type=int, default=8)
    ap.add_argument("--workers", type=int, default=min(24, ((__import__("os").cpu_count() or 4) * 2)))
    ap.add_argument("--json-summary", action="store_true", help="Also write JSON summary beside CSV.")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    store = args.paperstore.resolve()
    if not store.is_dir():
        print(f"--paperstore is not a directory: {store}", file=sys.stderr)
        return 2

    inventory = collect_paper_inventory(store)
    if not inventory:
        print(f"No *.pdf|*.md|*.html files in {store}", file=sys.stderr)
        return 1

    out = args.output
    if out is None:
        out = store.parent / "abstract_inventory_report.csv"

    rows: list[dict[str, object]] = []
    pid_list = sorted(inventory.keys())

    if args.workers <= 1:
        for pid in pid_list:
            rows.append(
                analyze_paper_bundle(pid, inventory[pid], max_pdf_pages=args.max_pdf_pages)
            )
    else:
        with ThreadPoolExecutor(max_workers=max(2, args.workers)) as ex:
            futs = {
                ex.submit(
                    analyze_paper_bundle,
                    pid,
                    inventory[pid],
                    max_pdf_pages=args.max_pdf_pages,
                ): pid
                for pid in pid_list
            }
            for fu in as_completed(futs):
                rows.append(fu.result())

        rows.sort(key=lambda r: str(r["pid"]))

    dyn_keys: set[str] = set()
    for r in rows:
        dyn_keys.update(r.keys())

    fieldnames = [f for f in _STATIC_FIELDS_ORDER if f in dyn_keys]
    for k in sorted(dyn_keys):
        if k not in fieldnames:
            fieldnames.append(k)

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            flat = dict(r)
            for k in list(flat.keys()):
                v = flat[k]
                if isinstance(v, dict):
                    flat[k] = json.dumps(v, ensure_ascii=False)
            w.writerow(flat)

    n_pdf = sum(1 for pid in inventory if inventory[pid]["pdf"])
    n_md = sum(1 for pid in inventory if inventory[pid]["md"])
    n_html = sum(1 for pid in inventory if inventory[pid]["html"])
    headings = sum(1 for r in rows if r.get("has_structured_heading"))

    summary = {
        "paper_ids": len(inventory),
        "with_pdf": n_pdf,
        "with_md": n_md,
        "with_html": n_html,
        "structured_heading_yes": headings,
        "csv": str(out),
    }

    print(json.dumps(summary, indent=2))
    _log.info("Written %s", out)

    if args.json_summary:
        js = out.with_suffix(".summary.json")
        js.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
