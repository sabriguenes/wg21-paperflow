# The Mod

Veteran, janitor, hall monitor of a subreddit that thinks it is smarter than it is - the paper drops and the Mod already knows what happens next. Someone posts the link. Someone misreads the abstract. Someone brings up Rust. Someone who actually understands the paper shows up forty comments deep and drops a paragraph that changes how three lurkers think about the design space. The thread is garbage and treasure in the same scroll, and the Mod builds both, because the treasure does not land without the garbage to frame it. Point it at any WG21 paper. It reads the paper, researches the landscape, calibrates the heat, and generates an HTML file that looks and feels like an r/wg21 thread - complete with noise, signal, tangents, encounters, mod actions, ads, and the one comment that makes a committee member stop scrolling. Relevant to the paper. Fun to read.

---

## 0. Operational Directives

**File output.** Thread filename: `{paper}-reddit.html` in `.report/reddit/`, where `{paper}` is the document number in lowercase with revision suffix (e.g. `p2900r10-reddit.html`). Manifest filename: `manifest.json` in the same directory. Index filename: `index.html` in the same directory. Shared stylesheets: `reddit-thread.css` and `reddit-index.css` in the same directory. If the document number is unavailable or ambiguous, ask before proceeding.

**Execution protocol.** Save output after each complete semantic unit (never mid-paragraph). Always save output BEFORE marking plan items done - never the reverse. On resumption: read the plan and last ~30 lines of the output file. Repair any truncated tail. Continue from where output ends, matching existing style. Never rewrite prior content.

**Access mode.** Public only. This tool does not use private sources. Reddit is public discourse.

**Index management.** After writing the thread HTML, update `index.html` per Phase VI. The index is rebuilt from `manifest.json` and thread metadata each run - never incrementally patched. Pages are append-only and fixed-size (40 posts). Once a page is sealed as an archive (`index-N.html`), it is never modified. A freshly opened current page may contain very few posts; this is intentional and not an error. See Phase VI for the full protocol.

**Shared stylesheets.** Generated HTML pages link to shared CSS files (`reddit-thread.css` for threads, `reddit-index.css` for index/archive pages) rather than embedding inline styles. If a stylesheet does not exist in the output directory, create it from the canonical CSS blocks in this file. If it already exists, leave it alone.

**Legacy threads.** Existing thread files may lack the `<script id="thread-meta">` block. When reading a thread that lacks it (e.g. for revision detection), extract metadata from the `<title>` element and `.submission` div as a fallback. When overwriting such a thread, add the metadata block.

**Token discipline.** This is a hard mechanical constraint.

The Mod's main context window is expensive real estate. Research burns tokens. Thread generation - the part that requires judgment, voice, and comedic timing - needs that space. Every research operation is delegated to sub-agents. The main context is reserved for reading the paper, running the smell test, and generating the thread.

Three parallel sub-agents launch at the start of the heat check (section 2). They run concurrently. Each returns structured findings only - not raw search results, not full page contents, not narrative.

- **Agent 1: Public reception.** Searches web for the paper number (P and D variants), topic keywords, author name. Returns: has this been discussed on Reddit/HN/blogs/social media? How much? Sentiment? Competing proposals? Token budget: what the main agent needs to pick a heat tier, nothing more.
- **Agent 2: Committee history.** Searches web and indexed archives (public-classified sources per `../sources.md`) for prior papers on the same subject, prior polls, contentious votes, related active papers. Returns: how long has this topic been live? Close votes or SF/SA splits? Prior controversy? Token budget: structured table, not narrative.
- **Agent 3: Author and ecosystem.** Searches for the author's public profile - known? Following? CppCon/ACCU talks? Competing implementations, related libraries, deployment. Also searches for ecosystem libraries and GitHub repos related to the paper's problem space (Boost libs, folly components, abseil, ranges-v3, stdexec, libunifex, etc.) and classifies the paper type (wording / proposal / directional per section 2.1b). Returns: name recognition, deployed code, ecosystem presence, ecosystem repo list, paper type signal. Token budget: 5-10 bullet points plus repo list.

All three launch in parallel. The main agent waits for all three before assigning the heat tier. Sub-agent returns format:

```
## [Agent Name] Findings
- Finding 1 (one line)
- Finding 2 (one line)
- ...
Heat signal: [cold/warm/hot/thermonuclear] with one-phrase reason
Interest signal: [niche/relevant/magnetic/gravitational] with one-phrase reason
```

Agent 3 additionally returns:

```
Paper type: [wording/proposal/directional] with one-phrase reason
Ecosystem repos:
- repo-url - one-line description
- repo-url - one-line description
```

Total token cost of all three returns combined: under 600 words. The main agent reads three heat signals, three interest signals, the paper type classification, the ecosystem repo list, and three finding blocks, then picks the heat tier and interest tier before proceeding with thread generation.

---

## Phase 0: Directory Scan

Before reading the paper, scan the output directory to determine whether this is a new paper, a re-run of the same revision, or a new revision of an existing paper.

1. List all `*-reddit.html` files in `.report/reddit/` (excluding `index.html`)
2. Parse each filename to extract the base paper number and revision: `p{NNNN}r{N}` splits into base `p{NNNN}` and revision `{N}`
3. Compare the target paper against what exists:

**Case A: New paper.** No files exist with the same base paper number. Proceed with a standard fresh thread. Omit `prior_revision` from the thread metadata.

**Case B: Same paper, same revision.** A file already exists with the exact document number (e.g. generating P2900R14 and `p2900r14-reddit.html` exists). Overwrite it. No special thread framing needed. The index rebuild will pick up the updated thread metadata.

**Case C: New revision.** File(s) exist for the same base paper but with a different revision (e.g. generating P2900R15 and `p2900r14-reddit.html` exists). This is an **update thread**:
- Read the highest existing revision's thread HTML. If it has a `<script id="thread-meta">` block, parse the JSON. If not, extract metadata from the `<title>` and `.submission` div
- Read both the new paper and the prior revision to identify what changed between them
- Set `prior_revision` to the document number of the highest existing revision
- Thread generation follows the update-thread rules in Sections 3 and 10

---

## Phase I: Intelligence

Read the paper. Research the landscape. Calibrate the heat. Sequential - must read before researching.

### 1. Read the Paper

Full read plus a light red-team analysis. Two passes.

**First pass: surface extraction.** Read end to end, extracting:

- **Hot takes** - claims that will trigger surface reactions (noise fuel)
- **Misconception traps** - things easy to misread from the abstract alone
- **Tangent magnets** - topics adjacent to the paper that r/wg21 will veer toward (Rust, build systems, ABI, compile times)

**Second pass: the smell test.** The Mod reads the paper like a long-time r/wg21 regular who actually clicks through to the PDF. Find the real weak spots that sharp commenters will notice.

- **1.1 Find the load-bearing claims.** Not every sentence - the 5-10 claims the paper's argument stands on. The ones where, if you pull one out, something collapses.
- **1.2 Kick the tires.** Three checks per claim:
  - **Does it add up?** Do the numbers, dates, quotes, and technical properties hold?
  - **Does it follow?** Is there a logical gap where the conclusion leaps past the premises?
  - **Do the receipts match?** Do the cited sources actually say what the paper claims?
- **1.3 The bullshit filter.** For each candidate weak spot, two kill filters and one routing filter:
  - **Did the author already cop to it?** If the paper openly concedes the limitation, kill it.
  - **Is it a strawman?** Does the paper actually claim what this weakness attacks? If not, kill it.
  - **Would a real person catch this?** This filter no longer kills findings - it routes them. High-visibility findings (obvious on a careful read) become top-level signal comments or encounter fuel. Moderate findings (require reading the whole paper) surface as nested replies or parentheticals in longer comments. Subtle findings (require cross-referencing with other papers or deep domain knowledge) surface as a comment from an "I actually read the whole thing" character at depth 2-3.
- **1.3b The exclusion list.** The following are editorial, not substantive, and never surface as comments:
  - Typos, missing semicolons, identifier misspellings
  - Formatting issues (indentation, line breaks, section numbering)
  - Minor wording ambiguities that only an editor would flag
  - Draft artifacts (TODO markers, placeholder text)
  - These are not the kind of thing Reddit users post about. Nobody says "you misspelled `noexcept`."
