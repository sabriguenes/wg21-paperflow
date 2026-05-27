# tomd Improvement Plan: HuggingFace Model Integration

A research report on augmenting tomd's deterministic PDF/HTML-to-Markdown pipeline with HuggingFace zero-shot transformers and layout-aware models. Five parallel investigations were conducted covering NLI text classifiers, layout-aware vision models, HTML/DOM classification, wording (ins/del) detection, and the broader PDF-to-Markdown ecosystem. This document consolidates the findings, ranks integration opportunities under tomd's operating constraints, and defines concrete experiments with success metrics.

- **Audience:** tomd lead developer, implementing agents, anyone evaluating whether these integrations should be built.
- **Companion docs:** [CLAUDE.md](src/tomd/CLAUDE.md) (architecture rules), [README.md](README.md) (usage), [proposal-requirements.md](../../proposal-requirements.md) (the instrument that consumes tomd output).
- **Scope:** research findings and integration strategy. Not a specification.

---

## 1. Operating Constraints

tomd runs in the cloud. It is not open source. These constraints govern every recommendation in this document:

1. **Accuracy is the primary objective.** Every integration is ranked by its expected improvement to conversion correctness. Performance is secondary.
2. **Cloud deployment with GPU access.** Single A100/H100 per conversion job is the assumed baseline. CPU-only is not a constraint.
3. **License is not a constraint.** AGPL, GPL, CC-BY-NC, OpenRAIL revenue caps are all acceptable for a closed-source cloud service that does not redistribute weights.
4. **Deterministic core is preserved.** MuPDF + spatial rawdict dual extraction remains the source of truth for text. Models contribute structural opinions (bounding boxes, labels, confidence scores) that enter the existing multi-signal ensemble. Models never produce text that enters the output.
5. **Existing `Confidence` enum contract is preserved.** `HIGH`, `MEDIUM`, `LOW`, `UNCERTAIN` in [types.py](src/tomd/lib/pdf/types.py) remain the output contract. Model signals raise or lower confidence; they do not replace the enum.

---

## 2. Current Architecture (Reference)

Pipeline execution order from [CLAUDE.md](src/tomd/CLAUDE.md), with file locations:

| Stage | Description | File |
|---|---|---|
| 1 | Per-page dual extract (MuPDF dict + spatial rawdict) | [extract.py](src/tomd/lib/pdf/extract.py) |
| 2 | Close document | [lib/pdf/\_\_init\_\_.py](src/tomd/lib/pdf/__init__.py) |
| 3 | Slide-deck detection (early exit) | [lib/pdf/\_\_init\_\_.py](src/tomd/lib/pdf/__init__.py) |
| 4 | Standards-draft detection (early exit >= 200 pages) | [lib/pdf/\_\_init\_\_.py](src/tomd/lib/pdf/__init__.py) |
| 5 | Hidden block stripping + readability check | [cleanup.py](src/tomd/lib/pdf/cleanup.py) |
| 6 | Header/footer detection and stripping | [cleanup.py](src/tomd/lib/pdf/cleanup.py) |
| 7 | Monospace propagation | [mono.py](src/tomd/lib/pdf/mono.py) |
| 8 | Wording detection (HSV color + strikethrough) | [wording.py](src/tomd/lib/pdf/wording.py) |
| 9 | Text cleanup (NBSP, dehyphenation, cross-page join) | [cleanup.py](src/tomd/lib/pdf/cleanup.py) |
| 10 | Span normalization | [spans.py](src/tomd/lib/pdf/spans.py) |
| 11 | WG21 metadata extraction | [structure.py](src/tomd/lib/pdf/structure.py) |
| 12 | Table detection | [table.py](src/tomd/lib/pdf/table.py) |
| 13 | Dual-path comparison -> Sections | [structure.py](src/tomd/lib/pdf/structure.py) |
| 14 | Merge table sections | [structure.py](src/tomd/lib/pdf/structure.py) |
| 15 | Structure (headings, lists, paragraphs, code, wording) | [structure.py](src/tomd/lib/pdf/structure.py) |
| 16 | TOC stripping | [toc.py](src/tomd/lib/toc.py) |
| 17 | Emit markdown + optional prompts.json | [emit.py](src/tomd/lib/pdf/emit.py) |

Key data types from [types.py](src/tomd/lib/pdf/types.py):

- `Confidence` enum: `HIGH`, `MEDIUM`, `LOW`, `UNCERTAIN`
- `SectionKind` enum: `TITLE`, `METADATA`, `HEADING`, `PARAGRAPH`, `LIST`, `CODE`, `TABLE`, `UNCERTAIN`, `WORDING`, `WORDING_ADD`, `WORDING_REMOVE`
- `Section` dataclass: carries `kind`, `text`, `confidence`, `heading_level`, `lines`, `mupdf_text`, `spatial_text`, `page_num`, `font_size`, `metadata`, `columns`, `fence_lang`, `indent_level`
- `Span` dataclass: carries `text`, `font_name`, `font_size`, `bold`, `italic`, `monospace`, `bbox`, `origin`, `color`, `link_url`, `wording_role`

Today every signal is deterministic: font size ratios, section numbering regex, geometry, dual-path agreement, HSV color thresholds. The recommendations below add model-derived signals into this ensemble without replacing any deterministic signal.

---

## 3. The Strategic Finding

No off-the-shelf converter (Marker, Nougat, MinerU, olmOCR, Docling, SmolDocling, GOT-OCR2) handles the combination of problems tomd solves:

- **ins/del wording** (green/red text, strikethrough detection) -- every surveyed tool either normalizes span colors away or rasterizes the page, losing the signal entirely.
- **Verbatim code preservation** -- generative models (Nougat, olmOCR, GOT-OCR2) hallucinate tokens in C++ code listings. Nougat's repetition/degeneration on out-of-distribution documents is well-documented (arXiv 2308.13418, section 5.4).
- **Font/color metadata preservation** -- tomd's `Span` dataclass carries `font_name`, `font_size`, `bold`, `italic`, `monospace`, `color`, `wording_role` through the entire pipeline. Generative models discard all of this.
- **Dual-path confidence scoring** -- the disagreement between MuPDF and spatial paths is the confidence mechanism. No surveyed tool has an equivalent.

Therefore the integration shape is never "let model X produce the Markdown." It is always "let model X cast an additional vote into the existing multi-signal confidence step." Models contribute typed bounding boxes, structural labels, and confidence scores. Text extraction stays with MuPDF.

---

## 4. Research Area 1: Layout-Aware Region Detection (Third Extraction Path)

### 4.1 Problem

tomd's dual-path comparison produces `UNCERTAIN` sections when MuPDF and spatial paths disagree on block boundaries. A third independent signal from a completely different modality (rendered page image) would break ties and raise confidence on regions that today require LLM reconciliation.

### 4.2 Models Surveyed

