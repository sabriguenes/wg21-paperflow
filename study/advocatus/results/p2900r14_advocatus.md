# Animadversiones - P2900R14

---

## Seal

***Cum obiectionibus*** - The cause proceeds with objections.

P2900R14 presents a contracts facility whose performance claims rest on zero empirical data, whose design principles are internally contradicted by its own rules on const_cast and side-effect elision, whose usability is undermined by implementation-defined evaluation semantics that create a pit-of-despair for safety-critical code, and whose explicit exclusion of invariants, virtual function contracts, and coroutine interoperability delivers an MVP so incomplete that it risks locking C++ into a constrained foundation incapable of gracefully accommodating the very extensions it promises.

Confidence: 0.92

---

## Objections

### Objection 1: The proposal asserts that ignoring contract assertions at runtime 'maximizes runtime performance' in Release builds, yet furnishes zero benchmarks, profiling data, or even citation to external measurement to substantiate this claim—a claim that will directly shape vendor default configurations affecting every C++ user.

**Severity:** High

**Explanation:** Line 1317 states that a 'reasonable default configuration for an optimized Release build might be to ignore contract assertions at run time (to maximize runtime performance).' This is presented as self-evident guidance to implementers, yet the paper contains no microbenchmark, no compilation-time measurement, and no throughput comparison between enforce-at-runtime and ignore-at-runtime modes. Implementers such as GCC, Clang, and MSVC will use this language to justify their default flag settings, meaning millions of developers will inherit a default chosen on the basis of an unverified assertion. The parenthetical 'with C++'s usual disregard for moderate increases in compile time' further implies a compile-time/runtime tradeoff that is never quantified. Without data, this is editorial opinion masquerading as engineering guidance.

**Motivatio:**

- **Adversary:** Performance-sensitive WG21 members (e.g., Game Development and Low-Latency SG14 constituency) and implementers (GCC, Clang, MSVC teams) who will challenge unsubstantiated performance defaults
- **Forum:** sg1
- **Damage:** revision_forcing

### Objection 2: The paper makes multiple performance claims across its text yet contains literally no empirical benchmark data anywhere in the document, rendering every performance assertion—from side-effect elision benefits to evaluation overhead—entirely speculative.

**Severity:** High

**Explanation:** Sections that would naturally house implementation experience and measurement (e.g., §4.1 Implementation compliance, §6.4) are either empty or contain no quantitative data in the provided text. The paper claims that side-effect elision is a valuable optimization, that ignoring predicates at runtime maximizes performance, and that multiple evaluations have manageable overhead—all without a single number. This is not a minor editorial gap: P2900 is a language-changing proposal that will impose costs on every conforming implementation. WG21 has historically demanded implementation experience (per the 'Rule of Three' or at least one implementation) before standardizing features of this magnitude. The absence of data invites challenges from National Bodies during the DIS ballot, who may request deferral until implementations can report real measurements.

**Motivatio:**

- **Adversary:** National Body reviewers (BSI, DIN, ANSI/INCITS) who enforce the expectation of implementation experience before standardization
- **Forum:** nb_comment
- **Damage:** paper_killing

### Objection 3: The permission for compilers to elide all side effects of predicate evaluation is justified as a performance optimization, but no measurement demonstrates that this elision produces meaningful speedups in practice, leaving a semantically disruptive rule without empirical justification.

**Severity:** Low