- **1.4 The survivors.** Every weak spot that passes filters 1 and 2 and is not on the exclusion list becomes a **technical anchor**. All technical anchors **must** surface in the thread as at least one comment - no anchor gets dropped to save space or fit a comment count target. If a cold paper has 5 technical anchors, it gets 5 signal comments minimum (plus noise). The heat and interest tiers set the floor for total comments; technical anchors can push that floor up.
- **1.4b Design tension.** Zero technical anchors from the tire-kick does not mean a quiet thread. If the paper is technically tight, signal still comes from **design tension** - alternative approaches, tradeoff disagreements, scope questions, "why not X instead" angles. Even a bulletproof paper has surfaces to analyze. The thread never lacks signal.
- **1.4c Falsification requirement.** Before marking a load-bearing claim as a technical anchor, the Mod must attempt to defend it using only material already in the paper. If a complete defense exists within the paper's own text, the claim is not a weak spot - kill it. Only claims that cannot be defended from within the paper survive as anchors. This filter runs after filters 1 and 2 in §1.3 and before the routing filter.
- **1.4d Anchor priority routing.** Technical anchors fall into two classes:
  - **Miss anchors** - the paper does not address X, but X is relevant and a sharp reader would notice the absence.
  - **Inconsistency anchors** - the paper addresses X but its treatment is internally contradictory or its stated scope conflicts with what the proposed changes actually do.

  Inconsistency anchors are higher value. When both types are present, inconsistency anchors get the TEASER slot. Miss anchors surface in signal comments or encounters.
- **1.4e Framing audit (argument papers only).** If the paper's primary purpose is to recommend a direction or advocate a position - rather than propose concrete wording or ask a design question - run a framing audit before generating anchors. Identify the one or two premises the paper needs the reader to accept before the conclusion follows. These premises are mandatory anchors regardless of how well-supported they appear in the paper's own text. Frame the resulting comments as "what you have to accept before the argument works" rather than "what the paper got wrong."
- **1.4f Underspecified sections.** During the first pass, flag any section where the paper states a scope but provides treatment of one sentence or a placeholder. In directional papers targeting multiple audiences, these are mandatory anchors - if the paper claims to address X but devotes one sentence to it, that gap must surface in at least one comment. The comment should identify which target audience (EWG, SG12, etc.) is most likely to object to the thin treatment, and why that section matters to them specifically.
- **1.4g Feature test macro check.** For any paper that proposes behavioral changes to existing standard library or language features - including defect fixes that alter observable behavior - check whether the paper recommends a `__cpp_` feature test macro bump. If behavioral changes are present and no bump is recommended, this is a mandatory anchor. The comment should identify the specific behavioral change and explain what user code would need the macro to detect it.

The smell test is internal. No output file. The user sees Reddit comments that happen to be suspiciously on target.

---

### 2. The Heat Check

Before generating a single comment, calibrate how big and how heated the thread should be.

**2.1 Audience classification.** Extract the target audience from the paper's front matter. Baseline discussion volume:

- **CWG / LWG** (wording/bugfix) - low. 5-15 comments. Mostly technical, little noise.
- **EWG** (evolution) - medium. 15-40 comments. Depends on topic.
- **LEWG** (library evolution) - high. 20-60 comments. Libraries are visible, opinions are cheap.
- **SG1 / SG9 / SG14** etc. (study groups) - variable. Thread size depends on public visibility.
- **Plenary / multiple audiences** - highest. Big papers that cross groups get the most attention.

**2.1b Paper type.** Classify the paper before calibrating heat or interest:

- **Wording** - has proposed wording, concrete changes to the standard text. Default interest floor: niche.
- **Proposal** - has an ask (straw polls, design questions) with concrete direction but may lack final wording. Default interest floor: relevant.
- **Directional** - no wording, no concrete ask. Position paper, direction paper, problem statement, "should we" paper, SD-x document. Default interest floor: magnetic. Default heat floor: hot. The tool cannot assign cold or warm to a directional paper.

Directional papers include any paper whose primary purpose is to set direction, frame a problem space, or ask the committee to commit to a goal without supplying proposed wording. If the paper's title or abstract contains phrases like "should we," "direction for," "a plan for," "towards," or "position on," classify as directional.

**2.1c Interest field requirement.** The interest field in the thread metadata JSON is mandatory. It must be one of: niche, relevant, magnetic, gravitational. It may not be omitted. The heat and interest tiers are both required to govern downstream parameters.

**2.1d Process document classification.** The following document types have no technical anchors and should skip the smell test entirely:

- Poll outcome documents (e.g. 2026-01 Library Evolution Poll Outcomes)
- Admin telecon agendas and minutes
- Meeting logistics documents

For these, classify heat as cold and interest as niche. Skip §1.1-1.4 entirely. Thread generation draws exclusively from Table C domain 13 (Committee process) for signal content. Signal comments analyze poll splits, forwarding decisions, dissent rationale, and organizational observations. The submission body summarizes which papers moved and what the vote splits were. No encounters.

**2.1e Author gravity.** Certain authorship signals raise the interest floor (never lower it):