| Model | HF ID | Params | License | Task | Zero-shot | Output | VRAM | Hallucination |
|---|---|---|---|---|---|---|---|---|
| Docling layout | `ds4sd/docling-layout-heron` | ~43M | Apache-2.0 | Region detection (RT-DETR-v2) | Yes | Typed bboxes | <2 GB | Low |
| Docling TableFormer | `ds4sd/docling-models` | ~200M | Apache-2.0 | Table cell structure | Yes | Cell grid | ~2 GB | Low |
| MinerU 2.5 layout | `opendatalab/PDF-Extract-Kit-1.0` | ~1-2B agg. | AGPL-3.0 | Region detection | Yes | Typed bboxes + reading order | 6-8 GB | Low-Med |
| Surya layout | `datalab-to/surya_layout` | ~80M | GPL/OpenRAIL-M | Region detection + reading order | Yes | Typed bboxes + labels + order | ~1 GB | Low |
| DocLayout-YOLO | `juliozhao/DocLayout-YOLO-DocStructBench` | ~25M | AGPL (ultralytics) | Region detection | Yes | Typed bboxes | <1 GB | Low |
| Surya table | `datalab-to/table_recognition` | ~80M | GPL/OpenRAIL-M | Row/col detection | Yes | Cell bboxes | ~1 GB | Low |
| Florence-2 | `microsoft/Florence-2-large` | 770M | MIT | Multitask VLM (OCR with region) | Yes | Quad bboxes + text | 3-4 GB | Med |
| Nougat | `facebook/nougat-base` | 350M | CC-BY-NC-4.0 | Image to Markdown | Yes (arXiv-like) | Page-level Markdown | ~3 GB | **High** |
| Marker | `datalab-to/marker` (Surya) | ~1B agg. | GPL-3.0 | Pipeline to Markdown | Yes | Block-level JSON + MD | 4-8 GB | Low-Med |
| olmOCR-2 | `allenai/olmOCR-7B-*` | 7B | Apache-2.0 | Image to Markdown | Yes | Page-level Markdown | 16-24 GB | Med |
| SmolDocling | `docling-project/SmolDocling-256M-preview` | 256M | Apache-2.0 | DocTags to Markdown | Yes | DocTags + MD | CPU-feasible | Med |
| GOT-OCR2 | `stepfun-ai/GOT-OCR-2.0-hf` | 580M | Apache-2.0 | Image to text/MD/LaTeX | Yes | Page-level string | ~3 GB | Med |
| Qwen2-VL | `Qwen/Qwen2-VL-7B-Instruct` | 7B (2B variant) | Apache-2.0 | General VLM | Yes | Free-form text | 16+ GB (7B) | Med-High |

### 4.3 Recommended Approach: Multi-Model Layout Ensemble

Run two or three layout-only detectors per page in parallel and require quorum. These models emit typed bounding boxes from rendered page images — a completely independent signal source from MuPDF's font metadata and spatial rawdict glyph positions.

**Primary candidates (run in parallel):**

1. **Docling layout** (`ds4sd/docling-layout-heron`) -- Apache-2.0, ~43M params, runs on CPU, local model with no cloud dependency. Labels: `section-header`, `text`, `code`, `table`, `page-header`, `page-footer`, `formula`, `caption`, `footnote`, `list-item`, `picture`. Library-callable via `docling-ibm-models`; outputs plain bounding boxes with labels.

2. **MinerU 2.5 layout** (`opendatalab/PDF-Extract-Kit-1.0`) -- best OmniDocBench layout score (97.5 mAP vs Docling 93.1). AGPL is acceptable. Higher VRAM but better accuracy.

3. **Surya layout** (`datalab-to/surya_layout`) -- Segformer-based, ~80M params. Independent vendor (datalab) from Docling (IBM). Labels include `Title`, `SectionHeader`, `Text`, `Code`, `Table`, `Formula`, `Caption`, `Footnote`, `ListItem`, `PageHeader`, `Picture`, `TextInlineMath`. Also emits reading order, which is the strongest signal for multi-column pages where MuPDF's natural order is unreliable.

**Integration point:** between stages 12 and 13 (after table detection, before dual-path comparison). Per page, render at 150-200 DPI, run layout detector(s), get `[(bbox, label, reading_order)]`. Compute IoU against MuPDF dict-blocks and spatial-reconstruction blocks. Promote the existing dual-path comparison in [structure.py](src/tomd/lib/pdf/structure.py) to an N-way agreement vector:

```
(mupdf_block_id, spatial_block_id, layout_label, layout_bbox)
```

**Confidence mapping:**
- 3+ signals agree on boundary AND label: `HIGH`
- 2 agree, 1 dissents on label: `MEDIUM`
- Boundary agreement but label conflict (e.g. spatial says `TEXT`, layout says `CODE`): `LOW`
- Boundary disagreement: `UNCERTAIN` (escalate to VLM judge)

**Reading order from layout model:** use as a tiebreaker against MuPDF's natural order on multi-column or sidebar pages. This is where MuPDF most often gets it wrong on WG21 papers.

**What the layout model does NOT do:** produce text. Keep text extraction strictly with MuPDF (born-digital PDFs give exact glyph data). The third path contributes geometry + typed labels + reading order, not characters. Hallucination risk stays low.

**Forward integration with the figure extractors.** Two figure-extraction paths ship today, both producing `SectionKind.IMAGE` sections with regex-derived alt text:

- The Resource-Dictionary raster path in [`tomd/lib/pdf/images.py`](src/tomd/lib/pdf/images.py) (v1) reads embedded raster XObjects.
- The vector-clustering path in [`tomd/lib/pdf/vector_images.py`](src/tomd/lib/pdf/vector_images.py) (v2, opt-in via `--extract-vector-images`) groups page drawing operators into figure candidates, filters decoration, and rasterises survivors. Heuristic by design; surfaces a per-paper `tomd:vector-extraction-uncertain` HTML marker disclosing what got rejected.

When the layout-aware path lands, four integration points open up:

- **a.** IoU each `picture` bbox from the layout model against `ExtractedImage.bbox` records. A match raises figure confidence and contributes the `picture` signal to the N-way agreement vector.
- **b.** Treat `picture` bboxes with **no** embedded-raster match as vector-diagram candidates. Today the v2 heuristic in `vector_images.py` handles this from drawing geometry alone; with a `picture` bbox in hand, the heuristic can be replaced by a single rasterise-the-region call (no clustering, no filter, no rejection-reason accounting), which removes the false-positive uncertainty that justifies v2's opt-in default. The promotion path from v2's opt-in to default-on is precisely this: once a layout `picture` label is available, the heuristic's rejection band collapses to "trust the structural signal."
- **c.** Replace the caption-proximity regex with structural `caption` labels from the layout model when present, falling back to the regex when absent. The regex stays a useful fallback because layout models occasionally miss the `caption` label on tightly-spaced figures.
- **d.** Retire `vector_images.py`'s heuristic constants (`_MIN_CLUSTER_DIM_PT`, `_MAX_TEXT_OVERLAP_FRACTION`, `_MIN_CLUSTER_ITEM_COUNT`, etc.) once the layout model's `picture` label is the load-bearing signal. The module can shrink to its bbox-clamp + rasterise + caption-reuse essentials.

