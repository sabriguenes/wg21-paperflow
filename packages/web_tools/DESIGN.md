# web_tools Design Document

## Problem

LLM review pipelines need web search to gather evidence, verify
citations, and assess community reception. The search results must
be deterministic (same query, same provider, same results) and
high-quality (niche WG21 papers, GitHub issues, technical blogs).

## Architecture Decision: Single Provider

We use the Brave Search API as the sole search backend.

### Why Not Metasearch / Backend Rotation

Our first design (v0.1) rotated free scraper backends: DuckDuckGo
via the ddgs library, with plans for Google, Yahoo, Yandex, and
Brave scrapers. This created two problems:

1. **Non-determinism.** Different backends return different results
   for the same query. Reviews produced on Monday with DDG results
   differed from reviews on Tuesday with Yandex results.

2. **Fragility.** HTML scrapers break when search engines change
   their DOM, update bot detection, or rate-limit differently.
   DuckDuckGo returned HTTP 202 (blocked) for niche WG21 queries
   while the browser found results. The ddgs library's metasearch
   returned irrelevant noise from Yandex while DDG was blocked.

A single paid API eliminates both problems.

### Why Brave

- Own index (not a Google/Bing proxy like DuckDuckGo, Ecosia,
  Startpage)
- $5/1000 queries, $5 free monthly credits
- 3000 RPM, no JavaScript rendering needed
- Structured JSON responses with status codes
- Good coverage of GitHub, open-std.org, technical blogs

### Why Not Google Custom Search API

Google CSE searches a "Programmable Search Engine" with configured
sites, not the full Google index. Even with "Search the entire web"
enabled, result quality is noticeably worse than google.com. Same
price ($5/1000), worse results.

## Backend ABC

`SearchBackend` is an ABC with one method: `search(query, max_results)
-> SearchResponse`. Adding a second backend (Mojeek, Yep, etc.) is
one file plus one registry entry. The session delegates without
knowing which backend it talks to.

## Ownership Model

`BraveBackend` is a long-lived shared resource. It owns:
- An `httpx.AsyncClient` for persistent connection pooling
- A token-bucket rate limiter (50 req/s)

`WebResearcher` is a lightweight wrapper created per pipeline run.
It borrows a backend (or creates one for single-run convenience)
and owns a separate `httpx.AsyncClient` for `fetch()` calls to
arbitrary URLs.

When a shared backend is passed in, the researcher does not close
it. When it creates its own, it closes it on exit.

## Status Codes

Every HTTP interaction returns a status code alongside the data:
`SearchResponse(status_code, results)` and `FetchResponse(status_code,
content)`. The session logs warnings on non-200 codes. The LLM tool
wrappers (`web_search`, `web_fetch`) flatten this to strings.

## What We Tried That Didn't Work

1. **DuckDuckGo HTML scraper** (tools.py v0): returned 403 after
   ~30 requests even with a real Chrome UA string.

2. **ddgs metasearch library** (v0.1): DuckDuckGo engine got HTTP
   202 (blocked). Other engines (Yandex, Yahoo) returned irrelevant
   results. The library swallowed HTTP status codes and raised
   "No results found" with no diagnostic info.

3. **_PrimpCapture log interception** (v0.1): we hooked into primp's
   logger to extract HTTP status codes that ddgs hid. This worked
   but was brittle and ugly.

4. **curl_cffi / TLS fingerprint impersonation** (planned, abandoned):
   we considered scraping Google/DDG/Brave/Yahoo/Yandex directly with
   browser-like TLS fingerprints. Google changes its DOM every 2-4
   weeks and requires JavaScript rendering. The maintenance burden
   wasn't worth it when a $5/month API gives better results.

5. **Multi-backend rotation with circuit breakers** (v0.1): aiobreaker
   + aiolimiter + round-robin. Added 4 dependencies and ~100 lines
   of rotation/stats/breaker code. All unnecessary with one reliable
   backend.

## Cost Estimates

| Scale | Volume | Monthly Cost |
|-------|--------|-------------|
| Local dev | ~100 searches/month | $0 (free tier) |
| Paper review | ~50 searches/run, 10 runs | ~$2.50 |
| Agora21 mailing | ~1500 searches/mailing | ~$7.50 |
| Full system | ~10K searches/month | ~$50 |
