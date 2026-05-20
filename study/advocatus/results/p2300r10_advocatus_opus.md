# Animadversiones - P2300R10

---

## Seal

***Cum obiectionibus*** - The cause proceeds with objections.

P2300R10 is a well-structured and transparently scoped proposal whose core sender/receiver architecture withstands scrutiny, but it carries four surviving objections—concerning unsubstantiated compiler optimization claims, the absence of type erasure, the complexity of custom I/O cancellation plumbing, and the novel uncatchable-exception semantic—that warrant revision or committee discussion before final adoption.

Confidence: 0.82

---

## Approbationes

### 1. Motivation and Design Priorities

*Approbatio.* The motivation section's characterization of C++11 async primitives and the stated design priorities reflect well-established community consensus and survived all charges. The Defensor prevailed on Humanitas.

### 2. Lazy vs. Eager Senders Design Rationale

*Approbatio.* The paper's thorough analysis of lazy vs. eager tradeoffs, including honest concession of eager sender overhead categories, demonstrates strong design reasoning. The Defensor prevailed on Confessio.

### 3. Scheduler and run_loop Specification

*Approbatio.* The zero-allocation intrusive linked list design for run_loop is architecturally sound and verifiable from the specification itself. The Defensor prevailed on Testimonium.

### 4. Cancellation Architecture (stop tokens)

*Approbatio.* The cancellation design openly acknowledges its tradeoffs including SG1's overhead concerns, demonstrating intellectual honesty about unresolved issues. The Defensor prevailed on Confessio.

### 5. Sender/Receiver Core Concepts and Algorithms

*Approbatio.* The core sender/receiver/operation_state concept hierarchy and the algorithm taxonomy are well-structured and no surviving charge targets their fundamental design. The Defensor prevailed on Humanitas.

### 6. Deferred Items and Future Work

*Approbatio.* The paper's explicit enumeration of 22 deferred items demonstrates responsible scoping and transparency about what is and is not included. The Defensor prevailed on Confessio.

---

## Objections

### Objection 1: The claim that compilers inline and remove 'most of the sender machinery' for lazy sender chains is presented without any empirical evidence such as generated code analysis or benchmarks.

**Severity:** Low

**Quoted text:**
> the compiler is able to see a chain of work described using senders as a tree of tail calls, allowing for inlining and removal of most of the sender machinery.

(Line 2238)

**Motivatio:**

- **Adversary:** SG1 performance-focused members (e.g., those concerned with embedded/real-time overhead)
- **Forum:** sg1
- **Damage:** revision_forcing

### Objection 2: The omission of type erasure facilities means the first stated design goal of composability and genericity across execution resources is only partially delivered, as users cannot write type-erased interfaces without building their own machinery.

**Severity:** Medium

**Quoted text:**
> Specific type erasure facilities are omitted, as per LEWG direction. Type erasure facilities can be built on top of this proposal, as discussed in § 5.9 Customization points.

(Line 846)

**Motivatio:**

- **Adversary:** LEWG members advocating for usable-out-of-the-box library facilities
- **Forum:** lewg
- **Damage:** section_weakening

### Objection 3: The only concrete I/O example (Windows socket recv) requires users to manually manage stop tokens and atomic synchronization, contradicting the stated design intent that cancellation plumbing is managed by algorithms.

**Severity:** Medium

**Quoted text:**
> To get a better feel for how this interface might be used by low-level operations see this example implementation of a cancellable `async_recv()` operation for a Windows Socket.

(Line 191)

**Motivatio:**

- **Adversary:** LEWG usability reviewers concerned with the pit-of-success principle
- **Forum:** lewg
- **Damage:** revision_forcing

### Objection 4: Modeling stopped signals as uncatchable exceptions that bypass catch(...) is a novel semantic with no C++ precedent, creating a significant teachability and correctness hazard.

**Severity:** Medium

**Quoted text:**
> When your task type's promise inherits from `with_awaitable_senders`, what happens is this: the coroutine behaves as if an *uncatchable exception* had been thrown from the `co_await` expression.

(Line 1769)

**Motivatio:**

