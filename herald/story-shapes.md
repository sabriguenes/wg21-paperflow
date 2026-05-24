# Story shapes

A story shape is a named template that defines what kind of article Herald can produce. Each shape declares a trigger (what event in the daily catalog fires it), required evidence (what the brief must contain), eligible journalist beats, and output constraints. The full shape schema and the pipeline that consumes it are documented in `3-writer.md`. This document is a breadth-first inventory - every shape at the one-liner level (name, trigger sketch, hard or soft type). Detailed per-shape definitions (evidence fields, beats, output constraints) are deferred to a later pass.

Each entry below gives:

- **Name** - short, capitalized label
- **Trigger sketch** - one sentence describing what fires this shape
- **Type** - `hard` (deterministic Python predicate on catalog metadata) or `soft` (LLM triage decides)

## Papers and standards process

- **Paper Filed** - a new paper number appears in a mailing or pre-mailing. `hard`
- **Paper Revised** - a paper's revision number increments (R0 to R1, etc.). `hard`
- **Paper Adopted** - a paper moves from a study group to LEWG/LWG or from LEWG/LWG to CWG/LWG for wording review. `hard`
- **Paper Rejected** - a paper receives a "do not pursue" or "not ready" poll result. `hard`
- **Paper Stalled** - a paper has not been revised or scheduled for N consecutive mailings. `hard`
- **Paper Withdrawn** - a paper's author withdraws it from consideration. `hard`
- **Contentious Paper** - a paper generates opposing reactions across multiple sources (mailing list, Reddit, blogs). `soft`
- **Working Group Vote** - a poll result is published for a study group or design group. `hard`
- **Plenary Vote** - a plenary session adopts or rejects a motion. `hard`
- **ISO Milestone** - a working draft reaches CD, DIS, or IS ballot stage. `hard`
- **Defect Report** - a new DR is filed or resolved against the working draft. `hard`
- **Feature Freeze** - the feature set for C++NN is declared closed. `hard`
- **C++ Timeline Update** - the committee publishes or revises the release schedule for a future standard. `hard`
- **National Body Comment** - an NB files comments during a ballot period. `hard`
- **Liaison Activity** - cross-group movement (SG to LEWG, WG14 crossover, WG23 interaction). `soft`

## Mailings

- **Mailing Political Analysis** - a new mailing drops; analyze what the paper advances and stalls reveal about institutional dynamics. `hard`
- **Mailing Data Summary** - aggregate statistics for the mailing: paper counts by working group, author concentration, revision distribution, new-vs-returning author ratio. `hard`
- **Paper Spotlight** - an individual paper from the mailing warrants deep analysis through the political lens. `soft`
- **Mailing Author Watch** - notable new contributors appear or previously active authors drop out of a mailing. `soft`
- **Pre-Mailing Preview** - papers are publicly visible before the official mailing deadline; preview what is coming. `soft`

## Libraries

- **Library Release** - a C++ library ships a 1.0 or new major version. `soft`
- **Library Minor Release Roundup** - several libraries ship minor or patch releases in the same window; aggregate coverage. `soft`
- **Library Deprecation** - a library is deprecated, archived, or declared end-of-life by its maintainer. `soft`
- **Library Fork** - a notable library is forked, signaling a governance or technical split. `soft`
- **Library Maintainer Transition** - a library's lead maintainer changes hands. `soft`
- **Library Security Advisory** - a security vulnerability is disclosed in a C++ library. `hard`
- **Library License Change** - a library changes its license in a way that affects downstream users. `soft`
- **Library Adoption Milestone** - a library crosses a significant adoption threshold (package-manager download count, major project adoption). `soft`
- **Boost Release** - a new Boost release ships. `hard`
- **Boost Library Accepted** - a library passes Boost formal review and is accepted into the collection. `hard`
- **Boost Library Rejected** - a library fails Boost formal review. `hard`
- **Boost Review Roundup** - summary of ongoing or recently completed Boost formal reviews. `hard`

