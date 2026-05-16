# Ablation on P4003R3 (137 sentences)

Gold distribution: TARGET=64 CONTEXT=51 SKIP=22

### nli-small alone  (target_margin=0.05, skip_margin=0.4)

- **accuracy**: 0.445 (61/137)
- **target -> skip miss-fires**: 1 (HIGH-STAKES; irrecoverable)

| metric | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| P | 0.526 | 0.351 | 0.250 |
| R | 0.625 | 0.392 | 0.045 |
| F1 | 0.571 | 0.370 | 0.077 |

**Confusion** (rows=gold, cols=pred):

| gold \ pred | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| **TARGET** | 40 | 23 | 1 |
| **CONTEXT** | 29 | 20 | 2 |
| **SKIP** | 7 | 14 | 1 |

### nli-small alone  (target_margin=0.0, skip_margin=0.4)

- **accuracy**: 0.445 (61/137)
- **target -> skip miss-fires**: 1 (HIGH-STAKES; irrecoverable)

| metric | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| P | 0.490 | 0.333 | 0.250 |
| R | 0.766 | 0.216 | 0.045 |
| F1 | 0.598 | 0.262 | 0.077 |

**Confusion** (rows=gold, cols=pred):

| gold \ pred | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| **TARGET** | 49 | 14 | 1 |
| **CONTEXT** | 38 | 11 | 2 |
| **SKIP** | 13 | 8 | 1 |

### nli-small alone  (target_margin=0.05, skip_margin=0.6)

- **accuracy**: 0.460 (63/137)
- **target -> skip miss-fires**: 0 (HIGH-STAKES; irrecoverable)

| metric | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| P | 0.526 | 0.367 | 1.000 |
| R | 0.625 | 0.431 | 0.045 |
| F1 | 0.571 | 0.396 | 0.087 |

**Confusion** (rows=gold, cols=pred):

| gold \ pred | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| **TARGET** | 40 | 24 | 0 |
| **CONTEXT** | 29 | 22 | 0 |
| **SKIP** | 7 | 14 | 1 |

### nli-small alone  (target_margin=0.05, skip_margin=0.2)

- **accuracy**: 0.416 (57/137)
- **target -> skip miss-fires**: 3 (HIGH-STAKES; irrecoverable)

| metric | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| P | 0.526 | 0.314 | 0.100 |
| R | 0.625 | 0.314 | 0.045 |
| F1 | 0.571 | 0.314 | 0.063 |

**Confusion** (rows=gold, cols=pred):

| gold \ pred | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| **TARGET** | 40 | 21 | 3 |
| **CONTEXT** | 29 | 16 | 6 |
| **SKIP** | 7 | 14 | 1 |

### zeroshot-large alone  (target_margin=0.05, skip_margin=0.4)

- **accuracy**: 0.569 (78/137)
- **target -> skip miss-fires**: 1 (HIGH-STAKES; irrecoverable)

| metric | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| P | 0.569 | 0.583 | 0.545 |
| R | 0.906 | 0.275 | 0.273 |
| F1 | 0.699 | 0.373 | 0.364 |

**Confusion** (rows=gold, cols=pred):

| gold \ pred | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| **TARGET** | 58 | 5 | 1 |
| **CONTEXT** | 33 | 14 | 4 |
| **SKIP** | 11 | 5 | 6 |

### zeroshot-large alone  (target_margin=0.0, skip_margin=0.4)

- **accuracy**: 0.555 (76/137)
- **target -> skip miss-fires**: 1 (HIGH-STAKES; irrecoverable)

| metric | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| P | 0.556 | 0.556 | 0.545 |
| R | 0.938 | 0.196 | 0.273 |
| F1 | 0.698 | 0.290 | 0.364 |

**Confusion** (rows=gold, cols=pred):

| gold \ pred | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| **TARGET** | 60 | 3 | 1 |
| **CONTEXT** | 37 | 10 | 4 |
| **SKIP** | 11 | 5 | 6 |