**Explanation:** The elision rule at line 1317 grants compilers extraordinary latitude: they may remove all side effects of a predicate expression when the result is provable at compile time. This is a novel semantic permission with no precedent in the C++ abstract machine for expressions that are syntactically present. The paper justifies it on performance grounds but provides no data showing, for example, that eliding a logging call inside a predicate saves measurable time in a real workload. Compiler teams (particularly Clang's optimizer group) will need to implement complex analysis to determine when elision is safe, imposing engineering cost. Without evidence that the optimization matters, the cost-benefit calculus is entirely one-sided.

**Motivatio:**

- **Adversary:** Compiler frontend engineers (Clang, GCC) who must implement the elision analysis
- **Forum:** lewg
- **Damage:** revision_forcing

### Objection 4: The proposal acknowledges that conforming implementations may evaluate contract assertions multiple times but neither quantifies the performance cost of repeated evaluation nor provides any mitigation strategy, leaving a potentially unbounded overhead entirely unaddressed.

**Severity:** Medium

**Explanation:** Line 1317 states that 'each contract assertion should be evaluated exactly once' but then permits implementations to repeat evaluations. For predicates with non-trivial computational cost (e.g., checking sortedness of a container, validating graph invariants), repeated evaluation could multiply runtime overhead by an unbounded factor. The paper offers no guidance on how implementations should bound repetition, no measurement of the cost in representative workloads, and no mechanism for users to prevent it. This is particularly dangerous for safety-critical systems where timing guarantees matter: an implementation that evaluates a predicate three times instead of once could push a real-time system past its deadline.

**Motivatio:**

- **Adversary:** SG12 (Undefined/Unspecified Behavior) and safety-critical systems advocates (e.g., MISRA C++ working group, Autosar Adaptive Platform developers)
- **Forum:** sg1
- **Damage:** section_weakening

### Objection 5: The explicit exclusion of class invariants—a foundational element of Design by Contract since Bertrand Meyer's original formulation—leaves the proposal unable to express the most common form of object-level correctness guarantee, undermining the claim that this is a comprehensive contracts facility for C++.

**Severity:** Medium

**Explanation:** Section 2.3 lists invariants among intentionally excluded features, deferring them to a future extension. However, invariants are not a luxury: they are the mechanism by which a class guarantees that its representation is valid across all public method boundaries. Without invariants, users must manually insert equivalent precondition and postcondition checks on every member function, which is error-prone and verbose. The Eiffel language, D's contract support, and Ada 2012's Type_Invariant all include invariants as first-class features. By shipping without them, P2900 forces users into a half-measure that cannot express 'this object is always valid'—the single most important contract in object-oriented programming. Future invariant proposals will face ABI and syntax constraints imposed by P2900's choices, potentially resulting in a suboptimal bolt-on design.

**Motivatio:**

- **Adversary:** Herb Sutter and the Cpp2/cppfront community, who have advocated for invariant-centric safety; also Bjarne Stroustrup's long-standing position on type safety through invariants
- **Forum:** lewg
- **Damage:** revision_forcing

### Objection 6: By making the evaluation semantic (enforce, observe, ignore) entirely implementation-defined with no per-assertion control mechanism, the proposal strips developers of the ability to specify that a particular safety-critical check must always be enforced, rendering contracts unreliable for precisely the use cases that need them most.

**Severity:** Medium

**Explanation:** Section 2.3 explicitly excludes 'the ability to express the desired evaluation semantic directly on the contract assertion.' This means a developer writing `pre(ptr != nullptr)` on a safety-critical function cannot guarantee that this check will execute in production—the compiler vendor's flag settings control everything. This contradicts Principle 9 (Independence of Contract-Assertion Evaluations) and makes it impossible to write portable contracts with deterministic behavior. In practice, a team using GCC with `-fcontract-semantic=ignore` will silently skip checks that another team using MSVC with `/contract:enforce` would catch. The result is that contracts become build-system trivia rather than semantic guarantees, undermining the entire value proposition for safety-critical and security-sensitive code.

**Motivatio:**

- **Adversary:** Safety-critical systems developers (automotive, aerospace, medical) represented in SG12 and WG23 (Programming Language Vulnerabilities)
- **Forum:** sg1
- **Damage:** revision_forcing

### Objection 7: Permitting const_cast modifications inside contract predicates directly violates the paper's own Principle 6 (No Destructive Side Effects) and the Prime Directive, creating a sanctioned pathway for predicates to corrupt program state while being nominally 'redundant checks.'

**Severity:** Medium

**Explanation:** Section 3.4.2 at line 842 states that contract_assert predicates may modify const objects via const_cast. This is a direct contradiction of Principle 6, which states that contract predicates should not have destructive side effects. A predicate that const_casts and modifies an object is not a redundant check—it is a state mutation disguised as an assertion. The Prime Directive promises that 'the presence or absence of a contract assertion shall not affect the observable behavior of a correct program,' but a predicate that modifies state via const_cast will produce different program behavior depending on whether contracts are evaluated or elided. This creates undefined behavior in practice (modifying a truly-const object is UB) and a normative contradiction within the paper's own design principles. Reviewers in SG12 will flag this as a safety hole.

**Motivatio:**

- **Adversary:** SG12 (Undefined/Unspecified Behavior study group) and Gabriel Dos Reis, who has historically championed const-correctness guarantees
- **Forum:** lewg
- **Damage:** section_weakening

### Objection 8: Allowing compilers to elide predicate side effects when the result is provable creates observable behavioral differences based solely on contract presence, directly contradicting the paper's Principle 9 and the redundancy model that underpins the entire design.

**Severity:** Low

**Explanation:** Section 3.5.8 permits the compiler to elide all side effects of a predicate when the result can be proven at compile time. This means a predicate like `pre(log_and_check(x))` may or may not execute its logging depending on the compiler's optimization capability—a capability that varies across vendors and optimization levels. The paper's Principle 9 requires that contract-assertion evaluations be independent, but elision makes behavior dependent on compiler intelligence. This is a minor inconsistency in principle but could cause confusion in practice when developers rely on side effects for diagnostics during development.

**Motivatio:**

- **Adversary:** Library authors who embed diagnostic logging in contract predicates (e.g., Bloomberg BDE, Abseil teams)
- **Forum:** reflector
- **Damage:** revision_forcing

### Objection 9: Making the evaluation semantic of every contract assertion implementation-defined creates a pit-of-despair where contracts that protect against null dereferences or buffer overflows in debug builds silently vanish in production, precisely when they are needed most.

**Severity:** High

**Explanation:** Line 1317 states that 'the mechanism by which the evaluation semantic is chosen is implementation-defined,' meaning the same source code can enforce, observe, or ignore contracts depending on compiler flags the developer may not control. A security-critical precondition like `pre(index < size())` might be enforced during testing but ignored in the release binary shipped to customers. Unlike Rust's `panic!` or Java's assertions (which have a clear, portable enable/disable model), C++ contracts under P2900 offer no portable way to guarantee enforcement. This is the defining usability failure of the proposal: it makes the feature unreliable by default, requiring every team to audit their build system's contract flags—a task that will be forgotten, misconfigured, or overridden by CI templates. The result is a false sense of safety.

**Motivatio:**

- **Adversary:** Security-focused developers and the ISO/IEC JTC1/SC22/WG23 (Programming Language Vulnerabilities) liaison, plus major users like Google (which enforces production assertions via Abseil's CHECK macros)
- **Forum:** lewg
- **Damage:** revision_forcing

### Objection 10: A simple function with contracts requires the developer to simultaneously understand six distinct conceptual primitives—preconditions, postconditions, assertion statements, result name bindings, evaluation semantics, and implicit const-ness—a cognitive load that rivals full design patterns and will deter adoption by non-expert C++ programmers.

**Severity:** Medium

**Explanation:** The introductory example at line 324 demonstrates a function with `pre`, `post`, `contract_assert`, a result binding `r`, and implicit const parameters. To understand this example, a developer must know: (1) `pre` introduces a precondition checked on entry, (2) `post(r : ...)` introduces a postcondition with a named result binding using colon syntax, (3) `contract_assert` is a statement-level check distinct from `assert`, (4) parameters are implicitly const in predicates, and (5) the evaluation semantic is implementation-defined. By contrast, Rust's equivalent uses `assert!` and `debug_assert!` with no special syntax, and Swift uses `precondition()` as a simple function call. The conceptual surface area of P2900's contracts will push casual users toward continuing to use raw `assert` macros, undermining adoption.

**Motivatio:**

- **Adversary:** Educators and the C++ teaching community (e.g., Kate Gregory, Sy Brand) who advocate for simpler language features
- **Forum:** lewg
- **Damage:** capital_cost

### Objection 11: The keyword `contract_assert` is unnecessarily verbose and creates a confusing parallel with the existing `assert` macro from `<cassert>`, forcing every C++ developer to remember which assertion mechanism to use in which context.

**Severity:** Low

**Explanation:** The choice of `contract_assert` at line 324 avoids a name clash with the `assert` macro but introduces a 15-character keyword for what is conceptually a simple runtime check. Developers will inevitably ask 'when do I use assert vs. contract_assert?' and the answer—that one is a macro with `NDEBUG` semantics and the other is a language feature with implementation-defined evaluation—is subtle enough to be a persistent source of confusion. Codebases will end up with a mix of both, and style guides will diverge. A shorter keyword or a deprecation path for the macro would have been preferable.

**Motivatio:**

- **Adversary:** Style guide maintainers (Google C++ Style Guide, LLVM Coding Standards) who must now adjudicate between assert and contract_assert
- **Forum:** reflector
- **Damage:** capital_cost

### Objection 12: The proposal provides no convenience APIs or standard library contract combinators for common checks like range validation or non-null guarantees, forcing every user to write raw boolean expressions and missing an opportunity to make correct contracts easy to write.

**Severity:** Medium

**Explanation:** The postcondition syntax at line 324 (`post(r : r == x && r != 2)`) requires users to compose raw boolean expressions for every check. There are no standard combinators like `in_range(r, 0, 100)`, `not_null(ptr)`, or `sorted(begin, end)` that would make common contracts readable and less error-prone. Kotlin's standard library provides `require()` and `check()` with built-in message formatting; Rust's `assert_eq!` and `assert_ne!` provide semantic clarity. Without such facilities, C++ contracts will be verbose, inconsistent across codebases, and prone to subtle logical errors in complex boolean expressions. This is a missed opportunity to provide a pit-of-success for the most common contract patterns.

**Motivatio:**

- **Adversary:** Library Evolution Working Group (LEWG) members who expect standard library support for new language features, and the Boost.Contract community who already provide such combinators
- **Forum:** lewg
- **Damage:** capital_cost

### Objection 13: The implicit const-ness rule for parameters in contract predicates silently transforms valid C++ expressions into ill-formed code, creating a subtle trap where developers must remember that the same variable has different mutability inside and outside a contract annotation.

**Severity:** Medium

**Explanation:** At line 842, the example `pre((x = 0) == 0)` shows that assignment to a parameter—legal in normal C++ code—becomes an error inside a precondition because parameters are implicitly const. This rule has no visual marker in the syntax: the developer must simply know that `pre(...)` imposes const-ness. Accidental assignment in predicates (a common typo for `==`) would be caught, which is good, but intentional modifications to local copies—a legitimate pattern in normal code—are silently forbidden. This implicit context-dependent const-ness has no equivalent in Go, Swift, Rust, or even in C++ lambdas (where capture mode is explicit). It will generate confusing compiler errors for newcomers.

**Motivatio:**

- **Adversary:** Compiler diagnostic teams (Clang's -Weverything maintainers) who must produce clear error messages for this implicit rule, and C++ newcomers encountering context-dependent const
- **Forum:** lewg
- **Damage:** capital_cost

### Objection 14: Permitting compilers to elide all side effects in contract predicates means that logging, counters, and diagnostic output placed inside contracts are unreliable, destroying a primary use case for assertions in debugging and production monitoring.

**Severity:** Medium

**Explanation:** Line 1317 grants compilers permission to remove all side effects of predicate evaluation. A developer who writes `pre(log_check("entering critical section", x > 0))` cannot rely on the log message appearing, even when the contract is nominally being enforced. This contrasts sharply with Rust's `assert!` macro, which guarantees that all expressions within it are evaluated in debug builds. The elision rule transforms contracts from reliable diagnostic tools into optimistic hints, undermining the use case of contracts as production monitoring instrumentation. Teams that currently use assertion macros with logging side effects (a widespread practice in game engines, financial systems, and embedded firmware) will find contracts strictly less capable than their existing macros.

**Motivatio:**

- **Adversary:** Game development studios (EA, Ubisoft) and financial systems developers (Bloomberg, Jane Street) who rely on assertion-embedded diagnostics in production
- **Forum:** lewg
- **Damage:** section_weakening

### Objection 15: The result binding syntax `post(r : expr)` uses a colon separator that is inconsistent with C++'s existing binding and initialization syntax, adding yet another context-specific meaning to an already overloaded punctuation mark.

**Severity:** Low

**Explanation:** At line 324, the postcondition `post(r : r == x)` introduces a colon to separate the result name from the predicate. In C++, colons already serve as base-class list separators, ternary operator components, label markers, range-for separators, and structured binding introducers (with `auto`). Adding another meaning in contract syntax increases parsing ambiguity for humans (though not for compilers). Swift uses `->` for return type annotation and Rust uses `=>` in match arms; either would have been more visually distinct. This is a minor friction point but contributes to the overall cognitive load of the feature.

**Motivatio:**

- **Adversary:** C++ syntax consistency advocates on the reflector and tooling authors (clang-format, IDE developers) who must handle yet another colon context
- **Forum:** reflector
- **Damage:** capital_cost

### Objection 16: The paper's claim that adding contract specifiers preserves ABI backward-compatibility is misleading because it ignores potential vtable layout disruptions, ODR violations from differing contract declarations across translation units, and linker-level consequences that could silently break binary compatibility.

**Severity:** Medium

**Explanation:** Line 411 asserts ABI compatibility when adding contracts to existing functions, but this claim is not rigorously analyzed. If a virtual function in a base class gains contract specifiers in a future proposal extension (currently ill-formed but explicitly planned), vtable layouts could change. Even in the current proposal, differing contract declarations across translation units could create ODR violations that linkers may not detect. The paper provides no ABI stability analysis, no discussion of symbol mangling implications, and no guidance for platform ABI maintainers (Itanium C++ ABI, MSVC ABI). This creates a false sense of security that could lead to silent binary incompatibilities when contracts are incrementally adopted in large codebases.

**Motivatio:**

- **Adversary:** Platform ABI maintainers (Red Hat's Itanium ABI stewards, Microsoft's MSVC ABI team) and package managers (Conan, vcpkg) that depend on ABI stability guarantees
- **Forum:** lewg
- **Damage:** section_weakening

### Objection 17: The proposal explicitly defers invariants, procedural interfaces, and per-assertion semantic control to future extensions, shipping an MVP so minimal that it cannot express the most fundamental contract patterns in object-oriented and safety-critical C++ code.

**Severity:** High

**Explanation:** Section 2.3 at line 373 lists invariants, procedural interfaces, and direct semantic specification among intentionally excluded features. These are not obscure extensions—they are the core of Design by Contract as practiced in Eiffel, Ada 2012, and D. Without invariants, a `std::vector` cannot express 'size() <= capacity() at all times.' Without procedural interfaces, contract-checking libraries cannot integrate with the language feature. Without per-assertion semantics, safety-critical code cannot mandate enforcement of specific checks. The paper promises these as future extensions, but WG21 history shows that bolt-on extensions face severe design constraints from the initial feature (see concepts, modules, coroutines). By standardizing an incomplete foundation, P2900 risks locking C++ into a contracts design that cannot gracefully accommodate its own acknowledged gaps.

**Motivatio:**

- **Adversary:** National Body reviewers who will question whether the MVP is sufficient to justify the language complexity cost, particularly BSI (UK) and DIN (Germany) delegations with strong safety-critical constituencies
- **Forum:** nb_comment
- **Damage:** paper_killing

### Objection 18: The proposal provides no migration path from the ubiquitous `assert` macro in `<cassert>` to `contract_assert`, leaving millions of lines of existing assertion code stranded with no incremental adoption strategy.

**Severity:** Medium

**Explanation:** Line 622 explains that `contract_assert` was chosen to avoid clashing with the `assert` macro, but the paper stops there. There is no deprecation timeline for `assert`, no mechanical transformation tool specification, no guidance on coexistence, and no discussion of how codebases with thousands of `assert` calls should incrementally adopt contracts. In practice, teams will face a binary choice: rewrite all assertions at once (impractical for large codebases) or maintain two parallel assertion systems indefinitely (confusing and error-prone). A migration guide, a compatibility macro, or even a recommendation for phased adoption would have addressed this. The absence of any migration strategy will slow adoption and fragment the ecosystem between legacy-assert and contract-assert camps.

**Motivatio:**

- **Adversary:** Large codebase maintainers (Google, Meta, Microsoft) who have millions of assert() calls and need an incremental migration path
- **Forum:** lewg
- **Damage:** capital_cost

### Objection 19: Postconditions that reference function parameters are ill-formed when the function is a coroutine, creating a fundamental incompatibility between contracts and one of C++20's most important features.

**Severity:** Medium

**Explanation:** Line 894 states that if a postcondition's predicate odr-uses a function parameter and the function is a coroutine, the program is ill-formed—even if the parameter is declared const. This is because coroutine parameters may be moved or destroyed during suspension, making their values unavailable at the point where postconditions would be checked (after `co_return`). This restriction means that `task<int> compute(int x) post(r: r >= x)` is ill-formed, despite being the most natural way to express a coroutine's output guarantee. As coroutines become increasingly central to modern C++ (networking, async I/O, generators), this limitation will affect a growing fraction of new code. The paper acknowledges the problem but offers no workaround beyond 'don't use postconditions on coroutines with parameters.'

**Motivatio:**

- **Adversary:** Lewis Baker, Eric Niebler, and the coroutine-heavy library community (libunifex, stdexec) who need contracts on async interfaces
- **Forum:** lewg
- **Damage:** revision_forcing

### Objection 20: Contracts on virtual functions are explicitly ill-formed, excluding the primary mechanism of runtime polymorphism from contract checking and leaving the most error-prone dispatch boundaries in C++ entirely unprotected.

**Severity:** Medium

**Explanation:** Line 684 states that 'for a declaration of a virtual function to have precondition or postcondition specifiers is ill-formed,' deferring virtual function contracts to a future extension. Virtual functions are where Liskov Substitution Principle violations occur, where derived classes silently narrow preconditions or widen postconditions, and where runtime dispatch makes static analysis insufficient. These are precisely the functions that benefit most from contract checking. Eiffel's original Design by Contract was built around inheritance and virtual dispatch. By excluding virtual functions, P2900 cannot protect the most dangerous boundaries in polymorphic C++ code. The future extension will face the notoriously difficult problem of contract inheritance semantics (covariant preconditions, contravariant postconditions) constrained by whatever ABI and syntax decisions P2900 has already locked in.

**Motivatio:**

- **Adversary:** OOP-heavy framework developers (Qt, LLVM's class hierarchies) and Liskov Substitution Principle advocates in the committee
- **Forum:** lewg
- **Damage:** revision_forcing

### Objection 21: P2900 depends on the unfinished companion proposal P1494R5 for its observable checkpoint semantics, creating a normative dependency on a paper that has not yet been approved and could change or stall.

**Severity:** Low

**Explanation:** Line 1397 states that P2900 'builds on top of [P1494R5]' for observable checkpoints, which define when side effects become visible relative to contract evaluation. If P1494R5 is revised, delayed, or rejected, P2900's side-effect elision rules and evaluation ordering guarantees become unmoored. This is a process risk: the committee could approve P2900 and then find that P1494R5 requires changes that retroactively alter contract semantics. Coupled proposals have caused problems before (e.g., the interaction between coroutines and executors). The dependency should be either resolved before P2900 advances or the relevant definitions should be self-contained within P2900.

**Motivatio:**

- **Adversary:** CWG (Core Working Group) members who must ensure normative references are stable before forwarding to plenary
- **Forum:** hallway
- **Damage:** capital_cost

### Objection 22: Contract predicates are explicitly excluded from the immediate context for SFINAE purposes, meaning that a contract violation during template instantiation produces a hard error instead of a substitution failure, breaking established template metaprogramming patterns and preventing contracts from participating in overload resolution.

**Severity:** Medium

**Explanation:** Line 874 states that contract predicates are 'not considered part of the immediate context,' which means that if a contract predicate triggers a type error during template instantiation, it is not a SFINAE-friendly substitution failure but a hard compilation error. This breaks the common pattern of using `enable_if` or concepts to select overloads based on whether operations are valid. A library author who adds `pre(requires sortable<Range>)` cannot use that contract as a soft constraint—it will hard-error if instantiated with an unsortable range, even if another overload would have been viable. This forces a choice between contracts and SFINAE-based generic programming, fragmenting the language's two most powerful correctness mechanisms.

**Motivatio:**

- **Adversary:** Generic programming experts (Eric Niebler, Barry Revzin, ranges-v3/std::ranges contributors) who rely on SFINAE and concepts for overload resolution
- **Forum:** lewg
- **Damage:** section_weakening

---

## Notae Minores

- S-M1: Typographical error in evaluation semantic name formatting ('quick**-**enforce')