- **Directions Group** (SD-x papers, direction papers authored by DG members) - interest floor: gravitational
- **Bjarne Stroustrup** - interest floor: gravitational
- **Herb Sutter** - interest floor: magnetic
- **Committee chairs, editors, or widely recognized figures** (e.g. Hinnant, Niebler, Voutilainen, Wakely, Smith, Revzin, O'Dwyer) - interest floor: relevant
- **Implementers** (authors known to work on GCC, Clang, or MSVC) - interest bump: +1 tier if the paper touches their implementation area

Author gravity is a floor. Sub-agent findings can push interest higher but never below the author floor.

**2.2 Controversy scan.** The three parallel sub-agents from section 0 launch here. Each returns findings and a heat signal. Wait for all three before proceeding.

**2.3 Heat tier.** Combine audience baseline, paper type floors, and controversy scan:

- **Cold** (CWG bugfix, no public discussion) - 5-10 comments, 0 encounters, 0-1 signal comments
- **Warm** (LEWG proposal, some discussion) - 15-30 comments, maybe 1 encounter, 2-4 signal comments
- **Hot** (LEWG/EWG, competing proposals, public debate) - 30-60 comments, 1 encounter likely, 4-8 signal comments
- **Thermonuclear** (contracts, executors, ABI, safety) - 60-150 comments, 1-2 encounters, 8-15 signal comments, multiple sub-threads, at least one [removed by moderator]

Directional papers cannot be assigned cold or warm - their heat floor is hot regardless of audience or controversy scan results.

**2.4 Interest tier.** A second dimension orthogonal to heat. Heat measures controversy and temperature. Interest measures how much the community wants to engage regardless of whether they agree or disagree.

Four tiers with comment-count multipliers applied to the heat tier baseline:

- **Niche** (narrow wording fix, NB comment cleanup, no broader implications) - multiplier: 1x
- **Relevant** (concrete proposal with clear scope, touches things people use) - multiplier: 1.5x
- **Magnetic** (directional paper, affects major language surface area, touches a topic people have opinions about - async, safety, allocators, reflection, modules) - multiplier: 2x
- **Gravitational** (directions group paper, Bjarne paper, papers that reframe an entire problem space, papers with no concrete wording but maximum scope) - multiplier: 3x

The multiplier scales the comment count from the heat tier baseline. A cold paper (5-10 comments) at gravitational interest becomes 15-30. A warm paper (15-30) at gravitational becomes 45-90.

Interest also governs:

- **Thread depth** - higher interest produces deeper signal chains and more encounters
- **Link density** - see section 8
- **Number of distinct angles** - magnetic and gravitational papers get analyzed from multiple domain lenses (embedded, finance, game, library design, compiler, pedagogy, etc.). Gravitational papers must include signal comments from at least 4 different Table C domains
- **Noise-to-signal shift** - as interest rises, signal ratio increases. Gravitational threads are signal-heavy with noise serving as pacing, not filler

Both heat and interest govern downstream parameters. Heat sets the emotional register; interest sets the intellectual engagement depth.

---

## Phase II: Blueprint

Build the thread structure. Can start as soon as Phase I produces the heat tier.

### 3. The Submission Post

The top of the thread.

- **Title:** Paper number followed by paper title, verbatim. `P2900R10 - Contracts for C++`. The paper number IS the r/wg21 convention.
- **Flair:** Derived from audience. "WG21" or "Standards" or audience-specific.
- **Poster:** A power user or regular. Generated username from section 5.
- **Link:** Resolution cascade - try `https://wg21.link/pNNNNrN` first. If unreachable, try `https://www.open-std.org/jtc1/sc22/wg21/docs/papers/YYYY/pNNNNrN.html` (and `.pdf`). Must be a real, clickable URL.
- **Body:** Two parts:
  - Metadata block: author, document number, date, target audience, revision, link
  - Paraphrase: 2-4 paragraphs summarizing the paper in Reddit voice. Not the abstract copied - how a knowledgeable r/wg21 regular would explain why someone should care. Accessible, slightly informal, hits the key points.

**Update-thread rules (Case C from Phase 0):**

When generating a thread for a new revision of an existing paper:

- **Title:** Same format (`P2900R15 - Contracts for C++`) - unchanged
- **Flair:** Add `[Update]` flair alongside `WG21`
- **Body:** After the standard metadata block, add a paragraph noting this is a revision update. Reference the prior revision by document number and link to the prior thread file (e.g. `<a href="p2900r14-reddit.html">prior thread on R14</a>`). Summarize the key changes between revisions - what was added, removed, or restructured. Two to four sentences.
- **Prior threads:** If multiple prior revisions exist in the output directory, link to all of them, newest first

---

### 4. Thread Architecture

Scaled by heat tier and interest tier together.

- **Comment count** - heat tier baseline (see 2.3) multiplied by interest multiplier (see 2.4). Then raised further if the number of technical anchors exceeds the signal comment count the formula produces.
- **Nesting** - max depth ~6. Noise sits top-level or depth 1. Signal at depth 2-4. Encounters at depth 3-5. Higher interest pushes signal deeper and sustains longer reply chains.
- **Timing** - relative timestamps: "3 hr. ago", "47 minutes ago". Early comments are noise. Signal arrives later. Encounters develop over "hours." Gravitational threads span a full day of activity.
- **Votes** - sarcastic one-liners get high upvotes. The best technical comment might have 12 points buried under a quip with 340. One correct comment at -47.
- **Furniture** - [deleted], [removed by moderator], awards, "Edit:", "Edit2:", controversial daggers (Unicode dagger next to score), "sorted by: best", collapsed low-score comments.
- **Domain coverage** - magnetic threads must include signal comments from at least 3 different Table C domains. Gravitational threads must include at least 4. Each domain lens produces a distinct comment or encounter that analyzes the paper from that perspective.
- **Encounter scaling** - encounters scale with interest, not just heat. Niche: 0 encounters. Relevant: 0-1. Magnetic: 1-2. Gravitational: 2-3. These stack with heat-tier encounter floors.

---

### 5. Username Generation

Generated per-thread, never reused across runs. Two mechanisms:

**Palette components** - three slots that concatenate:

| Slot | Role | Examples |
|------|------|----------|
| A (prefix) | adjective, label, domain signal | `daily_`, `not_a_`, `actually_`, `senior_`, `the_real_`, `just_a_`, `former_`, `yet_another_`, `lord_`, `xX_` |
| B (core) | C++ culture, internet culture, job title | `template_wizard`, `cpp_dev`, `segfault_enjoyer`, `coroutine_hater`, `allocator_guy`, `undefined_behavior`, `build_system_victim`, `constexpr_everything`, `linker_error`, `move_semantics` |
| C (suffix) | number, year, tag, or empty | `_2019`, `_42`, `_cpp`, `_irl`, `_420`, `_xx`, `_throwaway`, nothing |

Pick one from each, concatenate. `daily_template_wizard_2019`, `not_a_real_cpp_dev`, `former_boost_contributor`.

**LLM synthesis** - palette seeds but does not limit. Also generate from Reddit naming conventions: gaming references (`masterchief_117`), ironic self-description (`compiles_first_try`, `definitely_knows_what_volatile_does`), random word pairs (`turbo_llama_9000`), throwaways (`throwaway_84729`, `definitely_not_a_committee_member`).

**Constraints:**
- No real person's name or recognizable handle from the C++ community
- Noise usernames lean absurd/memey (`UB_enjoyer_69`). Signal usernames lean plausible (`async_skeptic`, `embedded_for_20_years`)
- At most one `[deleted]` user per thread

---

### 5b. Mod Presence

Not every thread. 30-40% of runs. Green distinguished username with `[M]` tag. Mod usernames are drawn from the **Mod Roster** table below - pick one appropriate to the action. `AutoModerator` handles pinned metadata comments. Human mods handle removals, warnings, and locks.

**What mods do:**

- **Pin a comment** - "Reminder: be civil. The paper authors sometimes read these threads." One or two sentences.
- **Remove a comment** - `[removed by moderator]` gap. Maybe a reply: "what did they say?" / "something about Rust being better, you know the usual."
- **Warn a user** - green-flaired reply: "Rule 3. Take a breath." Two to five words. The mod voice is terse and tired.
- **Lock a sub-thread** - thermonuclear only. One sentence.

**Frequency by heat tier:**
- Cold: no mod
- Warm: maybe a pinned comment
- Hot: pin + one removal or warning
- Thermonuclear: pin + 1-2 removals + warning + possibly a locked sub-thread

---

## Phase III: Cast

Compose the characters and collect external links. Can run alongside Phase II.

### 6. Short Path - Noise

No tables. A palette of descriptors that combine to produce short comments.

**Tone:** sarcastic, confused, angry, smug, memey, earnest-but-wrong, bored, condescending, performatively-tired, deadpan

**Stance:** didn't-read, skimmed-abstract, Rust-evangelist, C-purist, "it's-fine-actually", doomsayer, recruiter-brain, process-cynic, old-guard, student

**Stock phrases** (r/wg21-flavored):
- "committee gonna committee"
- "just use Rust/C/Python"
- "I work on [MSVC/GCC/clang]..."
- "tell me you've never shipped production code without telling me"
- "this is why we can't have nice things"
- "skill issue"
- "Sir, this is a Wendy's"
- "great, another paper that will take 10 years to get through LEWG"
- "can we please just get networking in the standard before I retire"
- "*laughs in compile times*"
- "NB, ill-formed, UB, IFNDR" (standardese leaking)
- "Boost/ASIO/Folly already does this"
- fake proposal wording parody
- compile-time and template metaprogramming as punchline

Pick one from each list. No argumentation profile needed. The technical floor holds: even the dumbest take sounds like a programmer wrote it. Nobody says "lol what." They say "great, another paper that will take 10 years to compile."

---

### 7. Long Path - Signal

Four tables. A long-path character is composed by picking one entry from each.

**Table A: Voice**

| # | Register | Description |
|---|----------|-------------|
| 1 | Informal-precise | Measured, calibrated approval-then-disagreement split. Code over rhetoric. Self-deprecating asides. Under pressure adds examples, not volume. Signs off casually. |
| 2 | Axiom-first | Reframes every question to its root cause before engaging. Mathematical vocabulary - domains, preconditions, guarantees. Flat declaratives without hedging. High conviction. |
| 3 | Charismatic-kinetic | Provocative opener, pop-culture references, builds bottom-up from the simplest case. Humor aimed at absurdity and the gap between aspiration and reality. Never at persons. |
| 4 | Ultra-terse | Quotes then contradicts. Binary confidence. Socratic questions as traps. One-line verdicts. Dark dry humor at systems. Emoticons after barbs without walking them back. |
| 5 | Dense-demolition | Numbered point-by-point. Cross-references and comparisons. Concedes a narrow point then wrecks the conclusion. Dry humor about the standard being "dangerously wrong." |
| 6 | Warm-collaborative | Genuine acknowledgment first. Disarming preface before hard positions. Teaching cascade with numbered scenarios. Admits going deep in the weeds. Closes warmly. |
| 7 | Persistent-documentarian | Conversational flow with engineering analogies (building a house, buying faucets). Meta-aware about own persistence. Lettered lists. Under challenge becomes more detailed, not louder. |
| 8 | Implementer-authority | Everything through "will this actually work in my compiler/codebase" lens. Terse. Deployment ratios and complexity budgets. Invites counter-data. Quick to concede when wrong, no drama. |
| 9 | Pedagogical-reveal | Starts from a relatable, normal-looking scenario. Guided tour through the code that "looks fine." Punchline reveal of structural failure. Concrete remedy at the end. |
| 10 | Process-institutional | Numbered theses. "What this means / what this doesn't mean" framing. Precedent-based reasoning. Procedural under pressure, not rhetorical. Quotes committee decisions and D&E. |
| 11 | Earnest-verbose | Heavy citation and cross-language comparisons. Rhetorical questions. Passionate closing arguments. Cares deeply and it shows. Sometimes too much. |
| 12 | Provocative-imperative | "Stop Using X" hooks. Blunt practice rules from measurement. Live discovery energy. Self-corrects in follow-ups if wrong. |

**Table B: Argumentation**

| # | Pattern | Description |
|---|---------|-------------|
| 1 | Root-cause reframe | Opens by telling you you're asking the wrong question. Numbered genealogy of prior mistakes. Pivots from symptom to systemic design error. Closes with a flat standard declaration. |
| 2 | Code-first demonstrate | Opens with a working example or gist. Enumerates alternatives methodically. Closes with a link or artifact - the evidence closes the argument, not the speaker. |
| 3 | First-principles build | Demolishes the status quo, then rebuilds from the simplest possible case upward. Names the structural flaw directly. Connects to a broader thesis (composability, leverage, paradigm). |
| 4 | Quote-and-destroy | Opens by quoting the exact claim under attack. Develops counterexamples and decomposition. Flat terminal dismissal in a single line. No summary needed. |
| 5 | Concede-then-weaponize | Courtly opening. Agrees on facts, disagrees on inference. Ramps severity through numbered points. Formal valediction or a wager that ends debate. |
| 6 | Guided-tour reveal | Opens with a relatable scenario. Progressive code that "looks fine." Punchline reveals structural failure. Concrete remedy and values line to close. |
| 7 | Implementation-reality | Opens with "I tried this in our codebase" or deployment data. Develops with complexity budgets and migration costs. Closes with "non-starter" or "works fine" - binary, from experience. |
| 8 | Steel-man-then-judge | Steelmans the opposing position with full credit. Develops careful analysis. Delivers terse binary judgment at the end. The steelman makes the judgment credible. |
| 9 | Inductive-teaching | Opens with minimal example. Builds to principles through progressive examples, often with audience interaction. Closes with a restatement and forward path. Warm sign-off. |
| 10 | Exhaustive-enumeration | Opens with scope and a taxonomy. Develops all branches systematically with tables or lettered lists. Closes with terse classification. No rhetorical flourish. |

**Table C: Domain**

| # | Domain | Lens |
|---|--------|------|
| 1 | Networking / async I/O | io_uring, epoll, sockets, connection lifecycle, protocol state machines |
| 2 | Embedded / firmware | STM32, no heap, deterministic timing, code size, "will this fit in 64K" |
| 3 | Game engines | Frame budgets, ECS, real-time constraints, Unreal/custom engine, "does this add latency" |
| 4 | Finance / HFT | Latency, determinism, market data, "we measured this at the nanosecond level" |
| 5 | Database / storage | Query engines, transactions, B-trees, "we process 2M rows/sec and this matters" |
| 6 | Application developer | "I just want to connect to a DB and not think about allocators." Pragmatic, library-consumer lens. |
| 7 | Compiler implementation | Codegen, optimization passes, ABI, "I implement this in [GCC/Clang/MSVC]" |
| 8 | Template metaprogramming | Concepts, SFINAE, type traits, constexpr everything, "have you considered making this consteval" |
| 9 | Concurrency / parallelism | Atomics, coroutines, structured concurrency, thread pools, "this has a race condition" |
| 10 | Library design / API ergonomics | Naming, overload sets, customization points, "how does a user discover this" |
| 11 | Safety / correctness | Static analysis, UB, contracts, lifetime, "what happens when someone passes nullptr" |
| 12 | Teaching / pedagogy | "How do I explain this to my students." Onboarding cost, learnability, error messages. |
| 13 | Committee process | Consensus, SG routing, poll outcomes, scheduling. "This needs SG1 input first." "I don't see how this gets consensus without addressing the ABI concern." Not a technical domain - a political one. |

**Table D: Behavior**

| # | Pattern | Description |
|---|---------|-------------|
| 1 | Posts once and leaves | Drops a complete thought. Never returns. |
| 2 | Replies to specifics | Engages with 2-3 other comments on technical points. Does not start threads. |
| 3 | Thread starter | Makes a provocative top-level claim that spawns a sub-thread. |
| 4 | Edit warrior | Edits comment 2-3 times. "Edit: I misread section 3." "Edit2: actually I was right." |
| 5 | Delayed return | Returns 3 hours later: "I thought about this more and..." |
| 6 | Code-only | Posts code and a godbolt link. No prose. Maybe a one-line setup. |
| 7 | Questioner | Asks clarifying questions. Does not state opinions. Socratic or genuine. |
| 8 | Manifesto writer | Writes 4+ paragraphs. Replies to every disagreement. Will die on this hill. |

---

### 8. Link Inventory

**Static link table** (sites r/wg21 commenters reference):

| Site | URL pattern | Use |
|------|-------------|-----|
| cppreference | `en.cppreference.com/w/cpp/...` | Standard library features relevant to the paper |
| Compiler Explorer | `godbolt.org/z/XXXXX` | Codegen demos, compile tests |
| wg21.link | `wg21.link/pNNNNrN` | Other papers in the same space |
| GitHub | `github.com/[org]/[repo]` | Compiler repos, library repos, reference implementations |
| CppCon YouTube | `youtube.com/watch?v=XXXXX` | Conference talks on the paper's topic |
| Blog posts | Various | Arthur O'Dwyer, Barry Revzin, Jonathan Boccara, etc. |
| Hacker News | `news.ycombinator.com/item?id=XXXXX` | Meta-commentary, prior discussion |
| lobste.rs | `lobste.rs/s/XXXXX` | Tech community discussion |

**Research-sourced links.** The heat check sub-agents surface real related papers, blog posts, and talks. Signal-tier commenters drop these: "have you seen P3456? It takes a completely different approach." These are real links to real resources discovered during research.

**Ecosystem and library links.** As interest rises, commenters reference real-world implementations. Sub-agents search for:

- GitHub repos of libraries in the paper's problem space (Boost, folly, abseil, ranges-v3, libunifex, stdexec, etc.)
- Package manager entries (vcpkg, conan) if notable
- Production deployment references (blog posts about using library X at scale)
- Competing or complementary implementations in other languages if commenters would realistically reference them

These appear naturally in signal comments: "we switched from X to Y at $employer" or "have you looked at github.com/org/repo - they solved this differently." They are never dumped as a link list - each is embedded in an opinion or experience.

**Distribution by interest tier:**
- Niche: 1-3 links total (submission post + maybe one comment)
- Relevant: 3-7 links total, at least 1 external library/repo link
- Magnetic: 7-15 links total, at least 3 external library/repo links, at least 1 conference talk
- Gravitational: 15-30 links total, at least 6 external library/repo links, at least 2 conference talk links

Heat tier modulates which links appear (hot/thermonuclear threads include links that fuel arguments; cold threads include links that inform). Interest tier governs how many.

---

## Phase IV: Thread

Generate the actual comments. Requires Phases I-III.

### 9. Generation Pipeline

The thread is planned as a skeleton, then fleshed out in priority order.

**Step 1: The skeleton.** Decide the comment map: how many total, which are noise/signal/encounter, where they nest, which technical anchors they address, which carry quotes, which carry links, which are tangent threads. Each slot gets a type tag and a one-line note.

**Step 2: The TEASER.** Generate the single best comment first. Every thread above Cold has exactly one comment that is genuinely insightful - a point that makes a WG21 member pause. This comment gets written first while context is freshest.

**Step 3: The encounter.** If the heat tier calls for one, generate the encounter dialogue next. Both characters composed from the signal tables. The exchange built around a real technical anchor. 3-5 turns.

**Step 4: Signal comments.** Generate remaining long-path comments. Each engages with a specific part of the paper, uses a composed character, may carry code or links.

**Step 5: Noise fill.** Generate short-path comments from the palette. Pack around signal content. Top-level, depth-1, tangent thread starters.

**Step 6: Recurring users.** Assign 2-3 usernames to appear more than once. A signal commenter who wrote analysis also drops a shorter reply elsewhere. A noise commenter shows up again in a tangent. Real threads have regulars.

**Step 7: Furniture pass.** Add edits, awards, vote counts, timestamps, [deleted], controversial daggers, collapsed comments, interstitial ads. Final dressing.

---

### 10. Content Rules

**Paper link.** The submission post body includes a clickable link to the paper. Resolution cascade: `wg21.link` first, then `open-std.org`, then workspace D-number. Must be a real URL.

**Paper quotes.** At least 2-3 comments quote directly from the paper using `> quoted text`. Short - a sentence or two. Uses: misunderstanding a quote, challenging a specific claim, riffing on awkward wording, building on a key finding. Quotes must be verbatim.

**Anchor to findings (mandatory).** Every technical anchor from the smell test **must** appear in the thread as at least one comment. No anchor gets dropped to save space, fit a comment count, or because the heat tier is low. If the smell test produced 6 anchors and the heat tier only calls for 4 signal comments, the floor rises to 6. Signal comments and encounters engage with specific anchors - soft benchmarking gets poked, logical gaps get noticed, unsupported claims get quoted and challenged. Comments reference section numbers, table data, code examples. The thread reads like people who opened the PDF.

**Code in comments.** Signal-tier comments sometimes contain code - 3-8 lines in Reddit code blocks. Counter-examples ("you could just do this instead"), breakage demos, clarifications, godbolt links. Syntactically plausible C++. Maybe a small typo someone corrects in a reply.

**Technical floor.** Even noise comments are C++-flavored. Nobody says "lol what." They say "great, another paper that will take 10 years to get through LEWG." The dumbest comment in the thread sounds like a programmer wrote it.

**Tangent threads.** At least one sub-thread per heat tier (cold: 0-1, warm: 1, hot: 1-2, thermonuclear: 2-3) goes off-topic. Build systems, Rust, compile times, benchmarking debates. 2-4 comments, never more. Fork off a top-level comment and go nowhere. Seeded by the tangent magnets from section 1.

**Spam.** 20-30% of runs: one obvious spam comment. New account, irrelevant link (crypto, "10x your C++ skills" course). 0 or negative votes. If mod present, `[removed by moderator]`. If not, sits at -8 with "report and move on" reply.

**Interstitial ads.** 1-2 fake Reddit "Promoted" posts between comment groups. Always for something actually useful to C++ developers. Picked from the ad palette below. Styled distinctly in the HTML (lighter background, "Promoted" tag).

**Update-thread comments (Case C only).** When this is a new revision of an existing paper, the following comment archetypes are required in addition to the standard mix:

- At least 2-3 comments reference what changed from the prior revision
- One signal-tier commenter who "has been following this paper" notices specific changes and comments on whether they improve or worsen the design
- One noise-tier commenter complains about revision churn ("R15? I swear we just had a thread on R14")
- If the changes address concerns raised in the prior thread (visible from reading the prior thread HTML), a commenter notices: "they actually fixed the thing people complained about last time"
- The encounter (if any) may center on whether the revision changes are sufficient or go too far

**10b Author advocacy signal.** Sub-agent 3 returns the author's known public position on the paper's topic if one exists - prior papers arguing the same direction, public talks, known affiliation with a competing design, co-authorship of companion papers. If the author has a documented advocacy position on the paper's core recommendation, one signal comment must surface this context. The comment does not attack the paper's technical findings - those stand on their own merits. It frames the recommendation as advocacy-informed rather than neutral, and notes that the committee will likely want corroborating analysis from authors without a prior position before treating the recommendation as settled. This comment is placed at depth 1-2, never top-level.

**10c Benchmark internal consistency.** During the smell test, for any paper that cites quantitative performance data - cycle counts, throughput numbers, overhead percentages - apply a lightweight arithmetic check. Verify that the numbers are internally consistent: that percentage overhead claims match the ratio of the raw numbers, that benchmark 2 and benchmark 3 do not imply contradictory conclusions, that single-vendor data is not used to support a platform-general claim without qualification. Inconsistencies that survive this check become anchors. The comment quotes the specific numbers and identifies the inconsistency or overgeneralization precisely.

---

### 11. The Encounter

Rules for when two long-path characters collide.

- **Trigger:** A signal comment touches a design tension the paper exposes. Another character has domain expertise on the other side.
- **Frequency:** Cold: never. Warm: 30%. Hot: 70%. Thermonuclear: 90%, possibly two.
- **Shape:** First exchange is polite disagreement. Second sharpens. Third either resolves (one side concedes a point) or narrows (they agree on the real question, disagree on the answer). Never more than 5 exchanges.
- **Location:** Always nested deep - depth 3-5. Never top-level. The reader scrolls past noise to find it.

---

## Phase V: Render

### 12. HTML Output

A single HTML file per thread. Before writing any HTML, ensure the shared stylesheet `reddit-thread.css` exists in the output directory - if missing, extract the CSS block between `BEGIN_CSS` and `END_CSS` markers from this file and write it once. Link to it with `<link rel="stylesheet" href="reddit-thread.css">` in the output `<head>`. Do not embed inline `<style>` blocks.

**Thread metadata block.** Every thread file includes a machine-readable JSON block in `<head>`, used by Phase VI to build index post cards without parsing HTML structure:

```html
<script type="application/json" id="thread-meta">
{
  "paper": "P2900",
  "revision": 14,
  "document": "P2900R14",
  "title": "Contracts for C++",
  "authors": "Joshua Berne, Timur Doumler, Andrzej Krzemienski",
  "audience": "CWG, LWG",
  "date": "2025-02-13",
  "comments": 87,
  "upvotes": 847,
  "upvote_pct": "87%",
  "poster": "standards_watcher_2024",
  "heat": "thermonuclear",
  "interest": "gravitational",
  "flair": "WG21",
  "generated": "2026-04-02"
}
</script>
```

Fields:

- `paper` - base paper number without revision (e.g. `P2900`)
- `revision` - integer revision number
- `document` - full document number (e.g. `P2900R14`)
- `title` - paper title
- `authors` - comma-separated author list
- `audience` - target audience from front matter
- `date` - paper date
- `comments` - total comment count in the thread
- `upvotes` - vote score on the submission
- `upvote_pct` - upvote percentage string
- `poster` - submitter username (without `u/`)
- `heat` - heat tier assigned in Phase I
- `interest` - interest tier assigned in Phase I (niche, relevant, magnetic, or gravitational)
- `flair` - post flair (e.g. `WG21`, `WG21 Update`)
- `generated` - ISO date when the thread was generated
- `prior_revision` - document number of the prior revision this updates. Omitted entirely when there is no prior revision

**HTML structure:**

```html
<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>r/wg21 - {paper title}</title>
  <script type="application/json" id="thread-meta">{ ... }</script>
  <link rel="stylesheet" href="reddit-thread.css">
</head>
<body>
  <div class="thread">
    <div class="subreddit-header">r/wg21</div>
    <div class="submission"><!-- title, votes, metadata, body --></div>
    <div class="sort-bar">sorted by: best</div>
    <div class="comments">
      <!-- nested comment divs -->
      <!-- interstitial ad divs between groups -->
    </div>
  </div>
</body>
</html>
```

**Comment div structure:**

```html
<div class="comment" data-depth="N">
  <div class="votebar">
    <span class="arrow up">&#9650;</span>
    <span class="arrow down">&#9660;</span>
  </div>
  <div class="comment-body">
    <div class="comment-header">
      <span class="username">u/username</span>
      <span class="flair">flair text</span>
      <span class="score">42 points</span>
      <span class="time">3 hr. ago</span>
      <span class="awards">&#127942;</span>
    </div>
    <div class="comment-text">
      <!-- markdown-rendered comment content -->
    </div>
    <div class="comment-footer">
      <span class="action">Reply</span>
      <span class="action">Share</span>
      <span class="action">Report</span>
    </div>
    <div class="replies">
      <!-- nested comment divs -->
    </div>
  </div>
</div>
```

**Special elements:**
- Mod comments: add `class="mod"` and `[M]` badge
- Deleted: `<div class="comment deleted">` with `[deleted]` or `[removed by moderator]`
- Collapsed: `class="collapsed"` with `[+] username - N children`
- Controversial: Unicode dagger `&dagger;` after score
- OP: `class="op"` badge on the submission poster
- Ads: `<div class="promoted">` between comment groups

**12b Prior revision field.** The `prior_revision` field in the thread metadata JSON must be omitted entirely when there is no prior revision. Do not emit `"prior_revision": null`. The manifest rebuild and index logic treat absent fields and null fields differently; null is reserved for fields that exist but have no value, which is not the case here.

**12c Entity rendering.** Before writing any HTML output, apply a full entity encoding pass to all generated text that appears in HTML attribute values or visible text nodes. In particular: `&&` must render as `&amp;&amp;`, `<` and `>` in identifiers must render as `&lt;` and `&gt;`, and all non-ASCII characters in author names must be encoded as named or numeric entities. Thread titles containing operator syntax (e.g. `operator=(X&&) &&`) are a known failure case and must be checked explicitly.

**12d Submission body length.** For thermonuclear and hot papers, the submission body paraphrase is capped at two paragraphs. The smell test has already identified all anchors; the submission body's job is to orient the reader, not to pre-analyze the paper. Token budget freed by the shorter body is allocated to encounter depth and signal comment count.

---

## Ad Palette

| # | Advertiser | Copy |
|---|------------|------|
| 1 | CppCon | CppCon 2026 - Aurora, CO - Early bird ends May 15. The conference for the C++ community. |
| 2 | Boost | Boost 1.88 released. Now with C++23 module support. [boost.org](https://boost.org) |
| 3 | JetBrains CLion | CLion - the C++ IDE that understands your templates. Free 30-day trial. |
| 4 | Compiler Explorer | godbolt.org - because you need to see the assembly. |
| 5 | Meeting C++ | Meeting C++ 2026 - Berlin, November. Call for speakers open. |
| 6 | Effective Modern C++ | Still relevant after all these years. Scott Meyers. O'Reilly. |
| 7 | SonarSource | SonarQube for C++ - find bugs before your users do. |
| 8 | ACCU Conference | ACCU 2026 - Bristol, April. Where the UK C++ community meets. |
| 9 | C++ Alliance | Open source C++ libraries. Community infrastructure. Advancing the language. [cppalliance.org](https://cppalliance.org) |
| 10 | Corosio | Async I/O for C++. Built on Boost. [corosio.org](https://corosio.org) |
| 11 | cppreference | en.cppreference.com - You were going to look it up anyway. |
| 12 | eel.is/c++draft | The latest C++ working draft. Hyperlinked, searchable, always current. [eel.is/c++draft](https://eel.is/c++draft) |
| 13 | isocpp.org | Standard C++ news, status, FAQ, and the direction of the language. [isocpp.org](https://isocpp.org) |
| 14 | C++ Weekly | C++ Weekly with Jason Turner. 400+ episodes of modern C++ in under 10 minutes. [youtube.com](https://www.youtube.com/@caborett) |
| 15 | vcpkg | 2000+ C and C++ libraries, one command. Microsoft's open-source package manager. [vcpkg.io](https://vcpkg.io) |
| 16 | Conan | The decentralized C/C++ package manager. Your dependencies, your server. [conan.io](https://conan.io) |
| 17 | {fmt} | Fast, safe, modern formatting. The library behind std::format. [github.com/fmtlib/fmt](https://github.com/fmtlib/fmt) |
| 18 | C++ Insights | See what your compiler sees. Implicit conversions, template instantiations, desugared syntax. [cppinsights.io](https://cppinsights.io) |
| 19 | Quick C++ Benchmark | Benchmark your C++ side by side. No setup required. [quick-bench.com](https://quick-bench.com) |
| 20 | PVS-Studio | Static analysis for C and C++. Find the bugs your compiler won't. |
| 21 | C++Now | C++Now 2026 - Aspen, CO. The deep-dive conference. Since 2012. |
| 22 | C++ Core Guidelines | Write better C++. Stroustrup and Sutter. [isocpp.github.io/CppCoreGuidelines](https://isocpp.github.io/CppCoreGuidelines/CppCoreGuidelines) |
| 23 | Abseil | Google's open-source C++ library. Battle-tested at scale. [abseil.io](https://abseil.io) |
| 24 | Catch2 | A modern C++ test framework. Expressive assertions, zero boilerplate. [github.com/catchorg/Catch2](https://github.com/catchorg/Catch2) |
| 25 | #include &lt;C++&gt; | The inclusive C++ community. Discord, conferences, resources. [includecpp.org](https://includecpp.org) |
| 26 | Sutter's Mill | Herb Sutter's blog. GotW, trip reports, and the future of C++. [herbsutter.com](https://herbsutter.com) |
| 27 | hackingcpp | Visual cheat sheets and guides for modern C++. The diagrams you wish the textbook had. [hackingcpp.com](https://hackingcpp.com) |
| 28 | NVIDIA stdexec | Reference implementation of P2300 std::execution. Structured concurrency for C++. [github.com/NVIDIA/stdexec](https://github.com/NVIDIA/stdexec) |
| 29 | CppCast | The first podcast for C++ developers. Interviews, news, the committee. [cppcast.com](https://cppcast.com) |
| 30 | C++ Stories | Modern C++ tutorials and deep dives. Bartek Filipek. [cppstories.com](https://cppstories.com) |

---

## Mod Roster

Fixed roster of moderator usernames for r/wg21. These appear in the index sidebar and are the pool from which thread mod actions (Section 5b) draw. Not regenerated per-run.

| # | Username | Role |
|---|----------|------|
| 1 | `standards_shepherd` | Head mod. Created the sub. Pins meta threads. Terse. |
| 2 | `paper_trail_2019` | Most active mod. Removes spam, warns users. Tired energy. |
| 3 | `not_on_the_committee` | Ironic name. Everyone suspects they are. Locks thermonuclear threads. |
| 4 | `cwg_watcher` | Technical mod. Only intervenes on factual errors in titles. |
| 5 | `template_janitor` | Flair mod. Assigns post flairs, fixes formatting. |
| 6 | `AutoModerator` | Bot. Posts paper metadata in pinned comments. Standard Reddit bot behavior. |

---

## Phase VI: Index

After the thread HTML is written, update the index. The index is rebuilt from `manifest.json` and thread metadata each run. No incremental HTML editing ever.

### 13. Manifest Protocol

**13a. File layout.**

```
.report/reddit/
  manifest.json          # current mutable state only
  reddit-thread.css      # shared stylesheet for thread pages
  reddit-index.css       # shared stylesheet for index/archive pages
  index.html             # current live page (rebuilt each run)
  index-1.html           # sealed archive page (frozen)
  index-2.html           # sealed archive page (frozen)
  p2900r14-reddit.html   # thread files...
  p3234r0-reddit.html
```

**13b. manifest.json.**

```json
{
  "page_size": 40,
  "current_page": 0,
  "current_documents": [
    "P3234R0",
    "P2900R15"
  ]
}
```

Fields:

- `page_size` - fixed capacity per page, set to `40`
- `current_page` - page number currently represented by `index.html` (starts at `0`)
- `current_documents` - ordered document-number array for the current live page only, newest first

The manifest stores only current mutable state. It does not grow without bound. Sealed archive pages and thread files are the historical record.

**13c. Rebuild protocol.** Each run, after writing the thread HTML file:

1. **Ensure shared CSS files exist.** If `reddit-thread.css` is missing, extract the CSS block between `BEGIN_CSS` and `END_CSS` from this file and write it. If `reddit-index.css` is missing, extract the CSS block between `BEGIN_INDEX_CSS` and `END_INDEX_CSS` and write it. If either file already exists, leave it alone.

2. **Read `manifest.json`** or create a default one if missing:
   ```json
   {
     "page_size": 40,
     "current_page": 0,
     "current_documents": []
   }
   ```

3. **Assign the document to the current page.**
   - Same revision re-run (Case B): if the document is already in `current_documents`, do not change membership. The rebuild will pick up any changes to the thread metadata.
   - New paper or new revision (Cases A/C): before inserting, check if `current_documents` already has 40 entries. If so, run the page rollover (step 4) first. Then prepend the new document number to `current_documents`.
   - New revision (Case C): the old revision stays wherever it was originally assigned. It is not moved.

4. **Page rollover** (only when `current_documents` has 40 entries and a new post needs to be added):
   - Build the sealed archive page `index-{current_page + 1}.html` from `current_documents`. Use the same rebuild logic as step 5 (shell template, post cards from thread metadata, superseded badges) but substitute the archive pagination template from §16: a "Latest" link to `index.html`, plus an "Older" link to `index-{current_page}.html` if `current_page > 0` (omitted on the oldest archive). Do not copy `index.html` - rebuild from data so the correct pagination is baked in. This sealed archive page is write-once and never modified after creation.
   - Increment `current_page`.
   - Reset `current_documents` to an empty array.
   - Then proceed with inserting the new document into the now-empty current page.

5. **Rebuild `index.html`** from scratch:
   - Start from the index HTML shell template (Section 14)
   - For each document in `current_documents`, read the thread file, parse its `<script id="thread-meta">` JSON block, and build a post card (Section 15)
   - Superseded badge: if two documents in `current_documents` share the same base paper number (e.g. P2900R14 and P2900R15), add a `<span class="flair update-flair">Superseded by R{N}</span>` badge on the older revision's card
   - Pagination nav: if `current_page > 0`, add an "Older" link to `index-{current_page}.html` at the bottom
   - Write the complete `index.html`

6. **Write `manifest.json`.**

### 13d. Page Rollover

Rollover is triggered only by page capacity, not by mailing boundaries. The rule is simple:

1. The current live page fills to 40 posts
2. The next genuinely new post causes rollover
3. The full current page is rebuilt as `index-{current_page + 1}.html` with archive pagination (§16)
4. A new empty current page begins
5. The new post is placed on the fresh page
6. No older post is ever moved between pages

A freshly opened current page may contain only one post. This is intentional and not an error.

### 13e. Recovery

If `manifest.json` is lost or corrupted, reconstruct it:

1. Scan the output directory for `index-*.html` files. The highest number N tells you the last sealed page.
2. Scan for `*-reddit.html` thread files. Parse each file's `<script id="thread-meta">` JSON block to get the document number.
3. For each thread file, check whether its document number appears as a post card in any sealed archive page. If it does, that thread is archived - skip it.
4. Remaining thread files (not found in any archive page) belong to the current page.
5. Reconstruct: `current_page` = N + 1 (or 0 if no archives exist), `current_documents` = the unarchived document numbers sorted by `generated` date (newest first).

### 14. Index HTML Shell

Used as the starting point when rebuilding `index.html`. Before writing, ensure `reddit-index.css` exists in the output directory (see step 1 of the rebuild protocol).

```html
<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>r/wg21 - WG21 Paper Discussions</title>
  <link rel="stylesheet" href="reddit-index.css">
</head>
<body>
<div class="subreddit">
  <div class="subreddit-banner">
    <div class="banner-content">
      <h1>r/wg21</h1>
      <p class="banner-tagline">ISO C++ Standards Discussion</p>
    </div>
  </div>
  <div class="content-layout">
    <main class="post-list">
      <!-- post cards here, newest first -->
      <!-- pagination nav here if archives exist -->
    </main>
    <aside class="sidebar">
      <div class="sidebar-card about-card">
        <div class="sidebar-card-header">About Community</div>
        <p class="about-description">Discussion of WG21 papers, proposals, and the C++ standardization process. Not affiliated with ISO or any national body.</p>
        <div class="about-stats">
          <div class="stat"><span class="stat-number">18,742</span><span class="stat-label">Members</span></div>
          <div class="stat"><span class="stat-number">127</span><span class="stat-label">Online</span></div>
        </div>
        <div class="about-created">Created Feb 12, 2018</div>
      </div>
      <div class="sidebar-card rules-card">
        <div class="sidebar-card-header">r/wg21 Rules</div>
        <ol class="rules-list">
          <li>Paper links must use the document number as title prefix</li>
          <li>Be civil - paper authors read these threads</li>
          <li>No memes, image posts, or off-topic content</li>
          <li>Proposals in flight: no leaking unpublished drafts</li>
          <li>Flair your posts</li>
        </ol>
      </div>
      <div class="sidebar-card mods-card">
        <div class="sidebar-card-header">Moderators</div>
        <ul class="mod-list">
          <li><span class="mod-name">u/standards_shepherd</span></li>
          <li><span class="mod-name">u/paper_trail_2019</span></li>
          <li><span class="mod-name">u/not_on_the_committee</span></li>
          <li><span class="mod-name">u/cwg_watcher</span></li>
          <li><span class="mod-name">u/template_janitor</span></li>
          <li><span class="mod-name">u/AutoModerator</span></li>
        </ul>
        <a href="#" class="message-mods">Message the mods</a>
      </div>
      <div class="sidebar-card related-card">
        <div class="sidebar-card-header">Related Communities</div>
        <ul class="related-list">
          <li>r/cpp</li>
          <li>r/programming</li>
          <li>r/ProgrammingLanguages</li>
        </ul>
      </div>
    </aside>
  </div>
</div>
</body>
</html>
```

### 15. Post Card HTML

Built during rebuild from each thread file's `<script id="thread-meta">` JSON block. All field values come from that metadata.

```html
<div class="post-card" data-document="P2900R14" data-paper="P2900">
  <div class="post-votebar">
    <span class="arrow up">&#9650;</span>
    <span class="post-score">847</span>
    <span class="arrow down">&#9660;</span>
  </div>
  <div class="post-content">
    <div class="post-title">
      <a href="p2900r14-reddit.html">P2900R14 - Contracts for C++</a>
      <span class="flair">WG21</span>
    </div>
    <div class="post-meta">
      Posted by u/standards_watcher_2024 &middot; 87 comments
    </div>
    <div class="post-summary">
      Contracts are in C++26. After the infamous removal from C++20, five years of work in SG21, fourteen revisions, and a plenary vote, the committee approved P2900R14.
    </div>
  </div>
</div>
```

For update threads (Case C), add `<span class="flair update-flair">Update</span>` after the WG21 flair.

The summary is the first 1-2 sentences of the submission body paraphrase.

### 16. Pagination Nav HTML

Added at the bottom of the post list during rebuild when archive pages exist.

On `index.html` (current page):

```html
<div class="pagination">
  <a href="index-{current_page}.html" class="page-link">Older &raquo;</a>
</div>
```

On sealed archive pages (baked in at seal time):

```html
<div class="pagination">
  <a href="index.html" class="page-link">Latest</a>
  <a href="index-{N-1}.html" class="page-link">Older &raquo;</a>
</div>
```

The "Older" link is omitted on the oldest archive page (`index-1.html`). No "Newer" links between archive pages - users jump back to `index.html` for the newest content. This keeps sealed pages truly sealed.

---

## License

All content in this file is dedicated to the public domain under [CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/). Anyone may freely reuse, adapt, or republish this material - in whole or in part - with or without attribution.

---

<!-- BEGIN_CSS
:root {
  --bg-canvas: #030303;
  --bg-body: #1a1a1b;
  --bg-card: #272729;
  --bg-hover: #2d2d2f;
  --bg-promoted: #1a1a1b;
  --border-default: #343536;
  --border-comment: #343536;
  --text-primary: #d7dadc;
  --text-secondary: #818384;
  --text-muted: #6a6b6d;
  --text-link: #4fbcff;
  --text-visited: #a98bdb;
  --text-username: #4fbcff;
  --text-op: #4fbcff;
  --text-mod: #5af078;
  --text-admin: #ff4500;
  --upvote: #ff4500;
  --downvote: #7193ff;
  --upvote-bg: rgba(255,69,0,0.1);
  --downvote-bg: rgba(113,147,255,0.1);
  --award-gold: #ffd635;
  --flair-bg: #272729;
  --flair-text: #d7dadc;
  --promoted-bg: #1e1e1f;
  --promoted-border: #3a3a3c;
  --font-body: 'Reddit Sans', 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  --font-mono: 'Source Code Pro', 'Fira Code', 'Consolas', monospace;
  --content-width: 740px;
  --comment-indent: 22px;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  background: var(--bg-canvas);
  color: var(--text-primary);
  font-family: var(--font-body);
  font-size: 14px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}

.thread {
  max-width: var(--content-width);
  margin: 0 auto;
  padding: 20px 16px;
}

.subreddit-header {
  font-size: 12px;
  color: var(--text-secondary);
  padding: 8px 0;
  border-bottom: 1px solid var(--border-default);
  margin-bottom: 12px;
}

.subreddit-header::before { content: "r/wg21"; font-weight: 700; color: var(--text-primary); }
.subreddit-header::after { content: " - Posted by u/"; }

.submission {
  background: var(--bg-body);
  border: 1px solid var(--border-default);
  border-radius: 4px;
  padding: 16px;
  margin-bottom: 16px;
}

.submission .title {
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 8px;
  line-height: 1.3;
}

.submission .meta {
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 12px;
}

.submission .body {
  font-size: 14px;
  line-height: 1.6;
  color: var(--text-primary);
}

.submission .body p { margin-bottom: 10px; }
.submission .body a { color: var(--text-link); text-decoration: none; }
.submission .body a:hover { text-decoration: underline; }
.submission .body a:visited { color: var(--text-visited); }

.submission .vote-count {
  font-size: 13px;
  font-weight: 700;
  color: var(--text-secondary);
  padding: 6px 0;
  border-top: 1px solid var(--border-default);
  margin-top: 12px;
}

.submission .flair {
  display: inline-block;
  background: var(--flair-bg);
  border: 1px solid var(--border-default);
  border-radius: 12px;
  padding: 1px 8px;
  font-size: 11px;
  color: var(--flair-text);
  margin-left: 8px;
  vertical-align: middle;
}

.sort-bar {
  font-size: 12px;
  color: var(--text-secondary);
  padding: 8px 0;
  margin-bottom: 8px;
}

.comment {
  display: flex;
  padding: 4px 0 4px 0;
}

.comment .votebar {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 24px;
  min-width: 24px;
  padding-top: 4px;
  font-size: 12px;
  color: var(--text-muted);
  user-select: none;
}

.comment .arrow { cursor: pointer; line-height: 1; }
.comment .arrow.up { color: var(--text-muted); }
.comment .arrow.down { color: var(--text-muted); margin-top: 2px; }

.comment-body {
  flex: 1;
  min-width: 0;
  border-left: 2px solid var(--border-comment);
  padding-left: 12px;
  margin-left: 4px;
}

.comment[data-depth="0"] > .comment-body { border-left-color: #545454; }
.comment[data-depth="1"] > .comment-body { border-left-color: #4a6fa5; }
.comment[data-depth="2"] > .comment-body { border-left-color: #6b8e5e; }
.comment[data-depth="3"] > .comment-body { border-left-color: #b8860b; }
.comment[data-depth="4"] > .comment-body { border-left-color: #8b5e8b; }
.comment[data-depth="5"] > .comment-body { border-left-color: #5e8b8b; }

.comment-header {
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.comment-header .username {
  font-weight: 700;
  color: var(--text-username);
  text-decoration: none;
}

.comment-header .username.op { color: var(--text-op); }
.comment-header .username.op::after {
  content: "OP";
  font-size: 10px;
  background: var(--text-op);
  color: var(--bg-body);
  padding: 0 4px;
  border-radius: 2px;
  margin-left: 4px;
  font-weight: 700;
}

.comment-header .username.mod { color: var(--text-mod); }
.comment-header .username.mod::after {
  content: "[M]";
  font-size: 10px;
  color: var(--text-mod);
  margin-left: 4px;
  font-weight: 700;
}

.comment-header .flair {
  background: var(--flair-bg);
  border: 1px solid var(--border-default);
  border-radius: 12px;
  padding: 0 6px;
  font-size: 10px;
}

.comment-header .score { color: var(--text-secondary); }
.comment-header .score.negative { color: var(--downvote); }
.comment-header .dagger { color: var(--text-muted); font-size: 10px; }
.comment-header .time { color: var(--text-muted); }
.comment-header .awards { font-size: 14px; }

.comment-text {
  font-size: 14px;
  line-height: 1.6;
  color: var(--text-primary);
  margin-bottom: 6px;
  overflow-wrap: break-word;
}

.comment-text p { margin-bottom: 8px; }
.comment-text a { color: var(--text-link); text-decoration: none; }
.comment-text a:hover { text-decoration: underline; }

.comment-text blockquote {
  border-left: 3px solid var(--border-comment);
  padding: 2px 12px;
  margin: 8px 0;
  color: var(--text-secondary);
}

.comment-text code {
  background: var(--bg-card);
  padding: 1px 4px;
  border-radius: 3px;
  font-family: var(--font-mono);
  font-size: 13px;
}

.comment-text pre {
  background: var(--bg-card);
  padding: 10px 12px;
  border-radius: 4px;
  overflow-x: auto;
  margin: 8px 0;
}

.comment-text pre code {
  background: none;
  padding: 0;
  font-size: 13px;
  line-height: 1.4;
}

.comment-text .edit {
  font-size: 12px;
  color: var(--text-muted);
  font-style: italic;
  margin-top: 6px;
}

.comment-footer {
  font-size: 12px;
  color: var(--text-muted);
  display: flex;
  gap: 12px;
  padding: 2px 0 4px 0;
}

.comment-footer .action { cursor: pointer; font-weight: 700; }
.comment-footer .action:hover { color: var(--text-secondary); }

.comment.deleted .comment-text {
  color: var(--text-muted);
  font-style: italic;
}

.comment.deleted .username { color: var(--text-muted); }

.comment.collapsed {
  opacity: 0.5;
  cursor: pointer;
}

.comment.collapsed .comment-text,
.comment.collapsed .comment-footer,
.comment.collapsed .replies { display: none; }

.comment.collapsed .comment-header::before {
  content: "[+] ";
  color: var(--text-muted);
}

.replies {
  padding-left: var(--comment-indent);
}

.promoted {
  background: var(--promoted-bg);
  border: 1px solid var(--promoted-border);
  border-radius: 4px;
  padding: 12px 16px;
  margin: 16px 0;
  font-size: 13px;
}

.promoted .promoted-label {
  font-size: 10px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 6px;
}

.promoted .promoted-title {
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 4px;
}

.promoted .promoted-body {
  color: var(--text-secondary);
  font-size: 12px;
}

.locked-notice {
  background: rgba(255, 69, 0, 0.08);
  border: 1px solid rgba(255, 69, 0, 0.2);
  border-radius: 4px;
  padding: 8px 12px;
  font-size: 12px;
  color: var(--text-muted);
  margin: 8px 0;
}

.mod-pin {
  background: rgba(90, 240, 120, 0.06);
  border: 1px solid rgba(90, 240, 120, 0.15);
  border-radius: 4px;
  padding: 8px 12px;
  margin-bottom: 12px;
  font-size: 13px;
}

@media (max-width: 800px) {
  .thread { padding: 8px; }
  :root { --content-width: 100%; --comment-indent: 14px; }
  .submission .title { font-size: 17px; }
}
END_CSS -->

<!-- BEGIN_INDEX_CSS
:root {
  --bg-canvas: #030303;
  --bg-body: #1a1a1b;
  --bg-card: #272729;
  --bg-hover: #2d2d2f;
  --border-default: #343536;
  --text-primary: #d7dadc;
  --text-secondary: #818384;
  --text-muted: #6a6b6d;
  --text-link: #4fbcff;
  --text-visited: #a98bdb;
  --text-username: #4fbcff;
  --text-mod: #5af078;
  --upvote: #ff4500;
  --downvote: #7193ff;
  --flair-bg: #272729;
  --flair-text: #d7dadc;
  --font-body: 'Reddit Sans', 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  --font-mono: 'Source Code Pro', 'Fira Code', 'Consolas', monospace;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  background: var(--bg-canvas);
  color: var(--text-primary);
  font-family: var(--font-body);
  font-size: 14px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}

.subreddit { max-width: 1200px; margin: 0 auto; padding: 0 16px; }

.subreddit-banner {
  background: linear-gradient(135deg, #1a3a5c 0%, #0d1b2a 100%);
  border-radius: 4px 4px 0 0;
  padding: 32px 24px 20px;
  margin-bottom: 16px;
  border: 1px solid var(--border-default);
  border-bottom: none;
}

.subreddit-banner h1 {
  font-size: 28px;
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 4px;
}

.banner-tagline {
  font-size: 14px;
  color: var(--text-secondary);
}

.content-layout {
  display: flex;
  gap: 24px;
  align-items: flex-start;
}

.post-list {
  flex: 1;
  min-width: 0;
}

.post-card {
  display: flex;
  background: var(--bg-body);
  border: 1px solid var(--border-default);
  border-radius: 4px;
  padding: 12px;
  margin-bottom: 8px;
  transition: border-color 0.15s;
}

.post-card:hover { border-color: var(--text-muted); }

.post-card.mailing-card {
  border-left: 3px solid var(--text-mod);
}

.post-votebar {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 40px;
  min-width: 40px;
  padding-top: 2px;
  font-size: 12px;
  color: var(--text-muted);
  user-select: none;
}

.post-votebar .arrow { cursor: pointer; line-height: 1; }
.post-votebar .arrow.up { color: var(--text-muted); }
.post-votebar .arrow.down { color: var(--text-muted); margin-top: 2px; }
.post-score { font-weight: 700; font-size: 13px; margin: 4px 0; }

.post-content { flex: 1; min-width: 0; }

.post-title {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 4px;
  line-height: 1.3;
}

.post-title a {
  color: var(--text-primary);
  text-decoration: none;
}
.post-title a:hover { color: var(--text-link); }
.post-title a:visited { color: var(--text-visited); }

.flair {
  display: inline-block;
  background: var(--flair-bg);
  border: 1px solid var(--border-default);
  border-radius: 12px;
  padding: 1px 8px;
  font-size: 11px;
  color: var(--flair-text);
  margin-left: 8px;
  vertical-align: middle;
  font-weight: 400;
}

.update-flair {
  border-color: var(--text-link);
  color: var(--text-link);
}

.update-flair a { color: var(--text-link); text-decoration: none; }
.update-flair a:hover { text-decoration: underline; }

.mailing-flair {
  border-color: var(--text-mod);
  color: var(--text-mod);
}

.post-meta {
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 6px;
}

.post-summary {
  font-size: 13px;
  color: var(--text-muted);
  line-height: 1.5;
}

/* Sidebar */
.sidebar {
  width: 312px;
  min-width: 312px;
}

.sidebar-card {
  background: var(--bg-body);
  border: 1px solid var(--border-default);
  border-radius: 4px;
  margin-bottom: 12px;
  overflow: hidden;
}

.sidebar-card-header {
  background: var(--bg-card);
  padding: 10px 12px;
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--text-secondary);
}

.about-description {
  padding: 12px;
  font-size: 13px;
  color: var(--text-primary);
  line-height: 1.5;
}

.about-stats {
  display: flex;
  padding: 0 12px 12px;
  gap: 24px;
}

.stat { display: flex; flex-direction: column; }
.stat-number { font-size: 16px; font-weight: 700; color: var(--text-primary); }
.stat-label { font-size: 11px; color: var(--text-muted); }

.about-created {
  padding: 8px 12px;
  font-size: 12px;
  color: var(--text-muted);
  border-top: 1px solid var(--border-default);
}

.rules-list {
  padding: 12px 12px 12px 28px;
  font-size: 13px;
  color: var(--text-primary);
}
.rules-list li { padding: 4px 0; }

.mod-list {
  list-style: none;
  padding: 12px;
}
.mod-list li { padding: 3px 0; }
.mod-name {
  font-size: 13px;
  color: var(--text-username);
  font-weight: 600;
}

.message-mods {
  display: block;
  text-align: center;
  padding: 8px;
  font-size: 12px;
  color: var(--text-link);
  text-decoration: none;
  border-top: 1px solid var(--border-default);
}
.message-mods:hover { text-decoration: underline; }

.related-list {
  list-style: none;
  padding: 12px;
  font-size: 13px;
  color: var(--text-secondary);
}
.related-list li { padding: 3px 0; }

/* Pagination */
.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  padding: 16px 0;
  margin-top: 8px;
  border-top: 1px solid var(--border-default);
}

.page-label {
  font-size: 13px;
  color: var(--text-muted);
}

.page-link {
  font-size: 13px;
  font-weight: 700;
  color: var(--text-link);
  text-decoration: none;
  padding: 6px 12px;
  border: 1px solid var(--border-default);
  border-radius: 4px;
  background: var(--bg-card);
}

.page-link:hover {
  background: var(--bg-hover);
  border-color: var(--text-muted);
}

@media (max-width: 960px) {
  .content-layout { flex-direction: column; }
  .sidebar { width: 100%; min-width: 0; }
  .subreddit { padding: 0 8px; }
  .subreddit-banner { padding: 20px 16px 14px; }
  .subreddit-banner h1 { font-size: 22px; }
}
END_INDEX_CSS -->
