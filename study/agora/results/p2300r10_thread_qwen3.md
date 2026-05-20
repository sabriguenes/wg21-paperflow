# r/wg21 - P2300R10 - std::execution

**Posted by** u/daily_template_wizard | 842 points | 327 comments

> Document: P2300R10
> 
> This revision proposes a standard execution framework with schedulers, senders, and receivers for composable asynchronous programming. Notable changes include removing type erasure containers and adding "just use Rust" to the non-normative motivation section.
> 
> The paper claims production use at Meta and NVIDIA but provides no benchmark data comparing to existing solutions like folly::Future or CUDA streams. Examples show thread pools without defining their interface, and the "hello world" requires understanding 6 distinct concepts. Critics argue this creates a pedagogical disaster and ABI stability nightmares for distributed systems.

#### u/senior_cpp_dev [score: 562]
> "Schedulers can't enforce execution policies when senders override customization points" (D-C1). This means any library can silently hijack execution context assumptions. Imagine debugging why your GPU scheduler suddenly starts running on a network thread because some transitive dependency's sender did post(). Who's actually responsible for policy enforcement here?

#### u/undefined_behavior_irl [score: 412] GOLD
> The "hello world" example (U-C1) requires: scheduler, sender, receiver, connect(), start(), and then the lambda. That's before explaining cancellation (D-C2) or why I need to reimplement std::socket (E-C3). This isn't pedagogy - it's a hostage situation.

##### u/lord_allocator_guy [score: 203]
> In Section 4.3 they show a thread_pool example but never define its interface (E-C4). Are we supposed to #include <std_compat/linux_only.h>?

#### u/not_a_segfault_enjoyer [score: 387]
> Sponsored by [CppCon](https://cppcon.org): Come watch Lewis Baker's "Why Coroutines Are Still Hard" talk in September. Use code WG21ROXX to get a free t-shirt with undefined_behavior_irl's face on it.

#### u/former_coroutine_hater [score: 301] GREEN FLAIR
> Rule 3. Take a breath. The paper removes type erasure (E-C1) to optimize for zero-cost abstractions. This isn't about interoperability - it's a philosophical choice. Let's discuss tradeoffs without hyperbole.

##### u/undefined_behavior_irl [score: 211]
> But without type erasure you can't pass senders across shared library boundaries safely. How many game studios will adopt this when their audio thread runs DLLs from 2003? (ABI stability tangent magnet)

#### u/yet_another_constexpr_everything [score: 289]
> This is just Rust's async/await with worse ergonomics. Observe:
> rust
> // Rust
> let data = db_query().await;
> 
> cpp
> // C++26 equivalent
> auto q = db_query() | then([](auto data){ /*...*/ });
> start_detached(schedule(my_pool) | then(q));
> 
> Who's maintaining the mental model here?

##### u/senior_cpp_dev [score: 192]
> Except Rust's model can't express pipeline parallelism patterns this enables. See NVIDIA's use case in section 6.3 for stream fusion optimizations.

##### u/former_coroutine_hater [score: 177]
> But the cognitive overhead (U-C1) kills adoption. Maybe we need both models? WG21 should consider ergonomic adapters.

#### u/mod_poster [score: 1028] GREEN FLAIR
> Reminder: Constructive criticism only. "Just use Rust" is tired and unhelpful. 17 people have posted that exact phrase in the last hour.

#### u/actually_build_system_guy [score: 245]
> Build dependency hell incoming: Every sender/receiver combo will require explicit instantiation. Wait until you try debugging why your HTTP client's sender can't connect to the JSON parser's receiver due to a missing trait specialization...

##### u/template_wizard_2019 [score: 156]
> That's why we need concepts! The paper's requires clauses will make errors clearer than SFINAE hell.

##### u/actually_build_system_guy [score: 132]
> Concepts don't fix ODR issues. Try this with two versions of the standard library in the same binary. (ABI tangent continuation)

#### u/daily_template_wizard [score: 198]
> committee gonna committee

#### u/undefined_behavior_cpp [score: 224]
> "Production use claims at Meta/NVIDIA with no performance data" (P-S1). I'll believe it when I see numbers showing this outperforms existing callback chains.

##### u/senior_cpp_dev [score: 167]
> Meta's internal builds have 14% throughput gains on social graph traversals. I saw the perf slides last WG21 meeting.

#### u/former_coroutine_hater [score: 155] GREEN FLAIR
> Tangent alert: Let's keep Rust comparisons to 20% of the thread. We've hit quota. Discuss cancellation semantics instead.

#### u/const_coroutine_guy [score: 143]
> Optional cancellation (D-C2) is a footgun. If my database query can't be cancelled, it should express that in its type. This creates latent bugs in long-running systems.

##### u/yet_another_constexpr_everything [score: 121]
> Mandatory cancellation has its own problems. See: JavaScript's AbortController mess. Need both options.

#### u/not_a_segfault_enjoyer [score: 117]
> Sponsored by [Compiler Explorer](https://godbolt.org): Try writing one of these senders without syntax support. We dare you.

#### u/throwaway_scheduler [score: 98]
> Why does the first example use thread_pool without defining it? This reads like a Boost doc where you spend more time figuring out which macro to use than writing actual code.

#### u/mod_poster [score: 89] GREEN FLAIR
> Reminder: This is a direction paper. Implementation details will follow. Please focus on fundamental objections rather than missing APIs.

#### u/undefined_behavior_irl [score: 83]
> If direction papers ignore ergonomics (U-C1) and interoperability (E-C1), what exactly are we standardizing? The theory of async programming?

##### u/former_coroutine_hater [score: 77]
> Theory that NVIDIA and Meta are using in production (P-S1). Trust the hype.

#### u/senior_cpp_dev [score: 72]
> > "Missing standard I/O operations force reimplementing socket code" (E-C3)
> 
> This is LEWG's problem. The paper explicitly defers to P2131R4 for I/O. Should we block execution on I/O facilities?