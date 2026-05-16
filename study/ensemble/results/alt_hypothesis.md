# Alt-hypothesis ablation

## nli-small :: baseline

- target hypotheses: ['A statement of fact or opinion.']
- skip hypotheses:   ['A heading, list marker, or page metadata.']

  margin (0.05, 0.4): acc=0.453  T->S miss=1  F1 T/C/S = 0.576/0.385/0.077
  margin (0.05, 0.6): acc=0.467  T->S miss=0  F1 T/C/S = 0.576/0.411/0.087
  margin (0.0, 0.4): acc=0.438  T->S miss=1  F1 T/C/S = 0.587/0.247/0.077

## nli-small :: v1_claim_or_proposal

- target hypotheses: ['A claim, proposal, or assertion.']
- skip hypotheses:   ['A heading, list marker, or page metadata.']

  margin (0.05, 0.4): acc=0.438  T->S miss=6  F1 T/C/S = 0.500/0.439/0.187
  margin (0.05, 0.6): acc=0.431  T->S miss=1  F1 T/C/S = 0.500/0.417/0.154
  margin (0.0, 0.4): acc=0.489  T->S miss=6  F1 T/C/S = 0.618/0.378/0.187

## nli-small :: v2_request_too

- target hypotheses: ['A claim, proposal, request, or assertion.']
- skip hypotheses:   ['A heading, list marker, or page metadata.']

  margin (0.05, 0.4): acc=0.409  T->S miss=7  F1 T/C/S = 0.376/0.463/0.308
  margin (0.05, 0.6): acc=0.409  T->S miss=2  F1 T/C/S = 0.376/0.462/0.267
  margin (0.0, 0.4): acc=0.431  T->S miss=7  F1 T/C/S = 0.504/0.393/0.308

## nli-small :: v3_skip_fragment

- target hypotheses: ['A statement of fact or opinion.']
- skip hypotheses:   ['A heading, list marker, page metadata, or sentence fragment.']

  margin (0.05, 0.4): acc=0.482  T->S miss=0  F1 T/C/S = 0.614/0.341/0.077
  margin (0.05, 0.6): acc=0.504  T->S miss=0  F1 T/C/S = 0.614/0.400/0.087
  margin (0.0, 0.4): acc=0.482  T->S miss=0  F1 T/C/S = 0.630/0.239/0.077

## nli-small :: v4_combo

- target hypotheses: ['A claim, proposal, request, or assertion.']
- skip hypotheses:   ['A heading, list marker, page metadata, or sentence fragment.']

  margin (0.05, 0.4): acc=0.431  T->S miss=1  F1 T/C/S = 0.515/0.414/0.077
  margin (0.05, 0.6): acc=0.445  T->S miss=0  F1 T/C/S = 0.515/0.437/0.087
  margin (0.0, 0.4): acc=0.489  T->S miss=1  F1 T/C/S = 0.624/0.374/0.077

## nli-small :: v5_multi_target

- target hypotheses: ['A claim, proposal, or assertion.', 'A request or recommendation.', 'A definition or specification.']
- skip hypotheses:   ['A heading, list marker, page metadata, or sentence fragment.']

  margin (0.05, 0.4): acc=0.489  T->S miss=0  F1 T/C/S = 0.660/0.103/0.000
  margin (0.05, 0.6): acc=0.489  T->S miss=0  F1 T/C/S = 0.660/0.103/0.000
  margin (0.0, 0.4): acc=0.489  T->S miss=0  F1 T/C/S = 0.650/0.109/0.000

## nli-small :: v6_multi_both

- target hypotheses: ['A claim, proposal, or assertion.', 'A request or recommendation.', 'A factual or empirical statement.']
- skip hypotheses:   ['A heading or list marker.', 'Page metadata or a section header.', 'A sentence fragment or formatting artifact.']

  margin (0.05, 0.4): acc=0.350  T->S miss=8  F1 T/C/S = 0.301/0.458/0.054
  margin (0.05, 0.6): acc=0.365  T->S miss=4  F1 T/C/S = 0.301/0.467/0.065
  margin (0.0, 0.4): acc=0.358  T->S miss=8  F1 T/C/S = 0.414/0.397/0.054

## zeroshot-large :: baseline

- target hypotheses: ['A statement of fact or opinion.']
- skip hypotheses:   ['A heading, list marker, or page metadata.']

  margin (0.05, 0.4): acc=0.584  T->S miss=0  F1 T/C/S = 0.710/0.395/0.345
  margin (0.05, 0.6): acc=0.591  T->S miss=0  F1 T/C/S = 0.710/0.430/0.308
  margin (0.0, 0.4): acc=0.569  T->S miss=0  F1 T/C/S = 0.705/0.319/0.345

## zeroshot-large :: v3_skip_fragment

