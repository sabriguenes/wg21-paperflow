---
title: "Synthetic Noise Fixture"
reply-to:

---

This synthetic fixture contains only table borders and horizontal rules. The vector-extraction pipeline must produce zero image candidates from this page.

| Header | Column B cell | Column C cell |
| --- | --- | --- |
| Row 1 data | Column B cell | Column C cell |
| Row 2 data | Column B cell | Column C cell |
| Row 3 data | Column B cell | Column C cell |

The text above is wrapped in a table; the lines on the page are borders and running-header rules, not diagrams.

<!-- tomd:vector-extraction-uncertain: pages_scanned=1 candidates=6 kept=0 rejected=6 reasons={edge_band:2, too_few_items:1, too_small:5} pages_skipped=0. Vector extraction is heuristic; missed diagrams and false positives are both possible. See vector_images.py constants for tuning surface. -->
