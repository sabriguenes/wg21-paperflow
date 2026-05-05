# web_tools - Agent Rules

## What this is

Web search and content fetch for LLM pipelines. Brave Search API
as the sole backend. `WebResearcher` is the user-facing object.
`SearchBackend` ABC allows future backends.

## Public Surface

- `WebResearcher` - create, use, close. Borrows or owns a backend.
- `SearchResult` - frozen dataclass: title, url, snippet.
- `SearchResponse` - frozen dataclass: status_code, results.
- `FetchResponse` - frozen dataclass: status_code, content.
- `SearchBackend` - ABC for adding backends.

## How to Add a Backend

1. Create `backends/<name>.py`
2. Subclass `SearchBackend`
3. Implement `async def search(self, query, max_results) -> SearchResponse`
4. Declare `name` class attribute
5. Add `close()` if the backend owns resources
6. Register in `backends/__init__.py`

## Invariants

- **Status codes on everything.** `search()` returns `SearchResponse`
  with `status_code`. `fetch()` returns `FetchResponse` with
  `status_code`. No bare strings or lists.
- **Backends are self-contained.** Each backend owns its own HTTP
  client. No shared client coupling between session and backend.
- **Backends are long-lived.** `BraveBackend` holds a persistent
  connection pool and rate limiter. Create once, share across
  `WebResearcher` instances for parallel runs.
- **Researcher borrows or owns.** Pass a backend to share it. Omit
  to auto-create one. `_owns_backend` tracks who closes it.
- **Fail loud.** Missing `BRAVE_API_KEY` raises `ValueError` at
  construction time, not at first search call.
- **No global state.** The researcher is an explicit object. Create
  it, pass it around, close it.