### zeroshot-large alone  (target_margin=0.05, skip_margin=0.6)

- **accuracy**: 0.562 (77/137)
- **target -> skip miss-fires**: 0 (HIGH-STAKES; irrecoverable)

| metric | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| P | 0.569 | 0.531 | 0.667 |
| R | 0.906 | 0.333 | 0.091 |
| F1 | 0.699 | 0.410 | 0.160 |

**Confusion** (rows=gold, cols=pred):

| gold \ pred | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| **TARGET** | 58 | 6 | 0 |
| **CONTEXT** | 33 | 17 | 1 |
| **SKIP** | 11 | 9 | 2 |

### zeroshot-large alone  (target_margin=0.05, skip_margin=0.2)

- **accuracy**: 0.555 (76/137)
- **target -> skip miss-fires**: 1 (HIGH-STAKES; irrecoverable)

| metric | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| P | 0.569 | 0.556 | 0.471 |
| R | 0.906 | 0.196 | 0.364 |
| F1 | 0.699 | 0.290 | 0.410 |

**Confusion** (rows=gold, cols=pred):

| gold \ pred | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| **TARGET** | 58 | 5 | 1 |
| **CONTEXT** | 33 | 10 | 8 |
| **SKIP** | 11 | 3 | 8 |

### ensemble: arithmetic mean  (target_margin=0.05, skip_margin=0.4)

- **accuracy**: 0.547 (75/137)
- **target -> skip miss-fires**: 0 (HIGH-STAKES; irrecoverable)

| metric | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| P | 0.557 | 0.517 | 0.500 |
| R | 0.922 | 0.294 | 0.045 |
| F1 | 0.694 | 0.375 | 0.083 |

**Confusion** (rows=gold, cols=pred):

| gold \ pred | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| **TARGET** | 59 | 5 | 0 |
| **CONTEXT** | 35 | 15 | 1 |
| **SKIP** | 12 | 9 | 1 |

### ensemble: arithmetic mean  (target_margin=0.0, skip_margin=0.4)

- **accuracy**: 0.511 (70/137)
- **target -> skip miss-fires**: 0 (HIGH-STAKES; irrecoverable)

| metric | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| P | 0.526 | 0.429 | 0.500 |
| R | 0.938 | 0.176 | 0.045 |
| F1 | 0.674 | 0.250 | 0.083 |

**Confusion** (rows=gold, cols=pred):

| gold \ pred | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| **TARGET** | 60 | 4 | 0 |
| **CONTEXT** | 41 | 9 | 1 |
| **SKIP** | 13 | 8 | 1 |

### ensemble: arithmetic mean  (target_margin=0.05, skip_margin=0.6)

- **accuracy**: 0.547 (75/137)
- **target -> skip miss-fires**: 0 (HIGH-STAKES; irrecoverable)

| metric | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| P | 0.557 | 0.516 | 0.000 |
| R | 0.922 | 0.314 | 0.000 |
| F1 | 0.694 | 0.390 | 0.000 |

**Confusion** (rows=gold, cols=pred):

| gold \ pred | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| **TARGET** | 59 | 5 | 0 |
| **CONTEXT** | 35 | 16 | 0 |
| **SKIP** | 12 | 10 | 0 |

### ensemble: arithmetic mean  (target_margin=0.05, skip_margin=0.2)

- **accuracy**: 0.584 (80/137)
- **target -> skip miss-fires**: 1 (HIGH-STAKES; irrecoverable)

| metric | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| P | 0.557 | 0.684 | 0.667 |
| R | 0.922 | 0.255 | 0.364 |
| F1 | 0.694 | 0.371 | 0.471 |

**Confusion** (rows=gold, cols=pred):

| gold \ pred | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| **TARGET** | 59 | 4 | 1 |
| **CONTEXT** | 35 | 13 | 3 |
| **SKIP** | 12 | 2 | 8 |

### ensemble: max-TARGET min-SKIP (recall-biased)  (target_margin=0.05, skip_margin=0.4)