## Toolchain and compilers

- **Compiler Release** - a major compiler (GCC, Clang, MSVC, ICC/ICX) ships a new version. `hard`
- **Compiler Feature Implementation** - a compiler implements a new C++ standard feature (feature X now in compiler Y). `hard`
- **Conformance Snapshot** - periodic comparison of which compilers implement which C++23/26/29 features. `hard`
- **ABI Break** - a compiler or platform announces an ABI-breaking change. `soft`
- **Build System Release** - CMake, Meson, or Bazel ships a release with C++-relevant changes. `hard`
- **Package Manager Release** - vcpkg, Conan, or another C++ package manager ships a release. `hard`
- **Sanitizer Update** - ASan, UBSan, TSan, MSan, or a static analyzer ships a notable update. `soft`
- **IDE and LSP Development** - clangd, MSVC IntelliSense, mrdocs, or another developer-tooling project ships a notable release. `soft`
- **Linker and Runtime Update** - lld, mold, or a C++ runtime library ships a notable release. `soft`

## Conferences

- **Conference Announcement** - a C++ conference publishes dates, venue, or call for papers. `hard`
- **Talk Analysis** - political analysis of a conference talk's framing and implications for the ecosystem. `soft`
- **Conference Daily** - daily summary during an active conference. `hard`
- **Hallway Report** - the political temperature and informal consensus at an event. `soft`
- **Keynote Coverage** - a keynote address at a major conference warrants standalone coverage. `soft`
- **Conference Retrospective** - post-conference synthesis of themes, turning points, and takeaways. `soft`
- **Trip Report Roundup** - multiple attendees publish trip reports; aggregate and contrast their perspectives. `soft`
- **Travel Grant Announcement** - a conference or foundation announces diversity, student, or travel grants. `hard`
- **WG21 Meeting Preview** - an upcoming ISO committee meeting is imminent; preview the agenda and expected decisions. `hard`
- **WG21 Meeting Recap** - an ISO committee meeting concludes; summarize outcomes vs. expectations. `hard`

## People

- **Profile Update** - a person does something notable enough to warrant coverage. `soft`
- **Public Statement Analysis** - political analysis of a public figure's speech, blog post, or interview. `soft`
- **Career Transition** - someone changes employer, role, or committee status in a way that matters to the ecosystem. `soft`
- **Obituary** - a notable figure in the C++ community dies. `hard`
- **New Committee Member** - a new person joins WG21 or a national body delegation. `soft`
- **Committee Role Change** - a chair, vice-chair, or convener role changes hands. `hard`
- **Award or Recognition** - a person receives a notable award (Grace Hopper, ACM, ISO merit). `soft`
- **Anniversary or Milestone** - a person or project reaches a significant anniversary (20 years on committee, 10th edition of a book). `soft`
- **Grant or Fellowship** - a person receives a grant or fellowship relevant to C++ work. `soft`

## Industry and vendor

- **Vendor Announcement** - a compiler vendor, platform owner, or major employer announces something C++-relevant (compiler release, platform decision, language migration). `soft`
- **Vendor Strategy Shift** - a vendor changes strategic direction in a way that affects C++ (memory-safety pivot, language deprecation, rewrite announcement). `soft`
- **Vendor Hiring Signal** - a vendor's job postings or team changes signal a directional shift (new C++ team, C++ team disbanded). `soft`
- **Acquisition or M&A** - a company acquisition affects a C++ project, library, or team. `soft`
- **Large Codebase Migration** - a major project migrates to, from, or within C++ (rewrite in Rust, adopt C++20 modules, modernize legacy codebase). `soft`
- **Big-Tech Open-Source Release** - a large company open-sources a significant C++ project. `soft`
- **Embedded and Real-Time Industry** - a development in the embedded, automotive, or real-time sector affects C++ usage patterns. `soft`

## Government and policy