- target hypotheses: ['A statement of fact or opinion.']
- skip hypotheses:   ['A heading, list marker, page metadata, or sentence fragment.']

  margin (0.05, 0.4): acc=0.584  T->S miss=0  F1 T/C/S = 0.705/0.442/0.375
  margin (0.05, 0.6): acc=0.584  T->S miss=0  F1 T/C/S = 0.705/0.478/0.231
  margin (0.0, 0.4): acc=0.547  T->S miss=0  F1 T/C/S = 0.687/0.316/0.375

## zeroshot-large :: v5_multi_target

- target hypotheses: ['A claim, proposal, or assertion.', 'A request or recommendation.', 'A definition or specification.']
- skip hypotheses:   ['A heading, list marker, page metadata, or sentence fragment.']

  margin (0.05, 0.4): acc=0.474  T->S miss=0  F1 T/C/S = 0.639/0.220/0.400
  margin (0.05, 0.6): acc=0.496  T->S miss=0  F1 T/C/S = 0.639/0.330/0.333
  margin (0.0, 0.4): acc=0.504  T->S miss=0  F1 T/C/S = 0.680/0.211/0.400


## Sorted summary (by accuracy desc, T->S asc)

| acc | T->S miss | F1 T | F1 S | classifier | variant | margin |
| --- | --- | --- | --- | --- | --- | --- |
| 0.591 | 0 | 0.710 | 0.308 | zeroshot-large | baseline | (0.05, 0.6) |
| 0.584 | 0 | 0.710 | 0.345 | zeroshot-large | baseline | (0.05, 0.4) |
| 0.584 | 0 | 0.705 | 0.375 | zeroshot-large | v3_skip_fragment | (0.05, 0.4) |
| 0.584 | 0 | 0.705 | 0.231 | zeroshot-large | v3_skip_fragment | (0.05, 0.6) |
| 0.569 | 0 | 0.705 | 0.345 | zeroshot-large | baseline | (0.0, 0.4) |
| 0.547 | 0 | 0.687 | 0.375 | zeroshot-large | v3_skip_fragment | (0.0, 0.4) |
| 0.504 | 0 | 0.614 | 0.087 | nli-small | v3_skip_fragment | (0.05, 0.6) |
| 0.504 | 0 | 0.680 | 0.400 | zeroshot-large | v5_multi_target | (0.0, 0.4) |
| 0.496 | 0 | 0.639 | 0.333 | zeroshot-large | v5_multi_target | (0.05, 0.6) |
| 0.489 | 0 | 0.660 | 0.000 | nli-small | v5_multi_target | (0.05, 0.4) |
| 0.489 | 0 | 0.660 | 0.000 | nli-small | v5_multi_target | (0.05, 0.6) |
| 0.489 | 0 | 0.650 | 0.000 | nli-small | v5_multi_target | (0.0, 0.4) |
| 0.489 | 1 | 0.624 | 0.077 | nli-small | v4_combo | (0.0, 0.4) |
| 0.489 | 6 | 0.618 | 0.187 | nli-small | v1_claim_or_proposal | (0.0, 0.4) |
| 0.482 | 0 | 0.614 | 0.077 | nli-small | v3_skip_fragment | (0.05, 0.4) |
| 0.482 | 0 | 0.630 | 0.077 | nli-small | v3_skip_fragment | (0.0, 0.4) |
| 0.474 | 0 | 0.639 | 0.400 | zeroshot-large | v5_multi_target | (0.05, 0.4) |
| 0.467 | 0 | 0.576 | 0.087 | nli-small | baseline | (0.05, 0.6) |
| 0.453 | 1 | 0.576 | 0.077 | nli-small | baseline | (0.05, 0.4) |
| 0.445 | 0 | 0.515 | 0.087 | nli-small | v4_combo | (0.05, 0.6) |
| 0.438 | 1 | 0.587 | 0.077 | nli-small | baseline | (0.0, 0.4) |
| 0.438 | 6 | 0.500 | 0.187 | nli-small | v1_claim_or_proposal | (0.05, 0.4) |
| 0.431 | 1 | 0.500 | 0.154 | nli-small | v1_claim_or_proposal | (0.05, 0.6) |
| 0.431 | 1 | 0.515 | 0.077 | nli-small | v4_combo | (0.05, 0.4) |
| 0.431 | 7 | 0.504 | 0.308 | nli-small | v2_request_too | (0.0, 0.4) |
| 0.409 | 2 | 0.376 | 0.267 | nli-small | v2_request_too | (0.05, 0.6) |
| 0.409 | 7 | 0.376 | 0.308 | nli-small | v2_request_too | (0.05, 0.4) |
| 0.365 | 4 | 0.301 | 0.065 | nli-small | v6_multi_both | (0.05, 0.6) |
| 0.358 | 8 | 0.414 | 0.054 | nli-small | v6_multi_both | (0.0, 0.4) |
| 0.350 | 8 | 0.301 | 0.054 | nli-small | v6_multi_both | (0.05, 0.4) |