- **accuracy**: 0.474 (65/137)
- **target -> skip miss-fires**: 0 (HIGH-STAKES; irrecoverable)

| metric | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| P | 0.485 | 0.286 | 0.000 |
| R | 0.984 | 0.039 | 0.000 |
| F1 | 0.649 | 0.069 | 0.000 |

**Confusion** (rows=gold, cols=pred):

| gold \ pred | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| **TARGET** | 63 | 1 | 0 |
| **CONTEXT** | 49 | 2 | 0 |
| **SKIP** | 18 | 4 | 0 |

### ensemble: max-TARGET min-SKIP (recall-biased)  (target_margin=0.0, skip_margin=0.4)

- **accuracy**: 0.482 (66/137)
- **target -> skip miss-fires**: 0 (HIGH-STAKES; irrecoverable)

| metric | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| P | 0.485 | 0.400 | 0.000 |
| R | 1.000 | 0.039 | 0.000 |
| F1 | 0.653 | 0.071 | 0.000 |

**Confusion** (rows=gold, cols=pred):

| gold \ pred | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| **TARGET** | 64 | 0 | 0 |
| **CONTEXT** | 49 | 2 | 0 |
| **SKIP** | 19 | 3 | 0 |

### ensemble: max-TARGET min-SKIP (recall-biased)  (target_margin=0.05, skip_margin=0.6)

- **accuracy**: 0.474 (65/137)
- **target -> skip miss-fires**: 0 (HIGH-STAKES; irrecoverable)

| metric | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| P | 0.485 | 0.286 | 0.000 |
| R | 0.984 | 0.039 | 0.000 |
| F1 | 0.649 | 0.069 | 0.000 |

**Confusion** (rows=gold, cols=pred):

| gold \ pred | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| **TARGET** | 63 | 1 | 0 |
| **CONTEXT** | 49 | 2 | 0 |
| **SKIP** | 18 | 4 | 0 |

### ensemble: max-TARGET min-SKIP (recall-biased)  (target_margin=0.05, skip_margin=0.2)

- **accuracy**: 0.467 (64/137)
- **target -> skip miss-fires**: 0 (HIGH-STAKES; irrecoverable)

| metric | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| P | 0.485 | 0.167 | 0.000 |
| R | 0.984 | 0.020 | 0.000 |
| F1 | 0.649 | 0.035 | 0.000 |

**Confusion** (rows=gold, cols=pred):

| gold \ pred | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| **TARGET** | 63 | 1 | 0 |
| **CONTEXT** | 49 | 1 | 1 |
| **SKIP** | 18 | 4 | 0 |

### ensemble: max both (symmetric OR)  (target_margin=0.05, skip_margin=0.4)

- **accuracy**: 0.518 (71/137)
- **target -> skip miss-fires**: 0 (HIGH-STAKES; irrecoverable)

| metric | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| P | 0.558 | 0.393 | 0.400 |
| R | 0.906 | 0.216 | 0.091 |
| F1 | 0.690 | 0.278 | 0.148 |

**Confusion** (rows=gold, cols=pred):

| gold \ pred | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| **TARGET** | 58 | 6 | 0 |
| **CONTEXT** | 37 | 11 | 3 |
| **SKIP** | 9 | 11 | 2 |

### ensemble: max both (symmetric OR)  (target_margin=0.0, skip_margin=0.4)

- **accuracy**: 0.496 (68/137)
- **target -> skip miss-fires**: 0 (HIGH-STAKES; irrecoverable)

| metric | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| P | 0.518 | 0.389 | 0.400 |
| R | 0.922 | 0.137 | 0.091 |
| F1 | 0.663 | 0.203 | 0.148 |

**Confusion** (rows=gold, cols=pred):

| gold \ pred | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| **TARGET** | 59 | 5 | 0 |
| **CONTEXT** | 41 | 7 | 3 |
| **SKIP** | 14 | 6 | 2 |

### ensemble: max both (symmetric OR)  (target_margin=0.05, skip_margin=0.6)

