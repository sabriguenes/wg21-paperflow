# r/wg21 - P2300R10 - std::execution

**Posted by** u/daily_paper_bot | 1847 points | 137 comments

> P2300R10 — std::execution — Dominiak, Evtushenko, Baker, Teodorescu, Howes, Shoop, Garland, Niebler, Lelbach — 2024-06-28 — SG1, LEWG
>
> R10 of the senders/receivers paper just dropped. For those keeping score at home, this is the structured async framework that's been in flight since 2020 and is targeting C++26. The big picture: schedulers, senders, receivers, and a pile of algorithms that compose. The small picture: still no type erasure, still no thread pool, still no I/O, still no timers. But hey, the Sudoku solver example is pretty cool.
>
> 22 deferred items and 63 stated limitations. The paper is 200+ pages. God speed to whoever's reviewing this in LEWG. Previous revisions have generated... let's say spirited discussion. Be civil or catch a ban.

---

**u/not_a_coroutine_hater** | 743 points

Great, another revision of P2300 that still doesn't ship a thread pool, type erasure, or I/O. What are we actually standardizing here, a framework for writing frameworks?

> **u/senior_template_wizard** | 512 points
>
> This is the part that kills me. Section 4.1, the "hello world":
>
> ```cpp
> scheduler auto sch = thread_pool.scheduler();
> sender auto begin = schedule(sch);
> sender auto hi = then(begin, []{
>     std::cout << "Hello world! Have an int.";
>     return 13;
> });
> sender auto add_42 = then(hi, [](int arg) { return arg + 42; });
> auto [i] = this_thread::sync_wait(add_42).value();
> ```
>
> The example *literally uses* `thread_pool.scheduler()` but the paper *does not define a thread pool*. It's in the example namespace. So the hello world of the standard async framework depends on a type that isn't in the standard async framework. You can't make this up.
>
>> **u/actually_reads_papers** | 389 points
>>
>> To be fair, the paper explicitly acknowledges this. From §4.20:
>>
>> > "This paper does not propose any concrete execution contexts [...] A separate paper will propose a simple thread pool."
>>
>> But "a separate paper" has been the answer for four years now. P2079 (system_context) exists but it's been stuck in SG1 purgatory. The layering argument makes sense in theory — you standardize the abstraction first, then the concrete things — but in practice it means nobody can actually *use* the abstraction without third-party code or writing their own executor. It's the classic "we'll ship the engine without any wheels" problem.
>>
>>> **u/former_boost_dev_42** | 267 points
>>>
>>> I've been shipping production code on top of libunifex and stdexec for two years now. The "no thread pool" thing is a red herring — every real codebase has its own thread pool anyway, and the whole point is that the *algorithms* compose regardless of which execution context you plug in. The value isn't in `static_thread_pool`, it's in `when_all`, `let_value`, `split`, `ensure_started`, and the structured lifetime guarantees.
>>>
>>> That said, the lack of `any_sender` (type erasure) is genuinely painful. I have a service boundary where I need to return a sender from a virtual function and right now I'm using `exec::any_sender_of<Result, std::exception_ptr>` from stdexec. It works, but it's not standard, and every library I integrate with has its own version. This is the one missing piece that actually blocks adoption at scale.
>>>
>>>> **u/actually_reads_papers** | 198 points
>>>>
>>>> Agreed on type erasure being the real blocker. The paper's position (§4.21) is:
>>>>
>>>> > "Type erasure for senders is important but has significant design space that needs further exploration, particularly around allocator support and the set of completion signatures to erase over."
>>>>
>>>> Which, fine, but this has been the position since R5. At some point "further exploration" starts to look like "we can't agree and we're hoping someone else solves it." The allocator question is real — do you bake an allocator into the type-erased sender? Do you use PMR? Do you SBO? — but these are solvable engineering problems, not open research questions.
>>>>
>>>>> **u/former_boost_dev_42** | 156 points
>>>>>
>>>>> The allocator question is actually harder than it looks. A type-erased sender needs to allocate for the operation state, and that allocation happens at `connect` time, not at sender creation time. So the allocator needs to flow through the receiver, which means the type-erased sender's `connect` signature depends on the receiver's allocator, which means you can't fully erase the sender without also constraining the receiver. It's turtles all the way down.
>>>>>
>>>>> stdexec's `any_sender_of` sidesteps this by just using `std::allocator` and hoping for SBO. It works for 90% of cases but it's not zero-overhead and the embedded/HPC people will (rightly) scream.