- **Government Mandate** - a government body issues a regulation, advisory, or procurement rule affecting C++ (memory-safety mandates, language requirements). `soft`
- **Procurement Rule Change** - a defense, aerospace, or critical-infrastructure procurement standard changes its language requirements. `soft`
- **National-Body Position Shift** - a national body changes its voting posture or priorities within ISO. `soft`
- **Regulatory Guidance** - a standards body or regulator publishes non-binding guidance relevant to C++ safety, security, or quality. `soft`

## Security and quality

- **CVE or Incident** - a security incident involves C++ code, a C++ library, or a vulnerability class endemic to C++. `soft`
- **Memory Safety Discourse** - a significant public statement, paper, or report about C++ memory safety appears. `soft`
- **Hardening Update** - a compiler, OS, or platform ships new hardening features, mitigations, or UB-related diagnostics. `soft`
- **Postmortem or RCA** - a published root-cause analysis reveals a systemic C++ issue (use-after-free at scale, integer overflow in critical path). `soft`
- **Defensive Coding Guidance** - a new coding guideline, static-analysis rule set, or best-practice document is published (Core Guidelines update, MISRA update, AUTOSAR update). `soft`
- **Formal Verification Development** - a tool or technique for formal verification of C++ code reaches a notable milestone. `soft`

## Competitive languages

- **Competitor Development** - a competing language (Rust, Carbon, Zig, Swift, Circle, Val/Mojo) ships a release, publishes a roadmap, or reaches a milestone that bears on C++'s position. `soft`
- **Cross-Language Migration Story** - a project publicly documents migrating between C++ and another language. `soft`
- **Polyglot Project Choice** - a significant project chooses its language stack in a way that includes or excludes C++, and the rationale is public. `soft`
- **Interop Development** - a tool, library, or language feature that improves C++ interoperability with another language ships or advances. `soft`
- **Language Benchmark or Comparison** - a credible benchmark, survey, or comparison study positions C++ relative to competitors. `soft`

## Community and discourse

- **Hot Thread Analysis** - a Reddit, Hacker News, or forum thread about C++ generates exceptional engagement or reveals a community fault line. `soft`
- **Blog Post Reaction** - a blog post by a notable author generates significant community response. `soft`
- **Mailing List Debate** - an std-proposals or std-discussion thread becomes heated or reveals a political divide. `soft`
- **Social Discourse Roundup** - aggregated C++ discourse from Mastodon, Bluesky, and X over a time window reveals a trend or shift. `soft`
- **Survey Findings** - Stack Overflow developer survey, JetBrains survey, Reddit polls, or ISO committee surveys publish results relevant to C++. `hard`
- **Community Initiative** - a new community group, working group, mentorship program, or open-source foundation forms around C++. `soft`

## Standing features

- **The Temperature** - monthly sentiment analysis across Reddit, HN, and social platforms. `hard`
- **The Queue** - paper pipeline tracking: which papers advanced, stalled, or were withdrawn since last issue. `hard`
- **The Gap** - PRAGMA ballot data vs. committee priorities: where declared goals and actual progress diverge. `hard`
- **The Calendar** - upcoming deadlines, meetings, conference dates, and mailing cutoffs for the next period. `hard`
- **The Roster** - committee composition snapshot: who joined, who left, role changes since last issue. `hard`
- **The Vendor Watch** - conformance roundup across compilers: what changed since last issue. `hard`
- **The Mailbag** - curated reader questions or community-submitted topics with editorial response. `hard`
- **The Long View** - year-in-review or half-year retrospective synthesizing trends across all coverage areas. `hard`

## Education and books

- **Book Release** - a new C++ book is published or a significant new edition ships. `soft`
- **Course or Curriculum Update** - a university, MOOC, or training provider launches or significantly updates a C++ curriculum. `soft`
- **Tutorial Series Launch** - a notable author or platform publishes a new C++ tutorial series. `soft`
- **Foundation Announcement** - the C++ Alliance, Standard C++ Foundation, or another C++-focused foundation makes a significant announcement. `soft`
- **Mentorship Program** - a new mentorship, scholarship, or outreach program targeting C++ developers launches. `soft`