- **accuracy**: 0.533 (73/137)
- **target -> skip miss-fires**: 0 (HIGH-STAKES; irrecoverable)

| metric | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| P | 0.558 | 0.438 | 1.000 |
| R | 0.906 | 0.275 | 0.045 |
| F1 | 0.690 | 0.337 | 0.087 |

**Confusion** (rows=gold, cols=pred):

| gold \ pred | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| **TARGET** | 58 | 6 | 0 |
| **CONTEXT** | 37 | 14 | 0 |
| **SKIP** | 9 | 12 | 1 |

### ensemble: max both (symmetric OR)  (target_margin=0.05, skip_margin=0.2)

- **accuracy**: 0.511 (70/137)
- **target -> skip miss-fires**: 0 (HIGH-STAKES; irrecoverable)

| metric | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| P | 0.558 | 0.360 | 0.375 |
| R | 0.906 | 0.176 | 0.136 |
| F1 | 0.690 | 0.237 | 0.200 |

**Confusion** (rows=gold, cols=pred):

| gold \ pred | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| **TARGET** | 58 | 6 | 0 |
| **CONTEXT** | 37 | 9 | 5 |
| **SKIP** | 9 | 10 | 3 |

### ensemble: weighted 0.7 small + 0.3 large  (target_margin=0.05, skip_margin=0.4)

- **accuracy**: 0.511 (70/137)
- **target -> skip miss-fires**: 0 (HIGH-STAKES; irrecoverable)

| metric | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| P | 0.544 | 0.419 | 0.333 |
| R | 0.875 | 0.255 | 0.045 |
| F1 | 0.671 | 0.317 | 0.080 |

**Confusion** (rows=gold, cols=pred):

| gold \ pred | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| **TARGET** | 56 | 8 | 0 |
| **CONTEXT** | 36 | 13 | 2 |
| **SKIP** | 11 | 10 | 1 |

### ensemble: weighted 0.7 small + 0.3 large  (target_margin=0.0, skip_margin=0.4)

- **accuracy**: 0.511 (70/137)
- **target -> skip miss-fires**: 0 (HIGH-STAKES; irrecoverable)

| metric | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| P | 0.530 | 0.421 | 0.333 |
| R | 0.953 | 0.157 | 0.045 |
| F1 | 0.682 | 0.229 | 0.080 |

**Confusion** (rows=gold, cols=pred):

| gold \ pred | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| **TARGET** | 61 | 3 | 0 |
| **CONTEXT** | 41 | 8 | 2 |
| **SKIP** | 13 | 8 | 1 |

### ensemble: weighted 0.7 small + 0.3 large  (target_margin=0.05, skip_margin=0.6)

- **accuracy**: 0.518 (71/137)
- **target -> skip miss-fires**: 0 (HIGH-STAKES; irrecoverable)

| metric | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| P | 0.544 | 0.441 | 0.000 |
| R | 0.875 | 0.294 | 0.000 |
| F1 | 0.671 | 0.353 | 0.000 |

**Confusion** (rows=gold, cols=pred):

| gold \ pred | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| **TARGET** | 56 | 8 | 0 |
| **CONTEXT** | 36 | 15 | 0 |
| **SKIP** | 11 | 11 | 0 |

### ensemble: weighted 0.7 small + 0.3 large  (target_margin=0.05, skip_margin=0.2)

- **accuracy**: 0.511 (70/137)
- **target -> skip miss-fires**: 2 (HIGH-STAKES; irrecoverable)

| metric | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| P | 0.544 | 0.444 | 0.286 |
| R | 0.875 | 0.235 | 0.091 |
| F1 | 0.671 | 0.308 | 0.138 |

**Confusion** (rows=gold, cols=pred):

| gold \ pred | TARGET | CONTEXT | SKIP |
| --- | --- | --- | --- |
| **TARGET** | 56 | 6 | 2 |
| **CONTEXT** | 36 | 12 | 3 |
| **SKIP** | 11 | 9 | 2 |