- **Adversary:** SG1/LEWG members concerned with teachability and principle of least surprise
- **Forum:** lewg
- **Damage:** section_weakening

---

## Notae Minores

- S-W1: Typo 'assigment' should be 'assignment' at line 3664 in normative wording.

---

## Acta

### Candidate charges and outcomes

**Survived (4):**
- P-C2: The claim that compilers can inline and remove 'most of the sender machinery' is a specific technical claim used to justify the lazy design choice, and a committee member could reasonably ask for evidence.
- D-C2: The paper acknowledges the omission but does not concede it undermines composability; a committee member could argue the first stated design goal is only partially met without type erasure.
- U-C1: The paper claims cancellation plumbing is managed by algorithms for common usage, yet the only concrete I/O example requires manual stop-token management with no convenience layer, which a real committee member would flag.
- U-C2: The uncatchable exception semantic for stopped signals is a novel C++ concept that bypasses catch(...), and a committee member could reasonably object that this violates developer expectations and creates a teachability problem.

**Killed (18):**
- P-C1: [Humanitas] The inefficiency of std::async/std::future/std::promise is universally acknowledged in WG21; no committee member would demand benchmarks for this well-established consensus position in a motivation section.
- P-C3: [Confessio] The paper itself presents this as a design analysis comparing eager vs. lazy approaches, not as a quantified performance claim; it is an architectural argument about overhead categories, which the paper openly discusses.
- P-C4: [Testimonium] The zero-allocation claim follows directly from the intrusive linked list design specified in the paper; a ten-second reading of the operation state layout confirms no heap allocation is needed.
- P-C5: [Humanitas] Field deployment evidence cited as anecdotal motivation would not be challenged by a committee member for lacking precise metrics; it is offered as supporting context, not a quantitative claim.
- D-C1: [Confessio] The paper explicitly acknowledges this tradeoff at line 1097, conceding that type-checking is deferred to connect time for dependently-typed senders, making this a known and accepted design consequence.
- D-C3: [Confessio] The paper explicitly concedes at line 1432 that SG1 raised concerns about runtime overhead in single-threaded scenarios and that these are still being investigated.
- D-C4: [Confessio] The paper explicitly acknowledges at line 1748 that only some senders can be made awaitable due to the expressiveness mismatch, directly conceding the limitation.
- U-S1: [Articulus] The paper does not claim to provide type erasure; it explicitly states type erasure is omitted per LEWG direction and deferred, so the charge attacks a feature the paper never claims to offer.
- U-S2: [Confessio] The paper explicitly concedes the removal of start_detached/execute and states they are to be replaced by the async_scope proposal, directly acknowledging the gap.
- U-M1: [Confessio] The paper itself states schedule_from is 'not meant to be used in user code' and explains it exists for the implementation of continues_on, directly conceding the concern.
- U-M2: [Confessio] The paper explicitly explains the rationale for placing sync_wait in std::this_thread, directly addressing the discoverability concern with a technical justification.
- U-S3: [Articulus] The paper does not claim to replace high-level parallel algorithm interfaces; the inclusive scan example demonstrates low-level composability, and the paper separately discusses future integration with parallel execution policies.
- E-D1: [Confessio] The paper explicitly states at line 1276 that time_scheduler and other extended concepts are deferred to future papers, openly conceding the scope limitation.
- E-D2: [Confessio] The paper explicitly acknowledges async sequences as future work at line 1658, conceding this is not in scope.
- E-D3: [Confessio] The paper explicitly states at line 1789 that scheduler-policy integration will be proposed separately, conceding the deferral.
- E-L1: [Confessio] This is a duplicate of D-C4; the paper explicitly concedes the expressiveness mismatch between senders and coroutines at line 1748.
- E-L2: [Confessio] This is a duplicate of D-C1; the paper explicitly concedes deferred type-checking at line 1097.
- E-L3: [Confessio] This is a duplicate of D-C3; the paper explicitly concedes the unresolved SG1 concern at line 1432.

**Relegated (1):**
- S-W1: [Dignitas] A single-character typo ('assigment' → 'assignment') is a housekeeping matter beneath the dignity of a formal charge.