## Process and meta

- **ISO Procedure Change** - the ISO or WG21 changes its operating procedures (voting rules, document handling, meeting format). `hard`
- **Working Draft Tooling Change** - the tools used to produce the C++ standard draft change (LaTeX to HTML, new document numbering). `hard`
- **Foundation Funding Round** - a C++-focused foundation announces a funding round, budget, or financial report. `soft`
- **Sponsorship Shift** - a major sponsor of C++ conferences, foundations, or tooling starts or stops sponsoring. `soft`
- **Editorial Process Change** - Herald's own editorial process changes in a way the audience should know about. `hard`

---

## Coverage notes

### Shapes carried over from 3-writer.md

The following shapes mirror items already sketched in the "Initial shapes" section of `3-writer.md`. They are included here for completeness so the inventory is self-contained:

- Mailing Political Analysis, Mailing Data Summary, Paper Spotlight (Monthly Anchor stream)
- Talk Analysis, Conference Daily, Hallway Report (Conference Coverage stream)
- Government Mandate, Vendor Announcement, Competitor Development, CVE or Incident (News Response stream)
- Profile Update, Public Statement Analysis, Career Transition (People stream)
- The Temperature, The Queue, The Gap (Standing Features stream)

### Net-new shapes

Everything else in this inventory is new relative to `3-writer.md`. The largest new coverage areas are:

- **Libraries** - entirely absent from the initial shapes. Library Release, Boost formal review coverage, maintainer transitions, and license changes are all new.
- **Toolchain** - the initial shapes folded compiler releases under Vendor Announcement. This inventory gives compilers and build tools their own category with finer-grained shapes.
- **Papers and standards process** - the initial shapes treated the mailing as the unit. This inventory adds per-paper lifecycle shapes (Filed, Revised, Adopted, Stalled, Withdrawn) and per-vote shapes.
- **Education and books** - entirely absent from the initial shapes.
- **Process and meta** - entirely absent from the initial shapes.

### Known gaps

This inventory does not attempt to cover:

- **Long-running saga tracking** (the executors saga, the ranges epoch, the networking TS). These are editorial topics, not shapes - they span many shapes over years. Whether they deserve a dedicated shape ("Saga Update") or are better served by existing shapes (Paper Spotlight, Contentious Paper) is an editorial question.
- **Cross-cutting themes** (ABI stability debate, freestanding, embedded C++). Same reasoning as sagas - these are lenses, not event types.
- **Humor, satire, or opinion columns**. These are journalist-persona concerns, not shape concerns. Any shape can be written with a sardonic angle if the journalist's voice calls for it.

### Overlap risks

Some shapes overlap and may collapse into one during the depth pass:

- **Library Security Advisory** vs **CVE or Incident** - a CVE in a library triggers both. Resolution: Library Security Advisory when the story is about the library and its fix; CVE or Incident when the story is about the vulnerability class or systemic pattern.
- **Compiler Release** vs **Vendor Announcement** - a compiler release is a vendor announcement. Resolution: Compiler Release is the specific shape (structured output with feature tables); Vendor Announcement is the catch-all for vendor news that does not fit a more specific shape.
- **Paper Spotlight** vs **Contentious Paper** - both analyze a single paper. Resolution: Paper Spotlight is mailing-anchored (triggered by the mailing drop, written as part of Monthly Anchor coverage); Contentious Paper is discourse-anchored (triggered by community reaction across multiple sources, written as News Response).
- **WG21 Meeting Preview** and **WG21 Meeting Recap** vs **Conference Daily** - committee meetings are not conferences, but the coverage pattern is similar. Resolution: keep them separate; the evidence requirements and output structure differ (meeting coverage is agenda-driven, conference coverage is talk-driven).
- **National-Body Position Shift** vs **National Body Comment** - both involve national bodies. Resolution: National Body Comment is hard-triggered by a ballot comment filing; National-Body Position Shift is soft-triggered by pattern recognition across multiple votes or statements.
