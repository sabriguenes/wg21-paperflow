# Pipeline Steps

0. **Read** — Chunk the paper and extract paper number citations (pure Python).
1. **Extract Claims** — LLM identifies normative assertions and phrases a question for each.
2. **Dedup Claims** — Deterministic + LLM semantic dedup, tombstone duplicates.
3. **Extract Evidence** — LLM identifies statements offered in support of assertions.
4. **Dedup Evidence** — Same dedup strategy applied to evidence items.
5. **Verify + Deps + Map** — LLM resolves cross-chunk deps, maps evidence to claims, finds contradictions.
6. **Load-Bearing** — LLM classifies claims by support graph position (critical gap, anchored, peripheral).
7. **Web Search** — LLM agent searches paperstore and web for external evidence on triggered claims.
8. **Resolve External** — LLM integrates external evidence into classifications.
9. **Report** — Pure Python renders final markdown with supported/unsupported claims and resources.