The N-way agreement vector documented above extends from `(mupdf_block_id, spatial_block_id, layout_label, layout_bbox)` to include a fifth element:

```
(mupdf_block_id, spatial_block_id, layout_label, layout_bbox, image_source)
```

`image_source` is the `ExtractedImage.source` field (`"raster"` / `"vector"`) plus its `xref` when the layout `picture` bbox IoU-matched an extracted image, or null when no match exists. The stability commitment in `tomd/CLAUDE.md` already documents which parts of `SectionKind.IMAGE` are pinned vs. swap-points for this integration, so the layout path can plug in without breaking emit or downstream consumers.

### 4.4 A La Carte Opportunities

- **Surya layout only** (`surya.layout.LayoutPredictor`): take just the layout component, ignore Surya's text detection/recognition. Output is `[{bbox, label, position}]` per page.
- **Docling layout + TableFormer**: use Docling's layout YOLO as the region tagger and TableFormer specifically as the table-structure recovery path. Both Apache-2.0 and CPU-friendly. Tables are the place where tomd's spatial reconstruction most often disagrees with MuPDF, so a third independent table opinion is high value.
- **DocLayout-YOLO** as a pure CPU-side region pre-classifier (lightweight enough to run alongside MuPDF on every page).
- **Florence-2 `<OCR_WITH_REGION>`** as a per-page targeted tool only when the existing paths flag a low-confidence region. Returns quad-boxes with text.
- **GOT-OCR2** for math/formula regions specifically (LaTeX output), invoked only on cells/blocks tagged `Formula`/`TextInlineMath` by the layout model.

### 4.5 What to Avoid

- **Nougat** -- documented repetition/degeneration on out-of-distribution documents. WG21 papers with code listings, ins/del wording, and section numbers like `2.1.3` are far from arXiv physics/CS prose. Output is a single page-level Markdown blob with no spans to ensemble. Hallucination risk is high and the failure mode (plausible-looking fake C++ tokens) is the worst case for WG21.
- **olmOCR / Qwen2-VL / Idefics3 as primary path** -- 7-8B params, GPU-only, whole-page generative output that loses span-level information. Acceptable only as a sidecar VLM for the existing reconcile step (see section 7), not as the third path.
- **LayoutLMv3 / LiLT / DocFormerV2 / UDOP** -- all require fine-tuning to be useful for layout-class prediction. tomd already has token positions from MuPDF, so marginal gain does not justify labeling cost.

### 4.6 Benchmark References

- OmniDocBench v1.5 (1,355 pages, 9 doc types): https://llm-stats.com/benchmarks/omnidocbench-1.5
- olmOCR-Bench: https://huggingface.co/datasets/allenai/olmOCR-bench
- MinerU vs Docling layout mAP: https://www.codesota.com/ocr/docling-vs-mineru
- DocLayout-YOLO paper: https://huggingface.co/papers/2410.12628

### 4.7 Known v2.0 vector-extraction limitations (calibrated against P4003R1)

The v2.0 vector-extraction heuristic in `tomd/lib/pdf/vector_images.py` has known false-positive classes pending the layout-aware path above (§4.3) which would replace the heuristic with structural `picture` bboxes.

**Horizontal flow diagrams with thin outer containers: handled via container detection.**

P4003R1 page 8 has an "IoAwaitable → IoRunnable → io_task<T>" flow diagram drawn as one row of labeled boxes connected by arrows. Its bbox is 381 × 35 pt — well under the strict `_MIN_CLUSTER_DIM_PT = 60` floor. The pattern is a thin outer container (2-item rectangle) enclosing smaller per-label clusters; centroid-based clustering doesn't merge them, so naively the outer cluster has only 2 items and the inner clusters are individually too small.

Resolved via `_detect_frame_drawings` + `_merge_clusters_into_frames` in `vector_images.py`: a frame-shaped drawing (low item count, extreme aspect, area < 30% of page) explicitly merges with the clusters it encloses into a "virtual cluster" with combined item count and union bbox. Virtual clusters get a relaxed min-dim floor (`_VIRTUAL_MIN_CLUSTER_DIM_PT = 30`) and bypass the `aspect_extreme` filter when their item count clears `_VIRTUAL_MIN_ITEM_COUNT = 50`. Targeted enough not to over-merge unrelated nearby content; constraints documented inline.

**Vector clusters duplicating structural content: handled via structural-overlap filter.**

P4003R1 pages 67 and 69 have code blocks rendered with substantial syntax-highlight vector decoration; P4003R1 page 8 has a comparison table where each cell has its own background rectangle. These cluster as vector figures despite the text path already producing structural representations (markdown code blocks, markdown tables).

Resolved via `_filter_vector_images_against_structural` in `pipeline.py`: after `structure_sections` completes, any vector `ExtractedImage` whose bbox overlaps a `SectionKind.TABLE` or `SectionKind.CODE` section by more than `_STRUCTURAL_OVERLAP_THRESHOLD` (50%) is dropped, and its corresponding IMAGE section removed from the section list. Raster images are not filtered (embedded screenshots co-located with code blocks are intentional content). The per-paper uncertainty marker's `kept` count is recomputed after filtering so its disclosure matches the actual markdown.

The fix relies on the structure pipeline correctly identifying TABLE and CODE sections. For the P4003R1 page 8 comparison table specifically, `tomd/lib/pdf/table.py`'s `_columns_match` was also extended with a right-edge-fallback signal so right-aligned numeric columns whose x-start varies row-to-row by cell-text length but whose x-end is exact (the canonical "1265.2" vs "-" both right-aligning to the same edge) detect as the same column. The fallback uses a strict `_COLUMN_X_END_TOLERANCE = 1.0pt` so it doesn't false-positive on table-of-contents-style layouts where section-name endings drift 1-2pt by coincidence.

### 4.8 Known text-pipeline bug: code-block reordering (independent of vector extraction)

Surfaced during P4003R1 calibration but not caused by vector extraction. P4003R1 page 34 has the layout:

```
y= 61-118  prose: "The window... TLS remains valid between await_suspend and await_resume:"
y=155-397  code:  auto initial_suspend() noexcept { ... }
y=434-462  prose: "Every time the coroutine resumes... The flow:"
```

