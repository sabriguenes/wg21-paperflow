# pipeline

Web search and content fetch for LLM pipelines. Uses the Brave
Search API for deterministic, high-quality results.

## Quick Start

```python
from pipeline import WebResearcher

async with WebResearcher() as researcher:
    # Search
    response = await researcher.search("coroutine executor C++")
    for r in response.results:
        print(r.title, r.url)

    # Fetch and extract content
    page = await researcher.fetch("https://example.com")
    print(page.content)
```

## LLM Tool Registration

```python
from pydantic_ai import Agent

agent = Agent(model="anthropic:claude-haiku-4-5-20251001", output_type=MyOutput)
agent.tool_plain(researcher.web_search)   # JSON results
agent.tool_plain(researcher.web_fetch)    # extracted markdown
```

## Environment Variables

- `BRAVE_API_KEY` - **Required.** Brave Search API subscription token.
  Get one at https://api-dashboard.search.brave.com/register
  ($5/1000 queries, free tier available).

## Shared Backend for Parallel Runs

`BraveBackend` is a long-lived object with its own connection pool
and rate limiter (50 req/s). Share it across parallel pipeline runs:

```python
from pipeline.backends.brave import BraveBackend

backend = BraveBackend()
try:
    await asyncio.gather(*[
        review_paper(pid, storage, search_backend=backend)
        for pid in pids
    ])
finally:
    await backend.close()
```

## Adding a Backend

See [DESIGN.md](DESIGN.md) for architecture and
[CLAUDE.md](src/pipeline/CLAUDE.md) for the step-by-step guide.
