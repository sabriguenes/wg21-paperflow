# Herald - Non-collection layers

Design material for Herald's downstream layers, extracted from the master design plan. Section numbers are preserved from the original layout so cross-references still work.

For the future `2-intelligence.md` through `5-editorial.md` specs.

## 3.3 Editorial philosophy: over-generate, curate, age out

Herald does not try to write the perfect article. It writes many articles and lets a human editor pick. This exploits an asymmetry:

- LLMs are cheap to regenerate, hard to perfect. Asking one to "make this article 5% better" wastes more attention than it saves
- Humans are slow at producing, fast at selecting. Showing an editor 8 drafts and saying "pick one" takes 90 seconds; polishing one draft takes 30 minutes

**Workflow:**

1. The writer generates N drafts per topic (small N, maybe 3-8), varying angle, tone, length, lead
2. Drafts land in a **slate** for the topic, state = `draft`
3. The editor opens the slate and sees all N drafts side-by-side with the underlying brief
4. Editor picks one (state = `approved`), rejects all, or defers
5. Approved drafts move to `published`; the rest of the slate marks `unpublished` (kept for analysis)
6. Topics that don't yield a publishable draft can be re-briefed if new evidence arrives, and otherwise **age out** per the topic's `freshness_window_days`

This is the editorial philosophy, not a workflow detail. It changes everything downstream: the writer is tuned for **diversity of angle**, not "the best article possible." The editor's tooling is a **selection UI**, not an editing UI. The system tracks **pick rate per topic / per source / per angle**, which is eventually a feedback signal for tuning. Nitpicking individual drafts is explicitly out of scope.

**Optional pitch layer (v2).** Before generating a full article, the writer first produces a one-paragraph pitch (headline + lede + angle). The editor approves pitches before any full draft is generated. Shifts wasted LLM tokens away from rejected angles. Worth considering once we see how often editors reject entire slates.

## 3.4 Source curation: LLM proposes, human approves

Sources are not a fixed list. Herald continuously discovers candidates and proposes them for permanent subscription. Same "system proposes, human curates" pattern as §3.3.

