# Animadversiones - P2300R10

---

## Seal

***Cum obiectionibus*** - The cause proceeds with objections.

While the paper presents a comprehensive framework for asynchronous execution, several significant design and usability concerns, along with specification errors, require addressing before adoption.

Confidence: 0.95

---

## Objections

### Objection 1: The paper claims compilers can eliminate sender abstraction overhead but provides no benchmarks to substantiate this zero-overhead assertion.

**Severity:** High

**Quoted text:**
> the compiler is able to see a chain of work described using senders as a tree of tail calls...compilers are capable of removing the senders abstraction entirely

(Line 2238)

**Motivatio:**

- **Adversary:** Performance-focused implementer
- **Forum:** LEWG
- **Damage:** paper_killing

### Objection 2: The paper claims bulk operation fusion optimizations but provides no implementation evidence or performance measurements.

**Severity:** High

**Quoted text:**
> an implementation could recognize two subsequent...merge them into a single submission of a GPU kernel

(Line 2238)

**Motivatio:**

- **Adversary:** GPU/HPC developer
- **Forum:** SG1
- **Damage:** paper_killing

### Objection 3: The deferral of time-based scheduling, file I/O, and networking contradicts the stated goal of caring about all reasonable use cases.

**Severity:** High

**Quoted text:**
> Care about all reasonable use cases, domains and platforms

(Line 31)

**Motivatio:**

- **Adversary:** Networking/file system developer
- **Forum:** LEWG
- **Damage:** paper_killing

### Objection 4: Deferring async file and network I/O to future papers makes the proposal incomplete for its primary motivating use cases.

**Severity:** High

**Quoted text:**
> Concepts that extend scheduler to support opening, reading and writing files asynchronously

(Line 1276)

**Motivatio:**

- **Adversary:** Systems programmer
- **Forum:** LEWG
- **Damage:** paper_killing

### Objection 5: The lack of a standard thread pool makes the hello-world example non-functional with the proposed standard alone.

**Severity:** High

**Quoted text:**
> scheduler auto sch = thread_pool.scheduler()

(Line 48)

**Motivatio:**

- **Adversary:** New adopter
- **Forum:** LEWG
- **Damage:** paper_killing

### Objection 6: The hello-world example requires understanding 5+ novel concepts before writing useful code, creating a significant barrier to entry.

**Severity:** Medium

**Quoted text:**
> scheduler auto sch = thread_pool.scheduler(); sender auto begin = schedule(sch);...

(Line 48)

**Motivatio:**

- **Adversary:** Novice C++ programmer
- **Forum:** reflector
- **Damage:** section_weakening

### Objection 7: Confusingly similar algorithm names like starts_on/continues_on and then/let_value create unnecessary cognitive load.

**Severity:** Medium

**Quoted text:**
> execution::starts_on...execution::continues_on

(Line 804)

**Motivatio:**

- **Adversary:** Library designer
- **Forum:** hallway
- **Damage:** section_weakening

### Objection 8: Implementing even simple algorithms like 'then' requires ~50 lines of boilerplate compared to simpler models in other languages.

**Severity:** Medium

**Quoted text:**
> template <class R, class F> class _then_receiver...

(Line 393)

**Motivatio:**

- **Adversary:** Library implementer
- **Forum:** SG1
- **Damage:** section_weakening

### Objection 9: The echo server example demonstrates poor readability of real-world sender composition compared to simpler models in other languages.

**Severity:** Medium

