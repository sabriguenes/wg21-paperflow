# r/wg21 - P2300R10 - std::execution

**Posted by** u/daily_paper_bot | 1847 points | 137 comments

> P2300R10 - std::execution — Michał Dominiak, Georgy Evtushenko, Lewis Baker, Lucian Radu Teodorescu, Lee Howes, Kirk Shoop, Michael Garland, Eric Niebler, Bryce Adelstein Lelbach — 2024-06-28 — Audience: SG1, LEWG
>
> R10 of the big one. Senders/receivers/schedulers as the composable async foundation for C++. This paper has been through more revisions than most of us have had jobs. The model replaces the old executors proposal with a structured, lazy, composable framework for async work. Think: you describe *what* to run and *where*, and the framework figures out the rest. Coroutines integrate, cancellation is (optionally) supported, and the whole thing is built on concepts and CPOs.
>
> If you've been following P2300 since R0 you know the drill. If you haven't, buckle up — this is the paper that will define how C++ does async for the next 20 years, or die trying. Meta and NVIDIA claim production use. The committee seems to be converging. Whether that's a good thing depends on which 40 comments deep you read.

---

**u/yet_another_cpp_dev** | 743 points

The absolute state of C++ async: we're on revision 10 of a paper to standardize something that Rust shipped in 1.39. Committee gonna committee.

---

&nbsp;&nbsp;**u/not_a_template_wizard** | 412 points

&nbsp;&nbsp;To be fair, Rust shipped async/await syntax. The *runtime* is still "pick your adventure" with tokio vs async-std vs smol vs whatever dropped this week. P2300 is trying to standardize the runtime model too, which is genuinely harder.

---

&nbsp;&nbsp;&nbsp;&nbsp;**u/former_boost_maintainer** | 289 points

&nbsp;&nbsp;&nbsp;&nbsp;This. People keep comparing apples to space shuttles. Rust's async is "here's a trait, write a runtime." P2300 is "here's a complete algebra of async composition with structured lifetime guarantees." You can disagree with the complexity but the scope isn't comparable.

---

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;**u/yet_another_cpp_dev** | 87 points

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Sure, but at some point "genuinely harder" becomes "so hard nobody ships it." We've been saying C++23, then C++26, and I'm starting to hear C++29 whispers.

---

**u/senior_segfault_enjoyer** | 621 points

> A sender describes asynchronous work. It is not the work itself — it is a recipe for work.

Six concepts, a dozen CPOs, and a partridge in a pear tree before you can print "Hello World" asynchronously. I showed the hello world example to a junior dev and they asked if it was a joke.

---

&nbsp;&nbsp;**u/actually_reads_papers** | 534 points

&nbsp;&nbsp;I keep seeing this complaint and I think it's both valid and misleading. Yes, the *conceptual* surface area is large. But the *usage* surface area for 90% of cases is:

&nbsp;&nbsp;```cpp
&nbsp;&nbsp;auto snd = just(42) | then([](int x) { return x + 1; });
&nbsp;&nbsp;auto [result] = sync_wait(std::move(snd)).value();
&nbsp;&nbsp;```

&nbsp;&nbsp;You don't need to understand `set_value_t`, `set_error_t`, `set_stopped_t`, `operation_state`, `receiver`, and `env` to *use* this. You need to understand them to *implement a scheduler*. That's a library author concern, not an application developer concern.

&nbsp;&nbsp;The real question is whether the committee can ship good enough defaults that application devs never touch the concept layer. And R10 is... better at this than R7 was, but still not great.

---

&nbsp;&nbsp;&nbsp;&nbsp;**u/senior_segfault_enjoyer** | 198 points

&nbsp;&nbsp;&nbsp;&nbsp;I hear you, but "you don't need to understand it" falls apart the instant you get a compiler error. Show me the error message when you pass a wrong sender to `then()` on GCC trunk. I'll wait.

