# Animadversiones - P2300R10

---

## Seal

***Cum obiectionibus*** - The cause proceeds with objections.

The paper survives as a viable standard proposal but carries significant usability and ecosystem objections that require attention before final adoption.

Confidence: 0.85

---

## Approbationes

### 1. Introduction and Priorities

*Approbatio.* Design goals are clearly stated and would be raised by committee members as legitimate concerns. The Defensor prevailed on Humanitas.

### 2. Hello World Example

*Approbatio.* The example demonstrates the core concepts clearly enough for committee review. The Defensor prevailed on Humanitas.

### 3. Asynchronous Inclusive Scan

*Approbatio.* The example demonstrates bulk operations clearly enough for committee review. The Defensor prevailed on Humanitas.

---

## Objections

### Objection 1: Compiler optimization claims lack empirical benchmark evidence.

**Severity:** Medium

**Quoted text:**
> the compiler is able to see a chain of work described using senders as a tree of tail calls, allowing for inlining and removal of most of the sender machinery

(Line 2238)

**Motivatio:**

- **Adversary:** Performance-conscious implementer
- **Forum:** lewg
- **Damage:** revision_forcing

### Objection 2: Bulk fusion optimization claims lack empirical evidence.

**Severity:** Medium

**Quoted text:**
> an implementation could recognize two subsequent § 4.20.9 execution::bulks of compatible shapes, and merge them together into a single submission of a GPU kernel

(Line 2238)

**Motivatio:**

- **Adversary:** GPU computing specialist
- **Forum:** lewg
- **Damage:** revision_forcing

### Objection 3: Eager sender overhead claims lack quantification.

**Severity:** Medium

**Quoted text:**
> This obviously has overheads both at runtime and in algorithm complexity

(Line 1538)

**Motivatio:**

- **Adversary:** Performance-conscious implementer
- **Forum:** lewg
- **Damage:** revision_forcing

### Objection 4: Cancellation overhead claims lack measurement.

**Severity:** Low

**Quoted text:**
> incur the runtime overhead of supporting cancellation, even if cancellation will never be requested by the caller

(Line 1538)

**Motivatio:**

- **Adversary:** Performance-conscious implementer
- **Forum:** lewg
- **Damage:** revision_forcing

### Objection 5: HPX allocation claims lack benchmark evidence.

**Severity:** Low

**Quoted text:**
> With the sender/receiver when_all(...) | then(...) we get that 'for free'

(Line 890)

**Motivatio:**

- **Adversary:** HPX team member
- **Forum:** reflector
- **Damage:** revision_forcing

### Objection 6: High-performance computing characterization lacks performance data.

**Severity:** Low

**Quoted text:**
> it has since become one of NVIDIA's core C++ libraries for high-performance computing

(Line 890)

**Motivatio:**

- **Adversary:** Performance-conscious implementer
- **Forum:** lewg
- **Damage:** revision_forcing

### Objection 7: Dynamic memory requirements for bare metal lack evidence.

**Severity:** Low

**Quoted text:**
> a domain for which senders are particularly well-suited due to their very low dynamic memory requirements

(Line 890)

**Motivatio:**

- **Adversary:** Embedded systems specialist
- **Forum:** lewg
- **Damage:** revision_forcing

### Objection 8: Efficient dispatch claims to OS I/O lack performance data.

**Severity:** Low

**Quoted text:**
> It has schedulers that dispatch efficiently to epoll and io_uring on Linux and the Windows Thread Pool on Windows

(Line 890)

**Motivatio:**

- **Adversary:** Systems programmer
- **Forum:** lewg
- **Damage:** revision_forcing

### Objection 9: Design goal of correctness by construction conflicts with cancellation complexity.

**Severity:** Medium

**Quoted text:**
> Make it easy to be correct by construction

(Line 31)

**Motivatio:**

- **Adversary:** Design committee member
- **Forum:** lewg
- **Damage:** section_weakening

### Objection 10: Sender algorithm customization conflicts with composability goal.

**Severity:** High

**Quoted text:**
> Be composable and generic, allowing users to write code that can be used with many different types of execution resources

