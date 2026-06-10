---
title: "Synthetic Table Fixture"
reply-to:

---

This synthetic fixture exercises the structural-overlap filter: the comparison table below is rendered with per-cell background rectangles and no inter-column spanning rules, the canonical false-positive shape for vector extraction. Each column would naively cluster as its own vector image; the filter must drop them when the table detector identifies the region as a TABLE section.

| Platform | Mode | Time (ms) | Score |
| --- | --- | --- | --- |
| LinuxX64 | release | 1234.5 | 9.10x |
| LinuxARM | release | 987.1 | 10.40x |
| macOSX64 | debug | 2456.7 | - |
| WindowsX64 | release | 1532.0 | 8.21x |
| FreeBSDX64 | release | 1801.2 | 7.55x |
| LinuxRISCV | release | 3104.8 | 5.20x |
| macOSARM | release | 856.4 | 11.92x |
| WindowsARM | debug | 4291.3 | - |
| LinuxX64 | debug | 2103.0 | 6.80x |

The result above shows that even when the markdown contains a clean rendered table, the converter must not emit duplicate vector PNGs of each column.

<!-- tomd:vector-extraction-uncertain: pages_scanned=1 candidates=4 kept=0 rejected=4 reasons={text_overlap:4} pages_skipped=0. Vector extraction is heuristic; missed diagrams and false positives are both possible. See vector_images.py constants for tuning surface. -->