1. As Herald processes content, it accumulates **outbound links**, **referenced authors**, **referenced sites**, **referenced feeds** observed but not subscribed
2. Periodically a **Feed Proposer** (LLM-driven) scans this pool and ranks candidates by signal: domain reputation, frequency of citation by sources we trust, topical relevance, presence of a feed/sitemap, language match
3. Top-ranked candidates land in `pending_sources` with the LLM's reasoning and evidence
4. The editor reviews each pending source (URL, what it is, why Herald thinks it's worth subscribing to, sample posts, citing trusted sources)
5. Editor accepts (-> `active`), rejects (-> `rejected` with cooldown), or defers
6. Editor can also **demote** an existing `active` source if it's gone bad

Sources have a state machine: `candidate` -> `pending` -> `active` / `rejected` / `demoted`. The cooldown prevents re-proposing the same dead blog every week.

The LLM in this loop is doing curation reasoning, not content generation - same `pipeline` machinery as the writer, different prompt and output type.

## 3.5 Integration: shared Postgres with wg21.org (Django)

wg21.org is a **Django + Postgres** application. Herald **shares the Postgres database** with it. That is the integration mechanism.

Concrete consequences:

- **Schema is shared.** Herald's tables (`sources`, `urls`, `contents`, `topics`, `drafts`, `published_articles`, etc.) live in the same Postgres as wg21.org's. Django views read directly from those tables
- **Editor auth is Django auth.** No JWT, no shared secret. Editor pages are Django views protected by a permission check on `request.user`. Herald never authenticates users
- **No HTTP API needed for read/write.** wg21.org talks to the DB directly. Herald's external surface collapses to: the Atom feed (a Django view), a small internal trigger API ("re-generate this slate"), and a Prometheus metrics endpoint
- **Migrations are Django migrations.** Owned by Herald's Django adapter (see §3.6)
- **R2 is shared too.** Django uses `django-storages` + `boto3`; Herald uses `fsspec`. Two clients, one bucket, namespaced by prefix

## 3.7 LLM hosting: self-hosted fleet via the pipeline framework

**All LLM calls go through the `pipeline` package** ([wg21-paperflow/packages/pipeline](c:\Users\Vinnie\src\wg21-paperflow\packages\pipeline)), which talks to **self-hosted models on owned hardware** (per CONTEXT.md). Cloud APIs (OpenAI, Anthropic, Gemini) are not in scope. Treat any architectural choice that implies "we'll just call OpenAI" as broken by construction.

What `pipeline` provides:

- `AgentBackend` / `ModelBackend` model abstraction (vLLM, SGLang, llama.cpp endpoints behind a single interface)
- `run_agent` / `run_task` enforcing determinism invariants
- Pydantic-typed structured output with retry budgets
- Prompt-injection defense via `pipeline.tools.wrap_source` (essential: web inputs are untrusted)
- Trace and debug artifact discipline

**The fleet is frontier + specialists**, not "one big model." Per CONTEXT.md: a frontier general model (the floor) plus 5-6 fine-tuned specialists (~7-32B each) co-located on the same B300-class GPU. Specialists are each optimized for one thing and explicitly bad at everything else. **Herald's writer routes per draft**: a 6-draft slate for a topic might pull one draft from the frontier model, one from a "terse news" specialist, one from an "editorial commentary" specialist, one from a "technical explainer" specialist, etc. The diversity comes from routing, not from temperature jitter. This is the architectural payoff of §3.3's over-generate strategy.

**The Feed Proposer** (§3.4) uses `pipeline` the same way - probably routed to a small classifier-style specialist over time.

The crawler does not use `pipeline`; collection has no LLM calls.

## 4.2 Intelligence (pure Python, no LLM)

A consumer of the `collection_events` log (see §4.1.4). Produces **deltas** and **topics**.

- A **delta** is "new evidence has arrived for an entity since the last snapshot." Computed by reading `content_first_seen` / `content_changed` events that touch URLs matching an entity watch, then diffing the new `url_content_versions` row against the previous `watch_snapshot`
- A **topic** is a cluster of evidence that is newsworthy on its own. Topics can be created by:
  - Explicit entity watches firing a delta of meaningful size
  - Heuristic clustering (M sources publish about the same thing within K hours - design choice in §6 still-open)
  - Manual editor seeding
- The layer assembles **evidence packages** per topic: a curated set of `contents` rows + source attribution + relevant `entities`

Intelligence decides "this is worth writing about." Editorial decides "this is worth publishing." Writer is in between.

## 4.3 Source curation (LLM proposes, human approves)

Mechanics from §3.4:

- **Candidate harvester** (Python, no LLM) consumes `collection_events` (kind `content_first_seen`), scans those `contents` rows for outbound links and citations, and emits `candidate_observed` events into the same log (which populate `candidate_sources`)
- **Feed Proposer** (LLM, periodic) reads from `candidate_sources`, ranks, writes to `pending_sources` with reasoning and evidence. Reuses `pipeline`
- **Editor pages in wg21.org** (Django views, editor-permission-gated) list pending sources and let the editor approve/reject/defer. The view writes directly to `source_decisions` in the shared DB; approved sources are inserted into `sources` and start being polled by the Herald worker
- **Cooldown enforcement** prevents re-proposing rejected URLs for the configured window

## 4.4 Writer (LLM, reuses pipeline)

Reads topics + evidence packages, produces multiple drafts per topic (§3.3).

- **Brief builder** turns a topic + evidence package into a Pydantic-typed `Brief` (structured: who, what, when, where, supporting quotes, source URLs, suggested angles). May be pure Python or LLM-assisted
- **Draft generator** runs N times per brief. Per §3.7, **the N drafts come from N different members of the specialist fleet** (frontier + terse-news specialist + editorial-voice specialist + technical-explainer specialist + ...), not from N temperature-jittered runs of the same model. The routing table lives in the writer and is data-driven (one row per (topic-kind, specialist) pair, easy to add a new specialist). Each run produces a typed `Draft` (headline, lede, body markdown, internal source references, plus `generator_model` for later pick-rate analysis)
- All LLM calls go through `pipeline.run_agent` / `run_task` against self-hosted endpoints, with determinism invariants and prompt-injection defense intact
- Drafts persist immediately with `state=draft`. No "best of N" selection inside the writer - that's the editor's job
- `published_articles.generator_model` and `editorial_actions` together give the pick-rate signal that feeds back into specialist fine-tuning (§3.8)

## 4.5 Editorial (human picks, system ages out)

- **Editor pages in wg21.org** render each topic's draft slate: N drafts side-by-side, the brief, the evidence, the source list. The view queries the shared DB directly
- Editor picks one (form POST flips `drafts.state` to `approved`), rejects all (flips them to `rejected`), or defers
- Approved drafts move to `published`. wg21.org's reader pages render published articles by querying `published_articles` directly. Static rendering is not on the table
- Unpicked drafts age out per the topic's `freshness_window_days`. A periodic Herald worker job sweeps and marks `aged_out`
- Editorial actions logged in `editorial_actions` for later analysis (pick rate per angle, per source, per generator model)
- Editor identity = `request.user` in the Django view. Herald never authenticates users

## 4.6 What Herald exposes (the small external surface)

Because integration is "shared Postgres" (§3.5), Herald's external surface is small:

**Herald does not expose:**
- A read API for articles - wg21.org reads `published_articles` directly
- An editor API - wg21.org's Django views mutate the shared tables directly
- Any user-facing HTML - all UI is wg21.org

**Herald does expose:**
- The **Atom feed** (best as a Django view in the adapter, rendering Herald rows)
- A small **internal trigger API**: "re-generate this slate now," "force re-fetch this URL," "mark this source unhealthy." Handful of endpoints for ops or for editor views that need synchronous background-work invocation
- A **`/metrics` endpoint** for Prometheus

Herald's primary interfaces are: the **shared Postgres schema** (contract with wg21.org), the **collection workers** (outbound HTTP only), and the **writer workers** (LLM only).