(Line 31)

**Motivatio:**

- **Adversary:** Generic programming specialist
- **Forum:** lewg
- **Damage:** paper_killing

### Objection 11: Hello-world requires understanding 5+ novel concepts before writing useful code.

**Severity:** High

**Quoted text:**
> scheduler auto sch = thread_pool.scheduler(); sender auto begin = schedule(sch);

(Line 48)

**Motivatio:**

- **Adversary:** Usability advocate
- **Forum:** lewg
- **Damage:** paper_killing

### Objection 12: sync_wait return type requires double unwrapping for common single-value case.

**Severity:** Medium

**Quoted text:**
> auto [i] = this_thread::sync_wait(add_42).value();

(Line 62)

**Motivatio:**

- **Adversary:** Usability advocate
- **Forum:** lewg
- **Damage:** revision_forcing

### Objection 13: Confusingly similar names for scheduler and value transformation operations.

**Severity:** Medium

**Quoted text:**
> The execution::starts_on algorithm will ensure that the given sender will start in the specified context

(Line 804)

**Motivatio:**

- **Adversary:** Usability advocate
- **Forum:** lewg
- **Damage:** revision_forcing

### Objection 14: Writing custom sender/algorithm requires prohibitive boilerplate.

**Severity:** High

**Quoted text:**
> template <class R, class F> class _then_receiver : public R {

(Line 393)

**Motivatio:**

- **Adversary:** Library implementer
- **Forum:** lewg
- **Damage:** paper_killing

### Objection 15: Windows socket recv example demonstrates extreme complexity for basic I/O.

**Severity:** Medium

**Quoted text:**
> using stop_callback_t = stop_callback_of_t<stop_token_of_t<env_of_t<Receiver>>, cancel_cb>;

(Line 296)

**Motivatio:**

- **Adversary:** Platform integration specialist
- **Forum:** lewg
- **Damage:** section_weakening

### Objection 16: Easy to forget .value() on sync_wait, getting silent empty optional.

**Severity:** Medium

**Quoted text:**
> which will either return a std::optional<std::tuple<...>> with the value sent by the last sender

(Line 67)

**Motivatio:**

- **Adversary:** Usability advocate
- **Forum:** lewg
- **Damage:** revision_forcing

### Objection 17: No convenience API for fire-and-forget with error reporting.

**Severity:** Low

**Quoted text:**
> execution::start_detached(std::move(snd));

(Line 790)

**Motivatio:**

- **Adversary:** Usability advocate
- **Forum:** lewg
- **Damage:** revision_forcing

### Objection 18: Echo server example demonstrates poor readability of real-world sender composition.

**Severity:** Medium

**Quoted text:**
> outstanding.start(EX::repeat_effect_until(EX::let_value(NN::async_read_some(ptr->d_socket,

(Line 340)

**Motivatio:**

- **Adversary:** Usability advocate
- **Forum:** lewg
- **Damage:** section_weakening

### Objection 19: No standard thread pool or execution resource provided.

**Severity:** High

**Quoted text:**
> scheduler auto sch = thread_pool.scheduler();

(Line 48)

**Motivatio:**

- **Adversary:** Ecosystem advocate
- **Forum:** lewg
- **Damage:** paper_killing

### Objection 20: No type-erased sender or scheduler wrapper provided.

**Severity:** High

**Quoted text:**
> Many of these costs are inherent in the nature of 'future' as a handle to work that is already scheduled for execution

(Line 862)

**Motivatio:**

- **Adversary:** Library implementer
- **Forum:** lewg
- **Damage:** section_weakening

### Objection 21: No migration path from std::future, std::async, or std::thread.

**Severity:** High

**Quoted text:**
> A future is a handle to work that has already been scheduled for execution

(Line 862)

**Motivatio:**

- **Adversary:** Ecosystem advocate
- **Forum:** lewg
- **Damage:** paper_killing

---

## Notae Minores

- S-M7: Inconsistent naming: dynamic_buffer vs dynamic_array in explanatory text
- U-C6: Inconsistent naming: get_scheduler() vs scheduler() member function

---

## Acta

### Candidate charges and outcomes

**Survived (21):**
- P-C1: Performance claims without empirical evidence constitute a substantive objection that a committee member would raise.
- P-C2: Kernel fusion optimization claims require benchmark evidence to substantiate performance benefits.
- P-C3: Runtime overhead assertions without quantification weaken the design justification for lazy-only senders.
- P-C4: Cancellation overhead claims need measurement to validate the eager vs. lazy design tradeoff.
- P-C5: HPX allocation claims require empirical evidence to substantiate the 'for free' assertion.
- P-C6: High-performance computing characterization without benchmarks undermines credibility claims.
- P-C7: Dynamic memory requirements for bare metal need measurement to validate embedded suitability.
- P-C8: Efficient dispatch claims to OS I/O mechanisms require latency and throughput measurements.
- D-C1: Design goal tension between correctness and cancellation complexity is a legitimate committee concern.
- D-C6: The tension between composability and scheduler customization priority is a substantive design concern.
- U-C1: Concept count for basic usage is a legitimate usability concern that committee members would raise.
- U-C2: Return type verbosity for common single-value case is a substantive usability objection.
- U-C3: Naming confusion between similar algorithms is a legitimate usability concern.
- U-C4: Boilerplate for custom senders is a significant implementability concern.
- U-C5: Platform I/O integration complexity is a legitimate usability concern for real-world adoption.
- U-C8: Silent misbehavior from forgetting .value() is a legitimate pit-of-despair usability concern.
- U-C9: Missing fire-and-forget with error reporting is a legitimate usability gap.
- U-C10: Readability of real-world sender composition is a legitimate usability concern.
- E-TP1: Lack of standard thread pool is a significant ecosystem gap that committee members would raise.
- E-TE1: Missing type-erased sender/scheduler wrapper is a significant facility gap for library authors.
- E-MG1: Missing migration path from std::future is a significant ecosystem concern.

**Killed (19):**
- D-C2: [Confessio] The paper explicitly acknowledges deferrals of time-based scheduling and networking to future papers.
- D-C3: [Confessio] The paper already qualifies the completion scheduler requirement, acknowledging not all senders advertise one.
- D-C4: [Testimonium] A simple code verification would confirm this is a typo in the example, not a design flaw.
- D-C5: [Confessio] The paper body explicitly qualifies the awaitable claim with the 'generally awaitable' limitation.
- S-R1: [Testimonium] A simple compilation check would verify this constructor naming error immediately.
- S-R2: [Testimonium] A ten-second API reference check would confirm the Windows type name spelling error.
- S-R3: [Testimonium] A simple code review would verify the missing base() member function.
- S-R4: [Testimonium] A simple compilation check would reveal the unqualified get_env recursion issue.
- S-R5: [Testimonium] Same as S-R4, a simple compilation check would reveal the issue.
- S-R6: [Testimonium] Same as S-R4, a simple compilation check would reveal the issue.
- S-M8: [Testimonium] A simple specification review would verify the missing completion_signatures declaration.
- S-M9: [Testimonium] A simple namespace qualification check would verify this unqualified type usage.
- U-C7: [Confessio] The paper explicitly acknowledges that typed senders are 'moderately more challenging to write'.
- E-T1: [Confessio] The paper explicitly acknowledges time-based scheduling is deferred to future papers.
- E-IO1: [Confessio] The paper explicitly acknowledges file and network I/O are deferred to future papers.
- E-PA1: [Confessio] The paper explicitly acknowledges parallel algorithm integration is deferred to future proposals.
- E-CO1: [Confessio] The paper explicitly acknowledges coroutine interop is limited to single-value senders.
- E-NW1: [Confessio] The paper acknowledges networking examples depend on companion proposal P2762.
- E-BK1: [Confessio] The paper explicitly acknowledges multi-dimensional grid support is deferred to future papers.

**Relegated (2):**
- S-M7: [Dignitas] This is a documentation inconsistency, a housekeeping issue beneath the dignity of formal charges.
- U-C6: [Dignitas] This is a naming inconsistency, a housekeeping issue beneath the dignity of formal charges.