---

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;**u/actually_reads_papers** | 267 points

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Oh, the error messages are *atrocious*. No argument there. But that's a QoI issue, not a design issue. Concepts were supposed to fix this and... well. We're working on it. The `transform_completion_signatures_of` machinery in §11.9 is particularly brutal — if you get the template arguments wrong you don't get a nice "hey you messed up," you get 400 lines of nested template instantiation failures. The paper even acknowledges this is a known pain point.

---

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;**u/constexpr_everything_42** | 145 points

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;This is the thing that worries me most about P2300 in practice. `transform_completion_signatures_of` is doing *type-level programming* to compute the output types of composed senders. It's powerful, but it's the kind of thing where a missing `const` on a lambda capture will cascade into an incomprehensible error 8 template instantiations deep. Section 11.9.1 basically admits this:

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;> The template aliases `transform_completion_signatures` and `transform_completion_signatures_of` are used to transform one set of completion signatures into another.

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;The word "transform" is doing a *lot* of heavy lifting. In practice this is a compile-time type-level interpreter. Anyone who's debugged Boost.Hana or Boost.Mp11 metaprograms knows what that means for diagnostics.

---

**u/lord_undefined_behavior** | 487 points

C++26 will ship with three async models: futures (broken), coroutines (half-baked), and senders (incomprehensible). This is fine. Everything is fine.

---

&nbsp;&nbsp;**u/not_a_coroutine_hater** | 203 points

&nbsp;&nbsp;Coroutines aren't half-baked, they're *deliberately minimal*. The machinery is there. P2300 integrates with them. Section 11.6.1 shows how `as_awaitable` bridges senders into coroutine land. The three models aren't competing, they're layered.

&nbsp;&nbsp;...is what I tell myself at 2am debugging a coroutine frame that got destroyed too early.

---

&nbsp;&nbsp;&nbsp;&nbsp;**u/lord_undefined_behavior** | 156 points

&nbsp;&nbsp;&nbsp;&nbsp;Layered like a geological formation. Each layer was deposited by a different civilization that didn't talk to the previous one.

---

---

