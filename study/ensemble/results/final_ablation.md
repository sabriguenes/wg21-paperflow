# Final cascade-vs-single ablation

Corpus: P2300R10 Phase 1 + Phase 2  (n=410)

## TARGET hypothesis: baseline ("A statement of fact or opinion.")

| config | corpus | acc | T→S | TARGET rec | SKIP rec | oracle calls |
| --- | --- | --: | --: | --: | --: | --: |
| nli-small alone | Phase 1 (prose) | 0.462 | 3 | 43% | 0% | 0/210 |
| zeroshot-large alone | Phase 1 (prose) | 0.543 | 1 | 75% | 67% | 210/210 |
| cascade | Phase 1 (prose) | 0.524 | 0 | 75% | 0% | 169/210 |
| prefilter + small | Phase 1 (prose) | 0.476 | 3 | 43% | 67% | 0/210 |
| prefilter + large | Phase 1 (prose) | 0.543 | 1 | 75% | 67% | 205/210 |
| prefilter + cascade | Phase 1 (prose) | 0.538 | 0 | 75% | 67% | 164/210 |
| nli-small alone | Phase 2 (wording) | 0.410 | 1 | 50% | 0% | 0/200 |
| zeroshot-large alone | Phase 2 (wording) | 0.700 | 0 | 85% | 0% | 200/200 |
| cascade | Phase 2 (wording) | 0.710 | 0 | 89% | 0% | 157/200 |
| prefilter + small | Phase 2 (wording) | 0.575 | 1 | 50% | 97% | 0/200 |
| prefilter + large | Phase 2 (wording) | 0.860 | 1 | 85% | 97% | 166/200 |
| prefilter + cascade | Phase 2 (wording) | 0.875 | 1 | 89% | 97% | 128/200 |
| nli-small alone | ALL | 0.437 | 4 | 47% | 0% | 0/410 |
| zeroshot-large alone | ALL | 0.620 | 1 | 81% | 10% | 410/410 |
| cascade | ALL | 0.615 | 0 | 84% | 0% | 326/410 |
| prefilter + small | ALL | 0.524 | 4 | 47% | 92% | 0/410 |
| prefilter + large | ALL | 0.698 | 2 | 81% | 92% | 371/410 |
| prefilter + cascade | ALL | 0.702 | 1 | 84% | 92% | 292/410 |

## TARGET hypothesis: alt ("A statement describing what something does, is, or proposes.")

| config | corpus | acc | T→S | TARGET rec | SKIP rec | oracle calls |
| --- | --- | --: | --: | --: | --: | --: |
| nli-small alone | Phase 1 (prose) | 0.443 | 0 | 98% | 0% | 0/210 |
| zeroshot-large alone | Phase 1 (prose) | 0.438 | 1 | 90% | 0% | 210/210 |
| cascade | Phase 1 (prose) | 0.438 | 0 | 98% | 0% | 37/210 |
| prefilter + small | Phase 1 (prose) | 0.457 | 0 | 98% | 67% | 0/210 |
| prefilter + large | Phase 1 (prose) | 0.457 | 1 | 90% | 67% | 205/210 |
| prefilter + cascade | Phase 1 (prose) | 0.457 | 0 | 98% | 67% | 32/210 |
| nli-small alone | Phase 2 (wording) | 0.745 | 0 | 96% | 0% | 0/200 |
| zeroshot-large alone | Phase 2 (wording) | 0.760 | 0 | 95% | 12% | 200/200 |
| cascade | Phase 2 (wording) | 0.770 | 0 | 99% | 0% | 46/200 |
| prefilter + small | Phase 2 (wording) | 0.905 | 1 | 95% | 97% | 0/200 |
| prefilter + large | Phase 2 (wording) | 0.905 | 1 | 95% | 97% | 166/200 |
| prefilter + cascade | Phase 2 (wording) | 0.930 | 1 | 99% | 97% | 22/200 |
| nli-small alone | ALL | 0.590 | 0 | 97% | 0% | 0/410 |
| zeroshot-large alone | ALL | 0.595 | 1 | 94% | 10% | 410/410 |
| cascade | ALL | 0.600 | 0 | 99% | 0% | 83/410 |
| prefilter + small | ALL | 0.676 | 1 | 96% | 92% | 0/410 |
| prefilter + large | ALL | 0.676 | 2 | 94% | 92% | 371/410 |
| prefilter + cascade | ALL | 0.688 | 1 | 98% | 92% | 54/410 |