---

*📢 Sponsored: [CppCon 2024](https://cppcon.org) — Aurora, CO — Sept 15-20. Early bird registration closing soon. This year's keynote track includes talks on P2300 implementation experience. Use code REDDIT24 for 10% off.*

---

**u/yet_another_cpp_dev** | 421 points

The hello world requires understanding schedulers, senders, receivers, `schedule()`, `then()`, `sync_wait()`, structured bindings, and `optional::value()`. Compare that to:

```python
import asyncio

async def main():
    print("Hello world!")

asyncio.run(main())
```

We're going to lose another generation of developers.

> **u/lord_constexpr_everything** | 334 points
>
> This comparison is dishonest and you know it. Python's asyncio hello world hides an event loop, a coroutine frame allocation, a task scheduler, and exception propagation behind syntax sugar. C++ could do the same thing with coroutines:
>
> ```cpp
> task<void> main_async() {
>     std::cout << "Hello world!\n";
> }
> sync_wait(main_async());
> ```
>
> The reason P2300's hello world looks complex is because it's *showing you the machinery*. That's a feature for the target audience (library authors, framework builders, HPC). The complaint is basically "assembly language is harder to read than Python." Yes. Different tools for different jobs.
>
>> **u/yet_another_cpp_dev** | 201 points
>>
>> I hear you, but the paper *presents it as a hello world*. Section 4.1 is literally titled "Hello world." If your hello world requires 6 concepts and a structured binding to unwrap an optional tuple to get a single int, maybe don't call it a hello world? Call it "minimal scheduler example" and lead with the coroutine integration instead.
>>
>> `auto [i] = this_thread::sync_wait(add_42).value();` — you need structured bindings to unwrap an optional tuple just to get a single int back. In Rust this is just `.await`. The ergonomics matter.
>>
>>> **u/lord_constexpr_everything** | 178 points
>>>
>>> The `optional<tuple<...>>` return from `sync_wait` is actually load-bearing though. The `optional` is empty when the sender completes with `set_stopped` (cancellation). The `tuple` is because senders can complete with multiple values. If you collapse those away for ergonomics, you lose information about what happened.
>>>
>>> Could there be a `sync_wait_value` that returns `T` directly and throws on cancellation/error? Sure. But that's a convenience wrapper, not a design flaw. The paper is showing the *primitive*.
>>>
>>> I do agree the paper could lead with coroutine interop. Section 4.8 shows `co_await` on senders and it's much more approachable.

---

**u/segfault_enjoyer_irl** | 287 points

This is the executors proposal all over again. By the time it ships, Rust will have had stable async traits for 5 years and nobody will care.

> **u/not_a_rust_evangelist** | 198 points
>
> Rust's async story is *also* a mess, just differently. No async traits in stable until recently, no async drop, `Pin` is a footgun, every runtime is incompatible, and `Send + 'static` bounds infect everything. The grass is not greener, it's just a different color of brown.
>
>> **u/daily_rustacean_cpp** | 145 points
>>
>> As someone who writes both: Rust's async is more *usable* today despite the warts. I can `tokio::spawn` a future and `.await` it. The ecosystem converged on tokio. In C++ I can't even do the equivalent without pulling in a non-standard library.
>>
>> That said, P2300's *model* is genuinely better than what Rust has. Structured concurrency with guaranteed cleanup, no `'static` bounds needed because lifetimes are structural, no `Pin` because operation states are pinned by the framework. If C++ actually ships this and the ecosystem adopts it, it'll be superior. The question is whether that ever happens.
>>
>>> **u/not_a_rust_evangelist** | 112 points
>>>
>>> "If C++ actually ships this and the ecosystem adopts it" is doing mass-of-Jupiter levels of heavy lifting in that sentence.

---

**u/allocator_guy_cpp** | 256 points

Can we talk about the `connect()` customization priority? From §11.5.1:

> "If the sender has a member `connect`, that is used. Otherwise, the tag_invoke-based customization is used."

So a sender's member `connect()` takes priority over any scheduler-level customization. Doesn't that completely undermine the "scheduler has final say" design principle? If I'm writing a GPU scheduler and I want to intercept `then(snd, f)` to fuse kernels, but the sender returned by a previous algorithm has its own `connect`, my scheduler never gets a chance to see it.

> **u/senior_template_wizard** | 201 points
>
> This is actually more nuanced than it looks. The customization point for *algorithms* (like `then`, `let_value`, etc.) is separate from `connect`. The scheduler gets to customize the algorithm via `transform_sender` in the sender's domain (§11.5.3). The `connect` priority only matters for the final connection step.
>
> So in your GPU kernel fusion example, the scheduler's domain would intercept `then(snd, f)` at the *algorithm level* via `transform_sender` and return a fused sender. That fused sender's `connect` is then the one that runs. The scheduler *does* get final say, just not at the `connect` level — at the `transform_sender` level.
>
>> **u/allocator_guy_cpp** | 167 points
>>
>> Okay, I see. So the layering is:
>>
>> 1. User writes `then(snd, f)`
>> 2. `then` checks the sender's domain for a `transform_sender` customization
>> 3. Domain (controlled by scheduler) can rewrite the whole expression
>> 4. Only *after* domain transformation does `connect` happen
>>
>> That's... actually pretty elegant? But it means the domain/scheduler customization is invisible at the call site. If I'm reading code that says `then(snd, f)`, I have no idea whether that's the default `then` or a completely rewritten operation. That's powerful but also terrifying for code review.
>>
>>> **u/senior_template_wizard** | 134 points
>>>
>>> Welcome to the expression template pattern, 2024 edition. Yes, it's terrifying. Yes, it's necessary for the GPU/HPC use case. The alternative is what CUDA does today: completely separate programming models with no composability. I'll take "invisible but principled customization" over "rewrite your entire codebase for each backend."
>>>
>>> The saving grace is that the *semantics* are preserved. A domain can change *how* `then(snd, f)` executes but not *what* it means. The completion signatures must be compatible. So from a correctness standpoint, you can reason about the code without knowing the domain. From a performance standpoint, you can't. Which is... exactly how allocators work today, honestly.

---

**u/coroutine_hater_2019** | 189 points

> "A sender that has a single value completion signature [...] may be awaited in a coroutine"

So the coroutine interop is basically limited to the simple cases? If my sender can complete with `set_value(int, float)` or `set_value(string)` I can't `co_await` it? That seems like a massive limitation for the one feature that would actually make this approachable.

> **u/actually_reads_papers** | 156 points
>
> It's not as bad as it sounds. Most senders in practice have a single value completion signature — `then(snd, f)` produces whatever `f` returns, `let_value` produces whatever the inner sender produces, etc. The multi-value case is mostly for low-level channel-like primitives.
>
> The real limitation is that `co_await`ing a sender that can complete with `set_error` will throw the error, and `set_stopped` will throw `unspecified`. So you lose the structured error handling that makes senders interesting in the first place. You're basically collapsing the rich completion signal space back down to "value or exception," which is what we already had with coroutines + `task<T>`.
>
>> **u/coroutine_hater_2019** | 98 points
>>
>> So the coroutine bridge is a lossy conversion. Great. That means any codebase that mixes coroutines and senders has to pick one model and stick with it, or deal with impedance mismatches at every boundary. This is going to be `std::string` vs `std::string_view` vs `const char*` all over again, but for async.

---

*📢 Sponsored: [Compiler Explorer](https://godbolt.org) — Now with stdexec trunk support. Try P2300 algorithms live. Donate to keep the servers running.*

---

**u/undefined_behavior_42** | 178 points

22 deferred items. 63 stated limitations. 200+ pages. Been in flight since 2020. At what point do we admit the scope is too large for a single proposal?

> **u/former_lewg_attendee** | 203 points
>
> The irony is that P2300 *is* the result of scoping down. The original executors proposal (P0443) was even larger. P2300 explicitly punted networking, I/O, timers, type erasure, and concrete execution contexts to separate papers. What's left is "just" the core abstraction layer.
>
> The problem is that the core abstraction layer is inherently complex because it has to support GPU dispatch, NUMA-aware scheduling, cancellation propagation, and zero-overhead composition all at the same time. You can't simplify it without cutting a use case, and every use case has a constituency that will block the paper if their thing gets cut.
>
> Committee gonna committee.

---

**u/daily_segfault_enjoyer** | 134 points

They keep claiming compilers can optimize away the sender abstraction entirely but there's not a single benchmark or godbolt link in the whole paper. Section 4.4:

> "In our experience, modern compilers are able to optimize sender/receiver code to be equivalent to hand-written state machines."

"In our experience" is doing a lot of work there. Show me the godbolt. Show me the `-O2` output. Show me a `when_all` of three `then` chains collapsing to a single function call. I've been burned by "zero-cost abstraction" claims before (*cough* `std::function` *cough*).

> **u/former_boost_dev_42** | 167 points
>
> I can actually speak to this from implementation experience. With stdexec on GCC 13 and Clang 17, simple linear chains (`schedule | then | then | then`) do optimize down to basically nothing — the compiler inlines through the `connect`/`start` chain and you get the same codegen as calling the lambdas directly.
>
> Where it falls apart is `when_all`. The operation state for `when_all` contains an atomic counter for synchronization, and the compiler can't optimize that away even when all child senders complete synchronously on the same thread. You end up with atomic decrements that are provably unnecessary but the compiler doesn't know that.
>
> The *really* interesting case is `let_value`, where the operation state of the inner sender is constructed inside the outer operation state. In theory this enables the compiler to see through the whole thing. In practice, the recursive template instantiation sometimes causes the inliner to bail out around depth 4-5.
>
> So: "zero cost" for simple cases, "low cost" for moderate cases, "you're paying for what you're using" for complex cases. Which is honestly fine, but the paper should say that instead of hand-waving.
>
>> **u/daily_segfault_enjoyer** | 89 points
>>
>> This is exactly the kind of analysis that should be *in the paper*. Thank you for actually having implementation experience instead of vibes.

---

**u/not_a_cpp_dev_throwaway** | 112 points

No standard async I/O senders, no networking senders, no file I/O. What exactly are people supposed to DO with this besides schedule lambdas on a thread pool that also doesn't exist?

> **u/lord_constexpr_everything** | 145 points
>
> Parallel algorithms. The `std::execution` algorithms are designed to replace the parallel STL execution policies with something that actually composes. `when_all(on(gpu_scheduler, sort(data1)), on(cpu_pool, sort(data2)))` is the pitch. Whether that pitch lands without a standard scheduler to plug in is... a valid question.
>
>> **u/not_a_cpp_dev_throwaway** | 78 points
>>
>> So the killer app is parallel algorithms, but the parallel algorithms integration is *also* a separate paper (P2500)? We're standardizing the foundation of a building where every floor is a separate paper and none of them have been approved yet. Cool. Cool cool cool.

---

**u/yet_another_segfault** | 95 points

There's zero guidance on how to migrate from `std::future`/`std::async` or Boost.Asio to this. Are existing codebases just supposed to rewrite everything? A migration guide or at least an interop layer would go a long way.

> **u/senior_template_wizard** | 108 points
>
> Boost.Asio interop is actually somewhat addressed by the execution context model — you can write an Asio-backed scheduler that satisfies the `scheduler` concept and then use P2300 algorithms on top of it. Chris Kohlhoff has been involved in the design discussions and Asio's `any_completion_handler` is spiritually similar to what `any_sender` would be.
>
> `std::future`/`std::async` interop is harder because those types have fundamentally different ownership semantics. A `std::future` is a handle to shared state; a sender *is* the computation. You can wrap a future in a sender (poll in `start`, complete when ready) but it's not zero-cost and it defeats the purpose.
>
> Honestly, the migration story is "don't migrate, just use senders for new code and let the old code age out." Which is realistic for greenfield but painful for anyone maintaining a large codebase.

---

**u/daily_build_system_rant** | 67 points

Can't wait to see the compile time impact of 200 pages of template metaprogramming in every translation unit that touches async. My CI is already at 45 minutes.

> **u/constexpr_everything_cpp** | 89 points
>
> stdexec already exists and compile times are... not great. A simple `schedule | then | then | sync_wait` chain adds about 2-3 seconds on GCC 13. `when_all` with 4 branches is closer to 5-6 seconds. For comparison, equivalent Boost.Asio code compiles in under a second.
>
> The concept checks are the main culprit. Every algorithm validates completion signatures at compile time, which is great for error messages but murder for compile times. Modules might help but lol modules.
>
>> **u/daily_build_system_rant** | 45 points
>>
>> "Modules might help but lol modules" is the most C++ sentence ever written.

---

**u/the_wg21_mod** [MOD] | 📌 pinned | 34 points

Reminder: Rule 3 — critique the paper, not the authors. I've already removed two comments that crossed the line. The P2300 authors have put in thousands of hours of work. You can disagree with the design without being a jerk about it. Next violation is a 7-day ban.

---

**u/actually_an_implementer** | 156 points

The cancellation story concerns me. From §4.11:

> "Cancellation is cooperative and flows in the reverse direction: from receivers to senders."

This depends on stop tokens (P2175), which still has unresolved performance concerns in single-threaded scenarios. Every operation state needs to check a stop token, and in the single-threaded case that's pure overhead — you're paying for thread-safe cancellation signaling when there's only one thread. For embedded targets this is a non-starter.

> **u/former_boost_dev_42** | 123 points
>
> The `in_place_stop_token` in the paper is designed to be optimizable in the single-threaded case — the stop callback registration is just a linked list insertion with no atomics if you can prove single-threaded execution. But "can prove" is the operative phrase. In practice, the compiler almost never proves it because the stop source could theoretically be accessed from another thread.
>
> What embedded folks actually want is a `never_stop_token` that the compiler can see is a no-op at compile time. The paper does define `never_stop_token` (§11.4.4) and algorithms are supposed to optimize for it, but it requires the scheduler to propagate `never_stop_token` through the whole chain, which means your scheduler has to opt in. It's solvable but it's not the default, and defaults matter.
>
>> **u/actually_an_implementer** | 98 points
>>
>> Right, and the problem compounds with `when_all`. Even if all child senders use `never_stop_token`, `when_all` itself introduces a `in_place_stop_source` to implement "cancel siblings when one fails." So you pay for stop token machinery in `when_all` even if none of your senders are cancellable. The paper could define a `when_all` specialization for `never_stop_token` children but it doesn't.
>>
>>> **u/former_boost_dev_42** | 87 points
>>>
>>> This is a quality-of-implementation issue, not a spec issue. A conforming implementation *can* specialize `when_all` for the `never_stop_token` case. The spec just doesn't *require* it. Whether that's good enough for embedded folks who need guarantees rather than hopes... probably not. But adding mandatory optimizations to the spec is a can of worms.

---

**u/gpu_shader_bro** | 72 points

The Sudoku solver example in §4.6 is genuinely impressive. Zero `shared_ptr`, zero manual synchronization, structured lifetimes guarantee cleanup. If you haven't read it, go read it. It's the best argument for why this design is worth the complexity.

Say what you will about the learning curve, but try writing that with `std::async` and `std::future` without leaking memory or racing on shutdown. You can't. The structured concurrency guarantees are the whole point.

> **u/segfault_enjoyer_irl** | 43 points
>
> Sure, but how many people are writing concurrent Sudoku solvers vs. "make an HTTP request and parse the JSON"? The paper optimizes for the hard case at the expense of the common case.
>
>> **u/gpu_shader_bro** | 67 points
>>
>> The hard case is where bugs live. "Make an HTTP request and parse the JSON" is easy with any async model. "Make 10,000 HTTP requests, cancel the rest when 3 fail, clean up all resources, and don't leak a single socket" is where `std::async` falls over and senders shine. The Sudoku solver is a teaching example for the *pattern*, not the *problem*.

---

**u/template_wizard_throwaway** | 38 points

committee gonna committee. see you all at R15 in 2027.

> **u/not_a_coroutine_hater** | 52 points
>
> Bold of you to assume R15. I'm betting R12 gets stuck in a naming bikeshed over whether it's `execution::run` or `this_thread::sync_wait` and we lose another 18 months.

---

**u/senior_allocator_dev** | 44 points

Genuine question: has anyone done a survey of how many production C++ codebases would actually adopt this? Every shop I've worked at has their own async framework (usually built on top of libuv or Asio) and the switching cost is enormous. Who is the actual customer here?

> **u/actually_reads_papers** | 76 points
>
> NVIDIA (stdexec), Meta (folly/coro is moving toward senders), Bloomberg (has been experimenting), and the HPC community broadly. The pitch isn't "replace your existing framework" — it's "your next framework should be built on standard primitives so libraries can interop." Right now if library A uses Asio and library B uses folly, you can't compose their async operations. With P2300, both could expose senders and you could `when_all` them.
>
> Whether that pitch is compelling enough to justify the complexity is the trillion-dollar question.

---

*📢 Sponsored: [Boost 1.86](https://www.boost.org) — Now shipping Boost.Cobalt with sender/receiver support. Try structured concurrency today without waiting for C++26.*

---

**u/daily_undefined_behavior** | 29 points

200 pages. I printed it out and it's thicker than my copy of the C++ standard from 1998. We've come so far.

> **u/yet_another_cpp_dev** | 35 points
>
> The real C++26 experience is reading a 200-page paper about async that doesn't include a way to do async I/O.