*📢 Sponsored: [CppCon 2024](https://cppcon.org) — "Structured Concurrency in Practice" track now confirmed. Early bird ends July 15. Use code SENDERS15 for 15% off.*

---

**u/allocator_guy_irl** | 398 points

Can we talk about the elephant in the room? There are **no type erasure facilities** in this paper. Section 4.1 is all concepts and templates. Every sender is a unique type. Every composition creates a new type.

You know what that means? If library A exposes a sender and library B exposes a sender, and you want to store them in the same container or pass them through a virtual interface, you're rolling your own `any_sender<Ts...>`. And everyone will roll it differently. And then we'll get ODR violations when two libraries link against different `any_sender` implementations with the same mangled name.

This is the `std::function` vs raw templates debate all over again, except the types are 10x more complex.

---

&nbsp;&nbsp;**u/former_boost_maintainer** | 312 points

&nbsp;&nbsp;P3325 (`any_receiver`) is the companion paper for this. It's not in P2300 because the committee decided to decouple them. Whether that was wise is... debatable. But the authors are aware of the problem.

&nbsp;&nbsp;The real concern is that P2300 ships in C++26 and P3325 ships in C++29, and we get 3 years of everyone writing their own type erasure layer. Which is exactly what happened with `std::function` and `std::move_only_function`.

---

&nbsp;&nbsp;&nbsp;&nbsp;**u/allocator_guy_irl** | 178 points

&nbsp;&nbsp;&nbsp;&nbsp;So the answer is "yes, it's a problem, and the fix is in a different paper that hasn't been approved yet." Cool. Love it. This is why we can't have nice things.

---

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;**u/daily_cpp_dev** | 89 points

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;To be fair, shipping the core algebra first and type erasure second is defensible. You need to nail down the concept requirements before you can erase them. But yeah, the gap is going to hurt.

---

**u/not_a_segfault_enjoyer_2019** | 356 points

> Meta and NVIDIA claim production use

Where are the benchmarks? Where are the performance numbers? I've seen "we use this in production" from both companies but I have yet to see a single published benchmark comparing P2300-style senders against:

1. Raw thread pools
2. Boost.Asio
3. libdispatch
4. io_uring direct

"Trust us, it's fast" is not engineering. Section 1.2 mentions production deployment but the paper contains zero performance data. For something that's supposed to be *the* async foundation of C++, this is wild.

---

&nbsp;&nbsp;**u/actually_reads_papers** | 289 points

&nbsp;&nbsp;This is a fair criticism but I think it misunderstands what the paper is. P2300 is a *design* paper, not a benchmark paper. It's specifying semantics, not claiming performance characteristics. The production use claims are about *design validation* — "we've used this shape of API and it works" — not "here are our p99 latencies."

&nbsp;&nbsp;That said, I agree the committee should demand implementation experience reports with numbers. stdexec (the reference implementation) is open source. Someone could benchmark it. The fact that nobody has published rigorous numbers is... telling? Or maybe just reflects that the people using it in production can't publish internal benchmarks.

---

&nbsp;&nbsp;&nbsp;&nbsp;**u/not_a_segfault_enjoyer_2019** | 134 points

&nbsp;&nbsp;&nbsp;&nbsp;A design paper for the foundational async abstraction of a systems programming language that doesn't discuss performance characteristics. Read that sentence again. This would not fly in any other systems language community.

---

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;**u/senior_template_wizard** | 201 points

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;I've been running stdexec internally against our io_uring wrapper and the overhead is... fine? The sender/receiver connect/start machinery compiles down to roughly what you'd write by hand, assuming the optimizer can see through the layers. The problem is "assuming the optimizer can see through the layers" — with `-O2` on GCC 14 I see maybe 5-8% overhead on a microbenchmark. With `-O3` and LTO it's within noise.

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;But that's *my* workload on *my* hardware with *my* compiler. The paper should at least have an "expected performance model" section. Something like "senders are expected to be zero-overhead abstractions when fully inlined; implementations should strive for..." etc.

---

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;**u/not_a_segfault_enjoyer_2019** | 67 points

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;5-8% overhead on a microbenchmark is not "fine" for a systems language primitive. That's the kind of thing that compounds across 15 layers of sender composition. But I appreciate you actually measuring something, which is more than the paper does.

---

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;**u/senior_template_wizard** | 112 points

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;To be clear: 5-8% at `-O2` without LTO. At `-O3 -flto` it's within measurement noise. And the 15-layer composition concern is exactly where the lazy evaluation model *helps* — the whole chain fuses into a single operation state. You don't get 15x overhead from 15 compositions. You get one allocation (or zero, if you're on a static thread pool) and one virtual call at the scheduler boundary.

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;The real overhead concern is compile time, not runtime. A 15-deep sender chain generates types that make `std::tuple<std::variant<std::tuple<...>>>` look simple.

---

**u/daily_constexpr_irl** | 267 points

Replacing executors with schedulers feels like renaming chairs on the Titanic. We spent years on P0443 just to throw it away and start over with different vocabulary. The `schedule()` CPO returns a sender that completes on the scheduler's execution context. An executor's `execute()` runs a callable on the executor's execution context. The semantic difference is "lazy vs eager." We burned 5 years of committee time on lazy vs eager.

---

&nbsp;&nbsp;**u/former_boost_maintainer** | 234 points

&nbsp;&nbsp;The lazy vs eager distinction is *the entire point*. Eager execution can't compose. If `execute(ex, f)` runs `f` immediately, you can't attach error handling, cancellation, or continuation without callbacks-of-callbacks. The sender model lets you build the whole pipeline *before* any work starts, which means:

&nbsp;&nbsp;1. The scheduler can see the full graph
&nbsp;&nbsp;2. Cancellation is structural, not cooperative
&nbsp;&nbsp;3. Error propagation follows the composition, not some side channel

&nbsp;&nbsp;P0443 was the wrong abstraction. It took years to figure that out. That's not wasted time, that's the design process working as intended. Slowly. Painfully. But working.

---

&nbsp;&nbsp;&nbsp;&nbsp;**u/daily_constexpr_irl** | 78 points

&nbsp;&nbsp;&nbsp;&nbsp;I don't disagree with the technical argument. I disagree with the timeline. "The design process working as intended" shouldn't take a decade for a systems language that's competing with languages that ship features in 6-week release cycles.

---

**u/coroutine_hater_throwaway** | 245 points

What about cancellation? Section 11.5 makes `set_stopped` *optional*. A sender *may* complete with stopped. A receiver *may* handle it. This means:

- Library A's senders support cancellation
- Library B's senders don't
- You compose them and... what happens?

> If a receiver does not handle the stopped signal, the operation is not stoppable.

So cancellation support is viral in one direction and silently dropped in the other. This is going to be a nightmare for library interop. Every library will need to document "we support cancellation" or "we don't" and users will have to track this manually.

---

&nbsp;&nbsp;**u/actually_reads_papers** | 198 points

&nbsp;&nbsp;This is a real design tension but I think the paper handles it better than you're suggesting. The `stop_token` propagation through environments (§11.5.2) means that if an outer scope requests cancellation, inner senders that *support* it will respond, and inner senders that *don't* will just... complete normally. That's not "silently dropped" — it's "best effort."

&nbsp;&nbsp;The alternative is making cancellation mandatory, which means every sender implementation has to handle `stop_requested()` checks, which adds overhead to senders that don't need it (pure computation, for example). The paper chose composability over uniformity here.

&nbsp;&nbsp;Whether that's the right call... I genuinely don't know. But it's a deliberate choice, not an oversight.

---

&nbsp;&nbsp;&nbsp;&nbsp;**u/coroutine_hater_throwaway** | 102 points

&nbsp;&nbsp;&nbsp;&nbsp;"Best effort cancellation" in a systems language. I'm going to frame that and hang it on my wall.

---

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;**u/actually_reads_papers** | 87 points

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;POSIX `pthread_cancel` would like a word about what happens when you make cancellation mandatory. Sometimes "best effort" is the only sane option.

---

---

*📢 Sponsored: [Compiler Explorer](https://godbolt.org) — Now with stdexec trunk support. See your sender chains compile in real time. Or don't. We're not your mom.*

---

**u/senior_allocator_guy** | 223 points

The missing I/O senders are going to be a problem. P2300 gives you the algebra of async composition but no actual async *operations*. No sockets. No files. No timers (well, there's a sketch of `schedule_at` but it's not concrete). So day one of C++26, you have this beautiful sender framework and... nothing to plug into it except `just()` and `then()`.

> This paper does not propose any I/O senders.

Everyone will write their own `async_read`, `async_write`, `async_accept` senders wrapping platform APIs. We'll get 15 incompatible implementations. This is the executors story all over again — standardize the abstraction, leave the useful bits to the ecosystem, wonder why adoption is slow.

---

&nbsp;&nbsp;**u/not_a_coroutine_hater** | 156 points

&nbsp;&nbsp;P2762 is the I/O senders paper. Same decoupling strategy as type erasure — ship the foundation first, I/O second. I understand the frustration but shipping P2300 + I/O + type erasure as one monolithic paper would guarantee it never lands.

---

&nbsp;&nbsp;&nbsp;&nbsp;**u/senior_allocator_guy** | 98 points

&nbsp;&nbsp;&nbsp;&nbsp;And shipping them separately guarantees a 3-year gap where the foundation exists but is useless for real async I/O. Pick your poison I guess.

---

**u/lord_template_wizard_cpp** | 189 points

I just want to point out that the scheduler customization point interaction is underspecified. If I have a scheduler that wants to enforce an execution policy (e.g., "all work on this scheduler runs with FIFO ordering"), but a sender in the chain overrides `get_completion_scheduler` to point somewhere else, the ordering guarantee is gone. Section 11.3 says:

> A scheduler is a handle to an execution resource that can create senders that will complete on that resource.

But there's no mechanism for a scheduler to *enforce* that composed senders respect its policies. The `get_completion_scheduler` query on a sender's environment can be overridden by any adapter in the chain. So `on(my_fifo_scheduler, some_sender | transfer(other_scheduler))` silently breaks the FIFO guarantee.

---

&nbsp;&nbsp;**u/former_boost_maintainer** | 167 points

&nbsp;&nbsp;This is a good catch. The design philosophy is that `transfer` is an *explicit* context switch — if you write `transfer(other_scheduler)`, you're *intentionally* leaving the original scheduler's domain. The FIFO guarantee applies to work *on that scheduler*, not to work that explicitly transfers away.

&nbsp;&nbsp;But I agree the interaction between scheduler policies and sender adapters could be specified more precisely. Right now it's "don't do that" which is... not great for a standard.

---

&nbsp;&nbsp;&nbsp;&nbsp;**u/lord_template_wizard_cpp** | 76 points

&nbsp;&nbsp;&nbsp;&nbsp;Right, but in a large codebase where senders are composed across module boundaries, "don't do that" becomes "someone will do that and it'll take 3 days to debug." A static check would be nice. Even a `static_assert` that fires when you `transfer` away from a scheduler with ordering guarantees.

---

**u/daily_cpp_dev** | 167 points

Hot take: this is just as complex as Boost.Asio but without 20 years of ecosystem maturity, battle-tested documentation, and Stack Overflow answers. We're trading a known quantity for an unknown one because the known one isn't "modern" enough.

---

&nbsp;&nbsp;**u/former_boost_maintainer** | 198 points

&nbsp;&nbsp;I maintain(ed) Asio-adjacent code for years. Asio is *not* a known quantity. It's a known *minefield*. The strand/executor model has sharp edges that have bitten every team I've worked with. The implicit executor propagation through `async_*` calls is a constant source of bugs. The callback-based composition doesn't support structured concurrency.

&nbsp;&nbsp;P2300 is complex, but it's complex in ways that are *visible*. Asio is complex in ways that are *hidden*. I'll take visible complexity every time.

---

&nbsp;&nbsp;&nbsp;&nbsp;**u/daily_cpp_dev** | 54 points

&nbsp;&nbsp;&nbsp;&nbsp;Fair. I've been bitten by strand lifetime issues more times than I care to admit.

---

**u/not_a_allocator_guy_42** | 134 points

Can someone explain to me why we need senders AND coroutines AND futures? Like, genuinely. I'm a mid-level dev who writes async code daily and I cannot figure out which one I'm supposed to use for what.

---

&nbsp;&nbsp;**u/actually_reads_papers** | 178 points

&nbsp;&nbsp;Short version:

&nbsp;&nbsp;- **Futures** (`std::future`/`std::promise`): Don't use these. They're broken. They allocate, they synchronize, they can't compose. Pretend they don't exist.
&nbsp;&nbsp;- **Coroutines** (`co_await`/`co_return`): Use these when you want to write async code that *reads* like sync code. They're the user-facing syntax.
&nbsp;&nbsp;- **Senders** (P2300): Use these as the *machinery* underneath. Senders are how you describe what runs where. Coroutines are how you write the code that runs.

&nbsp;&nbsp;In practice: you `co_await` a sender. The sender describes the async operation. The coroutine makes it readable. They're complementary, not competing.

&nbsp;&nbsp;`std::future` is the odd one out and should have been deprecated yesterday.

---

&nbsp;&nbsp;&nbsp;&nbsp;**u/not_a_allocator_guy_42** | 67 points

&nbsp;&nbsp;&nbsp;&nbsp;This is the clearest explanation I've seen in 3 years of following this paper. Why isn't this in the paper itself?

---

**u/segfault_enjoyer_2019** | 112 points

laughs in compile times

I just tried building a moderately complex sender chain with stdexec on GCC 14. 47 seconds for one translation unit. The type names in the debug symbols are longer than my commit messages.

---

&nbsp;&nbsp;**u/yet_another_cpp_dev** | 89 points

&nbsp;&nbsp;Template-heavy sender implementations and binary size — name a more iconic duo. I measured a 3x increase in .o file size compared to equivalent callback-based code. Most of it is debug info and template symbol names, but still.

---

&nbsp;&nbsp;&nbsp;&nbsp;**u/senior_template_wizard** | 76 points

&nbsp;&nbsp;&nbsp;&nbsp;`-fno-rtti -ffunction-sections -fdata-sections -Wl,--gc-sections` and the binary size difference drops to ~15%. The debug info bloat is real though. We ended up writing a custom DWARF filter for our CI.

---

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;**u/segfault_enjoyer_2019** | 43 points

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"Just write a custom DWARF filter" is peak C++ energy.

---

**u/the_mod** [MOD] | 1 point

Reminder: Rule 3 — "just use Rust" is not a technical argument. I've removed 4 comments already. Take a breath. Engage with the paper or don't.

---

**u/daily_undefined_behavior** | 98 points

Genuine question: has anyone actually tried to implement a custom scheduler from scratch using only the paper as reference? Not stdexec, not libunifex, just the paper. I tried last weekend and got stuck on the `operation_state` requirements in §11.4. The connect/start protocol is clear enough in isolation but the interaction with `get_env` and completion scheduler queries is... dense.

---

&nbsp;&nbsp;**u/constexpr_everything_42** | 134 points

&nbsp;&nbsp;I did this exercise about 6 months ago with R9. It took me about 3 days to get a basic single-threaded event loop scheduler working. The hardest part wasn't connect/start — it was getting the completion signatures right so that composed senders could query them. The `get_completion_signatures` customization point is where the type-level programming really kicks in.

&nbsp;&nbsp;My advice: start with stdexec's `inline_scheduler` as a reference, then simplify. The paper alone is necessary but not sufficient.

---

**u/actually_a_compiler_dev** | 87 points

From an implementer's perspective: the specification in R10 is *significantly* better than R7-R8. The wording is tighter, the concept definitions are more precise, and the interaction between senders and coroutines via `as_awaitable` is actually implementable now. We've been prototyping this in [redacted compiler] and the main remaining pain point is the `tag_invoke` → explicit customization point transition. The paper is moving away from `tag_invoke` but the migration path isn't fully specified.

---

&nbsp;&nbsp;**u/former_boost_maintainer** | 65 points

&nbsp;&nbsp;The `tag_invoke` removal is the best thing to happen to this paper. That mechanism was clever but it made every customization point a template metaprogramming puzzle. Explicit member functions are boring and that's exactly what we need.

---

**u/not_a_template_wizard** | 76 points

So when does this actually ship? C++26 is the target but the paper is still going through LEWG. Is there any realistic chance this makes it?

---

&nbsp;&nbsp;**u/daily_cpp_dev** | 112 points

&nbsp;&nbsp;The core senders/receivers/schedulers machinery is on track for C++26. The I/O senders, type erasure, and parallel algorithm integration are C++29 at the earliest. So C++26 gets you the engine without the car, and C++29 gets you the car. Maybe.

---

&nbsp;&nbsp;&nbsp;&nbsp;**u/lord_undefined_behavior** | 89 points

&nbsp;&nbsp;&nbsp;&nbsp;Great, another paper that will take 10 years to get through LEWG. At least my grandchildren will have nice async primitives.

---

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;**u/the_mod** [MOD] | 1 point

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;It's literally in the C++26 working draft already. The "10 years in LEWG" meme doesn't apply to papers that are already in the draft.

---

**u/senior_segfault_enjoyer** | 54 points

The thing nobody's talking about: this paper has **nine** authors. Nine. That's either a sign of broad consensus or design by committee (literally). Looking at the author list — NVIDIA, Meta, former Boost — these are serious people. But nine authors means nine opinions on every design decision, and you can feel it in the paper. Some sections read like they were written by a type theory enthusiast and others read like they were written by a pragmatic systems programmer. The tonal inconsistency is a smell.

---

&nbsp;&nbsp;**u/actually_reads_papers** | 43 points

&nbsp;&nbsp;Or it means the design has been validated across multiple domains (GPU compute, social media infrastructure, library design) and the breadth of authorship reflects breadth of applicability. Glass half full.

---

---

*📢 Sponsored: [Boost 1.86](https://www.boost.org) — Now shipping Boost.Cobalt, a coroutine + sender integration library. Because someone had to.*

---

**u/coroutine_hater_throwaway** | 67 points

Prediction: P2300 ships in C++26. Nobody uses it directly. Everyone uses a wrapper library that provides type erasure, I/O senders, and sane defaults. The wrapper library becomes the de facto standard. The committee standardizes the wrapper library in C++32. Circle of life.

---

&nbsp;&nbsp;**u/former_boost_maintainer** | 78 points

&nbsp;&nbsp;You just described the history of every C++ standard library feature. `<algorithm>` → ranges. `<thread>` → jthread. `<future>` → senders. We standardize the wrong thing, then standardize the right thing on top. It's not a bug, it's a tradition.

---

&nbsp;&nbsp;&nbsp;&nbsp;**u/yet_another_cpp_dev** | 45 points

&nbsp;&nbsp;&nbsp;&nbsp;The most depressing accurate take in this thread.

---

**u/daily_undefined_behavior** | 34 points

Rust's async story comparison aside — has anyone looked at how Swift's structured concurrency compares to P2300? The actor model + async let + task groups feel like they solve similar problems with 1/10th the conceptual overhead. I know it's a different language with different constraints but the ergonomics gap is striking.

---

&nbsp;&nbsp;**u/not_a_coroutine_hater** | 56 points

&nbsp;&nbsp;Swift has the luxury of a single compiler, a single runtime, ABI stability guarantees, and reference semantics by default. C++ has none of those. The conceptual overhead in P2300 is largely paying for the flexibility tax of "this has to work on embedded, GPUs, servers, and game engines simultaneously." Whether that tax is worth it is the fundamental question.

---

&nbsp;&nbsp;&nbsp;&nbsp;**u/daily_undefined_behavior** | 23 points

&nbsp;&nbsp;&nbsp;&nbsp;Fair point on the constraints. Though I'd argue "works everywhere" and "works well somewhere" are in tension, and P2300 is firmly in the "works everywhere" camp at the cost of "works well" for any specific domain.

---

**u/actually_reads_papers** | 156 points

Final thought from me on this thread: P2300R10 is not perfect. The missing type erasure hurts. The missing I/O senders hurt. The complexity is real. The compile times are real. The error messages are atrocious.

But this is the first time in 25 years of C++ standardization that we have a *composable*, *structured*, *lazy* async model that actually works across domains. The fact that Meta runs it in production, NVIDIA runs it on GPUs, and it integrates with coroutines — that's not nothing. That's a genuine achievement.

The question isn't "is P2300 perfect?" It's "is P2300 good enough to build on?" And after reading R10 cover to cover, I think the answer is yes. Barely. Grudgingly. With caveats. But yes.

---

&nbsp;&nbsp;**u/senior_segfault_enjoyer** | 67 points

&nbsp;&nbsp;"Barely. Grudgingly. With caveats." should be the tagline for C++ itself.

---

&nbsp;&nbsp;&nbsp;&nbsp;**u/lord_undefined_behavior** | 89 points

&nbsp;&nbsp;&nbsp;&nbsp;Put it on the tombstone.