PDF y-order is prose → code → prose. The converted markdown emits them as prose → prose → code, suggesting the structure pipeline (`structure.py`'s paragraph-merge pass, most likely) merges the two prose paragraphs across the interleaving code section.

The v1 IMAGE-survival pass guards against this for `SectionKind.IMAGE`. The equivalent guard for `SectionKind.CODE` may be missing or the merge predicate is firing because the first prose lacks terminal punctuation. Affects pure-raster runs too on any paper with prose-code-prose layout; the vector-extraction flag only made this paper visible during review.

Fix location: `tomd/lib/pdf/structure.py`'s paragraph-merge / section-ordering passes. Add a CODE-respecting guard symmetric to the IMAGE one. Separate ticket; medium-to-high complexity (requires tracing how `compare_extractions` builds the section order and where merging happens).

---

## 5. Research Area 2: Zero-Shot NLI for Block/Span Classification

### 5.1 Problem

[structure.py](src/tomd/lib/pdf/structure.py) classifies every `Section` as one of the `SectionKind` values using hand-coded rules over font size, section numbering, known section names, line geometry, and dual-path agreement. When signals disagree, the section gets `Confidence.LOW` or `Confidence.UNCERTAIN`. A zero-shot NLI classifier can provide an independent structural opinion on every block.

### 5.2 Models Surveyed

| HF ID | Params | License | Strengths | Weaknesses |
|---|---|---|---|---|
| `MoritzLaurer/deberta-v3-large-zeroshot-v2.0` | ~435M | MIT | Best NLI accuracy (0.643 F1-macro on 28-task suite); ONNX+int8 builds exist | One forward pass per label; 512-token cap |
| `MoritzLaurer/deberta-v3-large-zeroshot-v2.0-c` | ~435M | MIT (commercial-safe training data) | Same accuracy, legally cleanest | Slightly lower accuracy than non-c variant |
| `MoritzLaurer/deberta-v3-base-zeroshot-v2.0` | ~184M | MIT | 3-4x faster for ~3-5 pt F1 drop | Less robust on short spans |
| `MoritzLaurer/ModernBERT-base-zeroshot-v2.0` | ~149M | Apache-2.0 | 2x faster than DeBERTa-base with bf16; flash-attn; 8K context | Lower accuracy than DeBERTa |
| `tasksource/ModernBERT-large-nli` | ~395M | Apache-2.0 | Strong on reasoning benchmarks; 8K context | Less tuned for classification-as-NLI |
| `knowledgator/gliclass-large-v3.0` | ~435M | Apache-2.0 | **Single forward pass scores all N labels** (~10x faster than cross-encoder NLI); 0.700 avg F1 | Newer API, smaller community |
| `knowledgator/gliclass-modern-large-v3.0` | ~400M | Apache-2.0 | 43.8 ex/s vs 25.2 for DeBERTa; 8K context | -9 pt F1 vs DeBERTa gliclass (0.608) |
| `facebook/bart-large-mnli` | 407M | MIT | Default HF baseline, well-known | Materially weaker (~15 pt F1 gap vs v2.0 family) |
| `cross-encoder/nli-deberta-v3-large` | ~435M | Apache-2.0 | Plain 3-class NLI, good for hand-rolled hypotheses | Not multi-dataset tuned |

### 5.3 Recommended Approach: NLI on Every Block (Accuracy-First)

With GPU available, run NLI on every block, not just `UNCERTAIN` ones. On an A100 with fp16 batched inference, DeBERTa-v3-large processes ~500-1000 premise-hypothesis pairs/s, making 1000-4000 pairs per paper sub-second to seconds.

**Model choice:** `MoritzLaurer/deberta-v3-large-zeroshot-v2.0` (the non-`-c` variant; license is not a constraint). Consider `knowledgator/gliclass-large-v3.0` head-to-head: if it matches DeBERTa accuracy it is strictly cheaper (single forward pass over all labels).

**Label design (critical -- hypothesis wording matters more than model size):**

Use a document-structure-aware hypothesis template, not the default `"This example is about {}"`. Recommended template:

```
"This text fragment is a {label} in a technical document."
```

Labels phrased as natural-language descriptions:

| `SectionKind` target | NLI hypothesis label |
|---|---|
| `TITLE` | `"document title"` |
| `HEADING` | `"section heading"` |
| `PARAGRAPH` | `"normal paragraph of body prose"` |
| `LIST` | `"bullet-list item"` |
| `CODE` | `"piece of source code"` |
| `TABLE` | `"table cell row"` |
| `WORDING` | `"inserted or deleted wording change"` |
| `METADATA` | `"bibliographic citation or metadata"` |
| header/footer | `"page header or footer"` |
| TOC | `"table of contents entry"` |

Split structural vs semantic axes into two separate zero-shot calls to reduce label count per call (NLI cost is O(N labels)):
1. Primary type: {heading, paragraph, list-item, code, table-cell, header/footer, toc, metadata} (8 labels)
2. Heading depth: {title, h1, h2, h3, h4+} -- only when primary type = heading (5 labels)

**Premise enrichment:** for very short inputs (`void f();`, `2.1.3 Design Rationale`), concatenate light metadata context into the premise as parenthetical natural language:

```
"[font=Consolas 9pt monospace] void f();"
"[centered, font-size 1.4x body, bold] 2.1.3 Design Rationale"
```

NLI models read the premise as natural text. Encode metadata as parenthetical natural language, not JSON.

**Accuracy lifts:**
- Use two paraphrased hypotheses per label and average entailment scores (cheapest documented accuracy boost, per Laurer 2024 / Sanh T0 finding).
- Include a "none of the above" decoy hypothesis (`"This text does not fit any document structure category."`) to detect garbage spans and out-of-distribution OCR junk.
- Run single-label (softmax) for primary type; multi-label (sigmoid) for orthogonal flags (is-monospace-rendered, contains-citation, contains-ins-del-markup).

### 5.4 Ensemble Strategy

Treat the existing hand-coded multi-signal rules as **prior**, NLI as **likelihood**:

```
p_combined(label) proportional_to p_rule(label)^alpha * p_nli(label)^beta
```

Start with alpha=1.0, beta=0.5 (rules win ties, NLI breaks them). Calibrate on a small (<=200 span) hand-labeled set per document genre.

**Confidence mapping:** map NLI entailment scores to the existing 4-level `Confidence` enum via fixed thresholds *after* temperature calibration on a dev set. The default HF pipeline returns raw softmax — these are not calibrated probabilities. Fit a one-parameter temperature T by minimising NLL on the dev set before thresholding:

- `HIGH`: calibrated score >= 0.85
- `MEDIUM`: >= 0.65
- `LOW`: >= 0.45
- `UNCERTAIN`: below 0.45

Use NLI **margin** (p(top) - p(2nd)), not absolute top-1, for the tie-breaker decision. A 0.51/0.49 split should remain `UNCERTAIN` and escalate.

Reserve the LLM/VLM reconciliation step for residuals where both rules and NLI disagree, and NLI margin < 0.10.

### 5.5 Failure Modes Specific to WG21

- **Code-as-prose**: inline grammar productions and EBNF (`expression : assignment-expression ...`) look syntactically like prose to NLI. Misclassifies as PARAGRAPH unless monospace flag is pushed into the premise text.
- **Heading vs centered figure caption**: both are short, may be bold/larger. NLI alone cannot disambiguate without geometric context. Keep position/y-coordinate as a rule prior.
- **Numbered list item vs subsection heading**: "3.1 Rationale" vs "3. provide a free-standing operator". Need explicit `"numbered subsection heading"` and `"numbered list bullet"` labels and a rule prior from font-size delta.
- **Dense citation patterns** (`[N4988] 16.5.2.1 [intro.compliance]`): high token-overlap with bibliographic templates can flip TOC, bibliography, and cross-reference classifications. Disambiguate via page-position rule, not NLI.
- **Running headers/footers**: NLI sees them as legitimate METADATA. The dedup-across-pages rule in [cleanup.py](src/tomd/lib/pdf/cleanup.py) is far more reliable. Use NLI only as a secondary check.
- **Token-budget overflow**: long code blocks (>512 tokens for DeBERTa) get truncated mid-token. Either chunk and majority-vote, or move long blocks to ModernBERT (8K context).

### 5.6 Key Citations

- https://huggingface.co/MoritzLaurer/deberta-v3-large-zeroshot-v2.0
- https://huggingface.co/MoritzLaurer/ModernBERT-base-zeroshot-v2.0
- https://huggingface.co/onnx-community/deberta-v3-large-zeroshot-v2.0-c-ONNX
- https://huggingface.co/blog/Ihor/refreshing-zero-shot-classification
- https://arxiv.org/html/2312.17543v2 (Laurer et al., "Building Efficient Universal Classifiers with NLI")
- https://arxiv.org/html/2508.07662v1 (GLiClass paper)
- https://github.com/MoritzLaurer/zeroshot-classifier

---

## 6. Research Area 3: Wording (ins/del) Detection Augmentation

### 6.1 Problem

[wording.py](src/tomd/lib/pdf/wording.py) uses a three-layer detector: block-level color contamination filter, line-level majority filter (>50% non-link chars green or red), and span-level ins/del classification. Two-pass deletion: red spans without strikethrough drawing are collected as `del_unconfirmed`; if >= 5 green ins spans exist (`_MIN_WORDING_SPANS`), unconfirmed deletions are promoted. This heuristic is fragile for atypical papers (red used for emphasis, not deletion; greens that are not insertions).

### 6.2 Proposed Improvements

**6.2.1 Section-gate with embedding prototype matching**

Before the HSV color filter runs, classify each section as "wording section" vs "prose paragraph" vs "code block" using sentence-embedding prototype matching. This kills false positives where red == emphasis in prose, not deletion.

Recommended model: `BAAI/bge-large-en-v1.5` (1024d, MIT, top MTEB English). Alternatives: `Alibaba-NLP/gte-large-en-v1.5` (1024d, 8192-token context for long sections), `mixedbread-ai/mxbai-embed-large-v1` (1024d, Matryoshka), `nomic-ai/nomic-embed-text-v1.5` (768d, 8K context, explicit classification task prefix).

Pipeline: build ~30 hand-picked exemplar spans per class {wording-ins, wording-del, prose, code, table} from existing confidently-tagged tomd outputs. Encode to mean centroid. Classify new sections by cosine to nearest centroid + margin threshold. **Zero hand-labeling cost** (auto-mine from known-good papers).

Expected FP reduction on the atypical-paper class: 40-60% (red-as-emphasis), 20-30% (greens-that-are-not-ins). [uncertain]

**6.2.2 Zero-shot NLI for wording direction**

Use `MoritzLaurer/deberta-v3-large-zeroshot-v2.0` with hypothesis templates:
- `"This text proposes adding new normative wording to the C++ standard."`
- `"This text proposes deleting existing normative wording from the C++ standard."`
- `"This text is descriptive prose discussing a proposal, not an edit to the standard."`
- `"This text is a C++ code example."`

Run multi-label mode (`multi_label=True`) so ins/del/prose/code are independent probabilities. Always feed the span with +/-1 sentence of context (wording fragments are short and ambiguous in isolation). [uncertain] Expect ~70-80% precision on the ins/del direction given context; main failure: short identifier-only spans and mathematical formulas.

**6.2.3 VLM strikethrough oracle**

Replace the ">=5 green ins spans -> promote unconfirmed deletions" heuristic (`_MIN_WORDING_SPANS` in [wording.py](src/tomd/lib/pdf/wording.py)) with a per-crop VLM check. For every red-but-no-strikethrough-drawing span, render a tight bbox crop at 200-300 DPI and send to a VLM asking "is a horizontal line drawn through these glyphs?"

Recommended models:
- `Qwen/Qwen2.5-VL-7B-Instruct` -- best practical option for document grounding; GPU only.
- `microsoft/Florence-2-large` -- MIT, supports `<OPEN_VOCABULARY_DETECTION>` with custom phrases like "text with strikethrough line." Cheaper. [uncertain recall]

Cache by content hash. Batch inference per page. This eliminates a known-fragile heuristic with grounded visual perception.

No dedicated printed-strikethrough HF model exists. Closest dataset: `LTU-ML/handwritten_cross-outs` (7 cross-out types) -- could fine-tune a small ViT/YOLO but not directly usable zero-shot.

**6.2.4 Legal/redline domain models**

Surveyed but found limited transferability:

| Name | HF ID | Training | Relevance |
|---|---|---|---|
| LEGAL-BERT | `nlpaueb/legal-bert-base-uncased` | 12 GB legislation/cases/contracts | Low -- classifies clause topics, not diff structure |
| CONTRACTS-BERT | `nlpaueb/bert-base-uncased-contracts` | US contracts only | Low |
| Legal-BERT clause classifier | `HADRETNA/Legal-BERT-Clause-Classification` | LEDGAR, 100 clause types | Low |

None target diff/redline structure itself. Transfer to WG21 "is this an edit?" is weak.

### 6.3 Recommended Ensemble (Ordered Decision Flow)

1. **Color HSV remains primary** (cheap, deterministic, ~95% precision when colors are conventional). Keep block-level contamination filter and line-level majority filter in [wording.py](src/tomd/lib/pdf/wording.py) unchanged.
2. **Section-gate (embedding prototype match)** runs once per detected section to answer: "is this a wording section at all?" Kills false positives where red == emphasis in prose blocks. CPU-feasible.
3. **Zero-shot NLI** runs only on spans where color says yes but section-gate says no, or vice versa (disagreement set). Adds direction confirmation via ins/del hypotheses.
4. **VLM strikethrough oracle** runs only on red-without-strikethrough-drawing spans, replacing the `_MIN_WORDING_SPANS` heuristic. Per-page crop, batch inference.
5. Decision flow per span:
   - `color=green AND section_is_wording` -> ins
   - `color=red AND strike_drawn` -> del
   - `color=red AND NOT strike_drawn` -> VLM(crop) ? del : (NLI says del ? del : prose)
   - `color=neutral AND section_is_wording AND NLI says ins` -> ins (low confidence)

---

## 7. Research Area 4: VLM-as-Judge (Replacing prompts.json)

### 7.1 Problem

Today tomd emits `<pid>.prompts.json` with LLM reconcile prompts for uncertain regions ([emit.py](src/tomd/lib/pdf/emit.py)). The operator pastes each prompt into an LLM and manually applies the result. For cloud deployment with accuracy as the primary objective, automate this step.

### 7.2 Proposed Approach

For each uncertain region after the N-way ensemble (section 4), render a tight bbox crop at 200-300 DPI. Send the crop, the MuPDF text, the spatial text, and the ensemble label distribution to a strong VLM with a verbatim-preservation contract.

The contract:

> Choose between version A (MuPDF) and version B (spatial), or merge structurally without changing any token. Never paraphrase. Return the chosen Markdown plus a confidence indicator.

This is exactly the role the existing v1 prompts file was designed for. The change is running it at conversion time with a cloud VLM and writing the result back into the output.

### 7.3 VLM Candidates

| Model | Suitability | Cost |
|---|---|---|
| `Qwen/Qwen2.5-VL-72B-Instruct` | Leads OmniDocBench v1.5 generalist; best raw accuracy [uncertain, leaderboard volatile] | ~30-90 s for ~30 regions on H100 |
| `allenai/olmOCR-2-*` (7B) | Document-specialized, much cheaper | ~10-30 s for ~30 regions |
| `docling-project/SmolDocling-256M-preview` | Apache-2.0, CPU-feasible, emits DocTags with `<code>`, `<table>` regions | Cheapest; reconcile-only oracle |

Recommended: start with olmOCR-2 7B for cost-effectiveness; fall back to Qwen2.5-VL-72B for the residual where olmOCR-2 disagreed with both extraction paths.

### 7.4 Verbatim Verification

After VLM resolution, verify that every token in the VLM output exists in one of the two source texts (MuPDF or spatial). Any novel token is a hallucination. Log and escalate to human review if hallucination is detected. This is the critical safety invariant: the VLM fixes structure, never content.

---

## 8. Research Area 5: HTML Path Improvements

### 8.1 Problem

[lib/html/extract.py](src/tomd/lib/html/extract.py) detects the source generator (mpark, Bikeshed, HackMD, hand-written, schultke, dascandy/fiets, wg21, unknown) via hand-coded heuristics (custom elements, meta tags, class patterns). Generator-specific rules then drive how DOM elements map to Markdown. The `unknown` bucket is the quality gap.

### 8.2 Generator Detection -- Skip ML

The honest finding is that heuristic detection already wins on this task because generator signals are deterministic strings (`<meta name="generator" content="Bikeshed">`, `<code-block>` custom elements). ML helps only on `unknown`/hand-written/hybrid documents. The existing heuristics in [lib/html/extract.py](src/tomd/lib/html/extract.py) should remain primary.

One experiment worth running: encode a head+custom-element fingerprint with `BAAI/bge-small-en-v1.5` (33M, 384d, MIT) and do nearest-prototype over a small labeled set (5-20 docs/generator) to triage the `unknown` bucket. Do not replace the heuristics.

### 8.3 Code-vs-Prose Flag

Run `wandb/sourcecode-detection` (Apache-2.0, CodeBERTa-small fine-tune, claims F1~0.997) on `<p>`/`<div>` text nodes that are NOT already inside `<pre>/<code>`. Recovers code blocks in HackMD and hand-written HTML where fences leaked into paragraphs. This is the highest-payoff ML integration for the HTML path.

Alternatives: `huggingface/CodeBERTa-small-v1` (base, MIT) as feature extractor; `huggingface/CodeBERTa-language-id` for language detection once content is known to be code.

### 8.4 DOM Role Classification (Low Priority)

Zero-shot NLI on DOM elements is viable but expensive (9 labels x thousands of nodes at ~200ms/pair on CPU would take minutes per doc). Viable only with a tiered approach:

1. Heuristic filter: skip nodes classified by tag (`<pre>/<code>`, `<nav>`, `<footer>`, `<table class="toc">`, `<ins>/<del>`).
2. BGE embedding prototype-match on remaining "ambiguous" leaves (~10-20% of nodes). Cost: ~1-3 ms/text on CPU, total ~2-6 s per doc.
3. DeBERTa-base-zeroshot only on the <=1% where BGE margin is below epsilon.

Labels: `body content`, `boilerplate`, `metadata header`, `code block`, `wording diff`, `table of contents`, `figure/caption`, `bibliography`, `formal note/admonition`.

### 8.5 Boilerplate Extraction Models

No HF transformer beats Trafilatura zero-shot for generic boilerplate stripping on long-form documents:

| Name | Repo | Approach | License |
|---|---|---|---|
| Trafilatura | `adbar/trafilatura` (PyPI) | Hand-tuned heuristics + XPath | Apache-2.0 |
| Resiliparse | `chatnoir-eu/chatnoir-resiliparse` (PyPI) | C++ heuristic, very fast | Apache-2.0 |
| jusText | `miso-belica/jusText` (PyPI) | Sentence-density heuristic | BSD-2 |
| BoilerNet | `mrjleo/boilernet` (GitHub) | BiLSTM sequence labeling | MIT |
| MarkupLM | `microsoft/markuplm-base` | Text+XPath transformer (FT required) | MIT |

MarkupLM is the only HF model that natively eats HTML+XPath but is not zero-shot. Only worthwhile if labels are collected.

No models specifically for parsing technical/spec HTML (Bikeshed, ReSpec output) were found.

---

## 9. PDF-to-Markdown Ecosystem Comparison

### 9.1 Zero-Shot Opportunity Matrix

Which subproblems each top tool solves well (3) or poorly (0) on WG21-style papers:

| Subproblem | Marker | Docling | MinerU 2.5 | olmOCR-2 | SmolDocling | Nougat | pymupdf4llm |
|---|---|---|---|---|---|---|---|
| Code listings | 2 | 2 | 2 | 2 | 2 | **0** | 1 |
| Tables | 2 | **3** | **3** | 2 | 2 | 1 | 1 |
| ins/del wording | **0** | **0** | **0** | **0** | **0** | **0** | 1 |
| Math/LaTeX | 2 | 2 | 3 | 2 | 2 | 3 | 0 |
| Multi-column | 2 | **3** | **3** | 3 | 2 | 2 | 1 |
| Hyperlinks | 1 | 2 | 1 | 0 | 0 | 0 | **3** |
| Footnotes | 2 | 2 | 2 | 1 | 1 | 1 | 1 |
| TOC detection | 1 | 2 | 2 | 1 | 1 | 0 | 0 |
| Header/Footer | 2 | **3** | 2 | 2 | 1 | 1 | 0 |

**tomd's moat: ins/del wording.** Every tool scores 0. Color + strikethrough geometry detection in [wording.py](src/tomd/lib/pdf/wording.py) is unique to tomd and must stay deterministic (augmented by the wording ensemble in section 6).

### 9.2 Third-Path / Hint-Only Usability

Tools that expose layout models as callable functions returning bounding boxes (not all-or-nothing converters):

- **Docling**: `docling-ibm-models` exposes layout and TableFormer as plain functions. Best fit.
- **Surya**: `surya.layout`, `surya.table_rec` callable independently. Second best.
- **DocLayout-YOLO**: separate `layout_analysis` module. Lightweight.
- **PP-StructureV3**: separate `layout_analysis` module (PaddlePaddle dependency is heavy but hint-capable).

Tools that are all-or-nothing converters (cannot provide hints without running the full pipeline): Marker, MinerU, olmOCR, Nougat, GOT-OCR2, SmolDocling, Chandra-OCR-2.

---

## 10. Proposed Architecture (Cloud, Accuracy-First)

### 10.1 Per-Page Parallel Fan-Out

```
Per page at 150-200 DPI:

  Path A: MuPDF page.get_text("dict")           -> Block/Line/Span (existing)
  Path B: MuPDF page.get_text("rawdict")         -> Block/Line/Span (existing, spatial)
  Path C: Docling heron (RT-DETR-v2, ~43M)       -> [(bbox, label)] (new)
  Path D: MinerU 2.5 layout detection             -> [(bbox, label, reading_order)] (new)
  Path E: Surya layout (optional, gated)          -> [(bbox, label, reading_order)] (new, only if C and D disagree)
```

Paths A and B produce text with font metadata. Paths C, D, E produce typed bounding boxes from rendered page images. Text never comes from the model paths.

### 10.2 Per-Block Analysis

For every block/section:

1. **Rules** emit `(label, confidence)` as today in [structure.py](src/tomd/lib/pdf/structure.py).
2. **NLI** emits `(label_distribution)` via DeBERTa-v3-large-zeroshot-v2.0 or GLiClass.
3. **Layout ensemble** emits `(label, agreement_count)` from N-way region IoU voting.

### 10.3 Ensemble Decision

- 4+ signals agree: `Confidence.HIGH`, emit.
- 3 agree, 1 dissents: `Confidence.MEDIUM`, emit with annotation.
- Split: `Confidence.UNCERTAIN`, route to VLM judge.

### 10.4 VLM Judge Layer (Replaces prompts.json)

- For each `UNCERTAIN` region: render crop + provide MuPDF text + spatial text + ensemble label distribution.
- VLM (olmOCR-2 7B or Qwen2.5-VL-72B) resolves with verbatim contract.
- Post-resolution: verify every output token exists in source texts (hallucination check).

### 10.5 Specialized Model Invocations

- `TABLE` regions: Docling TableFormer recovers cell grid; tomd's geometric column-profile detection in [table.py](src/tomd/lib/pdf/table.py) becomes a confidence input, not the sole arbiter.
- `CODE` regions: keep MuPDF verbatim; NLI confirms not-prose.
- Red-no-strike spans: VLM strikethrough oracle (always, not just residual). Replaces `_MIN_WORDING_SPANS` heuristic.
- Wording sections: section-gate embedding match + NLI direction confirmation.

---

## 11. Inference Cost Estimates (Cloud, GPU)

Single A100/H100, typical WG21 paper (50 pages, ~1500 spans):

| Component | Time estimate | Notes |
|---|---|---|
| Layout ensemble (Docling + MinerU, parallel) | ~5-15 s | Per-page 150 DPI render + inference |
| NLI on all spans (1500 spans x 8 labels, DeBERTa-v3-large fp16 batched) | ~3-8 s | ~500-1000 pairs/s on A100 |
| TableFormer on ~5 tables | ~2 s | |
| Strikethrough VLM on ~20 candidate spans (Qwen2.5-VL-7B) | ~10-30 s | Per-crop batch inference |
| VLM judge on ~30 UNCERTAIN regions (Qwen2.5-VL-72B) | ~30-90 s | Most expensive component |
| **Total** | **~50-150 s/paper** | Parallelizable across pages |

If 72B is too slow for the VLM judge, swap to Qwen2.5-VL-32B or olmOCR-2 7B for ~3-5x speedup with modest accuracy loss [uncertain].

Memory: DeBERTa-v3-large fp32 ~ 1.7 GB; int8 ~ 540 MB. Docling layout ~ 200 MB. Fit comfortably alongside MuPDF in a single process.

---

## 12. Experiment Plan (Ordered)

### Experiment 1: Layout Ensemble Pass-Through (Highest ROI, Lowest Risk)

**What:** add Docling heron + MinerU 2.5 as parallel paths C/D. Per page, render at 150 DPI, run both layout detectors, log three-way agreement metrics on existing corpus. Do not change emit behavior.

**Measure:** fraction of currently-emitted blocks whose `Confidence` label would change under N-way agreement. Count of `UNCERTAIN` sections that would be resolved to `HIGH` or `MEDIUM`.

**Integration point:** new module (e.g. `lib/pdf/layout.py`) called between stages 12 and 13 in the pipeline.

**Success criterion:** >= 30% of current `UNCERTAIN` sections resolved to `MEDIUM` or higher without changing any emitted text.

### Experiment 2: NLI on Every Block

**What:** run `MoritzLaurer/deberta-v3-large-zeroshot-v2.0` (or `knowledgator/gliclass-large-v3.0`) on every block using the axis-split label set from section 5.3. Log disagreement with rules.

**Measure:** per-`SectionKind` disagreement rate. Manually inspect top-disagreement samples (~100) to determine whether NLI or rules are correct.

**Prerequisite:** hand-label ~300 spans from 3 WG21 papers across the 10 categories.

**Success criterion:** NLI agrees with hand labels >= 80% on `UNCERTAIN`/`LOW`-confidence blocks where rules had no strong opinion.

**Sub-experiment:** GLiClass-large head-to-head on the same eval set. Compare accuracy and per-paper latency at full label set in one pass. If GLiClass wins on both axes, it becomes the production choice.

**Sub-experiment:** premise enrichment ablation. A/B test: (a) raw text only, (b) text + parenthetical natural-language metadata (`"[bold, 14pt, centered] ..."`). Target: short headings and code lines.

### Experiment 3: VLM Judge Replacing prompts.json

**What:** gate behind a `--judge` flag. For every emitted `<!-- tomd:uncertain -->` region, automatically resolve via olmOCR-2 7B with the verbatim contract.

**Measure:** agreement with human review on a curated 50-region eval set. Hallucination rate (any token added that was not in either MuPDF or spatial source).

**Success criterion:** >= 90% agreement with human review. Hallucination rate < 2%.

### Experiment 4: Wording Section-Gate + Strikethrough Oracle

**What:** auto-mine ~30 wording vs prose centroids from confidently-tagged existing tomd outputs. Add a section gate before the HSV color filter. For red-without-strikethrough spans, add a per-crop VLM check (Qwen2.5-VL-7B).

**Measure:** ins/del classification accuracy on a curated set of atypical-color-convention papers, vs current heuristic.

**Success criterion:** FP reduction >= 30% on the atypical-paper set without FN increase.

### Experiment 5: Code-vs-Prose for HTML Path

**What:** run `wandb/sourcecode-detection` on text nodes not inside `<pre>/<code>` in the HTML render path.

**Measure:** count of recovered code blocks in HackMD and hand-written HTML test set.

**Success criterion:** >= 5 additional code blocks correctly detected per 100 hand-written HTML papers with no false positives.

### Experiment 6: Calibration and Threshold Sweep

**What:** on the dev set from experiments 1-2, fit a temperature T per label and per `Confidence` threshold. Compare against the naive thresholds in the ensemble plan.

**Measure:** NLL on held-out set. Fraction of sections that change `Confidence` level under calibrated vs naive thresholds.

**Success criterion:** calibrated thresholds produce a defensible mapping into the 4-level `Confidence` enum such that LLM reconciliation fires on <= 10% of sections (down from current UNCERTAIN rate).

---

## 13. Fine-Tuned Mini-Classifier (Deferred)

If zero-shot accuracy from experiments 2/4 proves insufficient, a small fine-tuned classifier would dominate. This is deferred until zero-shot baselines are established.

- **Base model:** `answerdotai/ModernBERT-base` (139M, 8K context, 2-4x faster than BERT).
- **Labeling cost:** ~300-800 spans across 20-40 papers, labels = {ins, del, prose, code, table, header}; ~4-8 person-hours with a labeling UI.
- **Synthetic augmentation:** auto-mine from already-correctly-tagged tomd outputs (HIGH confidence sections). Gives ~5-10x free data.
- **Expected gain over zero-shot NLI:** macro-F1 ~0.85-0.92 vs ~0.55-0.70, especially on short spans where NLI is weakest.
- **What it fixes:** wording-vs-prose boundary (high value). What it does not fix: ins-vs-del direction without color signal (still needs color or vision).

---

## 14. Follow-Up Investigations

These are defined well enough to dispatch as focused research tasks:

1. **Docling layout integration prototype.** Run Docling heron on 10 WG21 PDFs; produce a before/after diff of emitted Markdown (compare UNCERTAIN count, section kind accuracy). Requires only a test script, no pipeline changes.

2. **DeBERTa vs GLiClass head-to-head benchmark.** Hand-label 300 spans from 3 papers. Run both models. Compare accuracy, latency, and memory. Determine whether GLiClass's O(1)-in-labels advantage holds at tomd's actual label cardinality.

3. **VLM verbatim-preservation prompt engineering.** Write concrete prompt templates for olmOCR-2 7B and Qwen2.5-VL-72B with token-equality contract and hallucination-detection signals. Test on 50 uncertain regions.

4. **Strikethrough oracle cost/accuracy curve.** Compare Florence-2-base vs Florence-2-large vs Qwen2.5-VL-3B vs 7B on a curated crop set of red-without-strikethrough spans. Determine the minimum model size that achieves >= 90% strikethrough detection recall.

5. **ModernBERT fine-tuning viability.** If experiment 2 shows zero-shot accuracy is insufficient: mine ~500 labels from confident tomd outputs, fine-tune `answerdotai/ModernBERT-base`, compare against zero-shot on the same eval set.

6. **Optional `tomd[ml]` extra.** Audit `pyproject.toml` and decide whether model dependencies should be gated behind an optional extra (`tomd[ml]`) so the deterministic pipeline remains dependency-light for non-cloud use.

7. **SmolDocling as reconcile oracle.** Investigate whether SmolDocling (256M, Apache-2.0, CPU-feasible) can serve as a cheap reconcile-only oracle for UNCERTAIN regions, slotting into the existing `--llm` v2 interface alongside or instead of a full VLM judge.

8. **Reading-order evaluation.** Run Surya layout's reading-order output against MuPDF's natural order on 20 multi-column WG21 papers. Count order inversions. Determines whether reading-order from layout models is worth adopting as a tiebreaker.

---

## 15. Appendix: Model License Summary

License is not a constraint for tomd's closed-source cloud deployment, but documented here for reference:

| Model / Tool | License | Notes |
|---|---|---|
| DeBERTa-v3-large-zeroshot-v2.0 | MIT | |
| DeBERTa-v3-large-zeroshot-v2.0-c | MIT | Commercial-safe training data |
| GLiClass-large-v3.0 | Apache-2.0 | |
| ModernBERT-base-zeroshot-v2.0 | Apache-2.0 | |
| Docling models (heron + TableFormer) | Apache-2.0 + CDLA-Permissive-2.0 | |
| MinerU / PDF-Extract-Kit | AGPL-3.0 | Acceptable for cloud-only |
| Surya / Marker | GPL-3.0 + OpenRAIL-M (revenue cap) | Acceptable for cloud-only |
| DocLayout-YOLO | AGPL (ultralytics) | Acceptable for cloud-only |
| Nougat weights | CC-BY-NC-4.0 | Rejected on accuracy, not license |
| olmOCR-2 | Apache-2.0 | |
| SmolDocling | Apache-2.0 | |
| Florence-2 | MIT | |
| Qwen2.5-VL (7B/72B) | Apache-2.0 | |
| BGE-large-en-v1.5 | MIT | |
| wandb/sourcecode-detection | Apache-2.0 | |
| pymupdf4llm / PyMuPDF | AGPL-3.0 | Already a tomd constraint |

---

## 16. Summary

tomd's deterministic dual-path pipeline is unique in the ecosystem: no surveyed tool handles ins/del wording, and no generative model preserves span-level font/color metadata without hallucination risk. The correct integration shape is to add model-derived structural signals into the existing multi-signal confidence ensemble, never to replace text extraction.

The four highest-ROI integrations, in order:

1. **Layout ensemble as third path** (Docling + MinerU + optional Surya): resolves UNCERTAIN regions by adding an independent image-based structural opinion.
2. **NLI on every block** (DeBERTa-v3-large or GLiClass): provides an independent semantic opinion on section role for every block.
3. **VLM judge** (olmOCR-2 7B or Qwen2.5-VL-72B): automates the manual LLM reconcile step for remaining uncertain regions.
4. **Wording ensemble** (embedding section-gate + VLM strikethrough oracle): replaces fragile heuristics with grounded classification.

Total expected cost: ~50-150 s/paper on a single GPU. The existing `Confidence` enum and `Section`/`Span` data types in [types.py](src/tomd/lib/pdf/types.py) are preserved. The pipeline stages in [lib/pdf/\_\_init\_\_.py](src/tomd/lib/pdf/__init__.py) gain additional steps but retain the same execution order.