**Quoted text:**
> outstanding.start(EX::repeat_effect_until(...

(Line 340)

**Motivatio:**

- **Adversary:** Network programmer
- **Forum:** reflector
- **Damage:** section_weakening

### Objection 10: Limiting bulk operations to 1D shapes reduces utility for GPU/accelerator workloads despite stated goals.

**Severity:** Low

**Quoted text:**
> only integral types are used to specify the shape of the bulk section

(Line 1911)

**Motivatio:**

- **Adversary:** GPU programmer
- **Forum:** SG1
- **Damage:** capital_cost

---

## Notae Minores

- S-R1: Constructor name mismatch in _retry_op struct
- S-R2: Typo in WSAOVERALAPPED struct declaration
- S-R3: Missing base() method in _then_receiver
- S-R4: Unqualified get_env call in _then_sender
- S-R5: Unqualified get_env call in _retry_receiver
- S-R6: Unqualified get_env call in _retry_sender
- S-M7: Inconsistent naming between dynamic_buffer code and dynamic_array text
- S-M8: Missing completion_signatures for recv_sender
- S-M9: Unqualified operation_state_concept in inline scheduler's _op

---

## Acta

### Candidate charges and outcomes

**Survived (31):**
- P-C1: The paper makes strong performance claims about compiler optimizations without providing benchmarks, which a committee member would reasonably question.
- P-C2: The claim about bulk fusion optimizations lacks empirical evidence, and the paper does not concede this limitation.
- P-C3: The assertion of runtime overhead for eager senders is stated as obvious without quantification, warranting further scrutiny.
- P-C4: The cancellation overhead claim for eager senders lacks measurement, though the reasoning is plausible.
- P-C5: The 'for free' optimization claim for HPX's dataflow equivalent lacks supporting benchmarks.
- P-C6: Characterizing stdexec as a high-performance computing library without data undermines credibility.
- P-C7: The claim about low memory requirements for bare metal lacks substantiation.
- P-C8: Efficient dispatch to OS I/O mechanisms is asserted without performance data.
- D-C1: The 'correct by construction' goal conflicts with the complexity of cancellation implementation described in the paper.
- D-C2: The deferral of time-based scheduling and I/O/networking contradicts the 'care about all use cases' goal.
- D-C3: The lack of mandatory completion scheduler advertising undermines the 'clear execution answers' goal.
- D-C6: The tension between composability and scheduler-specific customization is a legitimate design concern.
- U-C1: The hello-world example's conceptual overhead is a valid usability concern.
- U-C2: The verbose result extraction via optional<tuple<>> is a legitimate usability issue.
- U-C3: The confusingly similar algorithm names create a genuine usability barrier.
- U-C4: The boilerplate required for custom senders is a significant usability hurdle.
- U-C5: The complexity of the Windows socket recv example highlights usability challenges.
- U-C6: Inconsistent naming between scheduler() and get_scheduler() creates confusion.
- U-C7: Dependently-typed senders introduce conceptual overhead that impacts usability.
- U-C8: The silent failure mode of forgetting .value() is a pit-of-despair pattern.
- U-C9: The lack of error-reporting fire-and-forget APIs creates practical usability issues.
- U-C10: The echo server example demonstrates poor readability of sender composition in practice.
- E-T1: Deferring time-based scheduling undermines the framework's practical utility for real-world applications.
- E-IO1: Deferring async file/network I/O limits the framework's immediate usefulness despite motivating examples.
- E-TP1: Lack of standard thread pool makes the hello-world example non-functional with the proposed standard alone.
- E-TE1: Absence of type-erased senders/schedulers creates practical limitations for library authors.
- E-PA1: Deferred integration with parallel algorithms leaves a gap in composability between parallel and async work.
- E-CO1: Limited coroutine interop creates friction for existing coroutine users despite stated goals.
- E-MG1: No migration path from existing async facilities creates adoption barriers for legacy codebases.
- E-NW1: Dependence on unfinished companion proposals undermines the flagship networking use case.
- E-BK1: Limiting bulk operations to 1D shapes reduces utility for GPU/accelerator workloads.

**Killed (9):**
- D-C5: [Confessio] The paper explicitly qualifies the 'all awaitables are senders' claim, conceding the limitation in the body text.
- S-R1: [Testimonium] A ten-second code review confirms the constructor name mismatch is an error.
- S-R2: [Testimonium] The typo in WSAOVERALAPPED is a factual error detectable via quick verification.
- S-R3: [Testimonium] The missing base() method in _then_receiver is a code error evident upon inspection.
- S-R4: [Testimonium] The unqualified get_env call would cause recursion, a factual error detectable via quick check.
- S-R5: [Testimonium] Same as S-R4; unqualified get_env causes recursion errors.
- S-R6: [Testimonium] Same issue as S-R4; unqualified get_env leads to recursion.
- S-M8: [Testimonium] Missing completion_signatures in recv_sender is a specification error detectable via quick check.
- S-M9: [Testimonium] The unqualified operation_state_concept is a factual specification error.

**Relegated (2):**
- D-C4: [Dignitas] The example bug is a typo, which falls under housekeeping rather than a substantive design flaw.
- S-M7: [Dignitas] Inconsistent naming between code and text is a minor editorial issue.
