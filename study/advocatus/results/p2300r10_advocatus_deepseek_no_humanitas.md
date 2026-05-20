# Animadversiones - P2300R10

---

## Seal

***Cum obiectionibus*** - The cause proceeds with objections.

The paper presents a comprehensive framework but lacks necessary empirical evidence and has significant design and usability issues that need addressing.

Confidence: 0.8

---

## Approbationes

### 1. 3. Motivation

*Approbatio.* Strong motivation for async framework. The Defensor prevailed on Confessio.

### 2. 4. Examples

*Approbatio.* Clear examples provided. The Defensor prevailed on Articulus.

### 3. 5. Design Goals

*Approbatio.* Well-articulated goals. The Defensor prevailed on Articulus.

---

## Objections

### Objection 1: Cancellation design complexity undermines ease of correct construction.

**Severity:** Medium

**Quoted text:**
> Make it easy to be correct by construction.

(Line 31)

**Motivatio:**

- **Adversary:** Library implementers
- **Forum:** LEWG
- **Damage:** section_weakening

### Objection 2: Omission of key use cases despite claims.

**Severity:** High

**Quoted text:**
> Care about all reasonable use cases, domains and platforms.

(Line 31)

**Motivatio:**

- **Adversary:** End users
- **Forum:** SG1
- **Damage:** paper_killing

### Objection 3: Senders without completions lack clarity.

**Severity:** Medium

**Quoted text:**
> Have clear and concise answers for where things execute.

(Line 31)

**Motivatio:**

- **Adversary:** Developers
- **Forum:** LEWG
- **Damage:** section_weakening

### Objection 4: Overstates awaitable compatibility.

**Severity:** Medium

**Quoted text:**
> All awaitables are senders.

(Line 17)

**Motivatio:**

- **Adversary:** Coroutines users
- **Forum:** SG1
- **Damage:** section_weakening

### Objection 5: Customization vs. composability tension.

**Severity:** High

**Quoted text:**
> Be composable and generic.

(Line 31)

**Motivatio:**

- **Adversary:** Library authors
- **Forum:** LEWG
- **Damage:** paper_killing

### Objection 6: High concept count for hello-world.

**Severity:** High

**Quoted text:**
> schedulerautosch=thread_pool.scheduler();

(Line 48)

**Motivatio:**

- **Adversary:** New users
- **Forum:** LEWG
- **Damage:** paper_killing

### Objection 7: Verbose return type handling.

**Severity:** Medium

**Quoted text:**
> auto[i]=this_thread::sync_wait(add_42).value();

(Line 67)

**Motivatio:**

- **Adversary:** Developers
- **Forum:** SG1
- **Damage:** section_weakening

### Objection 8: Confusing function names.

**Severity:** Medium

**Quoted text:**
> The `execution::starts_on` algorithm will ensure that the given sender will start in the specified context.

(Line 804)

**Motivatio:**

- **Adversary:** Users
- **Forum:** LEWG
- **Damage:** section_weakening

### Objection 9: Excessive boilerplate for customization.

**Severity:** High

**Quoted text:**
> template<classR,classF>class_then_receiver:publicR{Ff_;

(Line 393)

**Motivatio:**

- **Adversary:** Users
- **Forum:** SG1
- **Damage:** paper_killing

### Objection 10: Complexity in Windows example.

**Severity:** Medium

**Quoted text:**
> usingstop_callback_t=stop_callback_of_t<stop_token_of_t<env_of_t<Receiver>>

(Line 296)

**Motivatio:**

- **Adversary:** Windows developers
- **Forum:** LEWG
- **Damage:** section_weakening

### Objection 11: Dependent types add overhead.

**Severity:** Medium

**Quoted text:**
> Typed senders become moderately more challenging to write.

(Line 1175)

**Motivatio:**

- **Adversary:** Users
- **Forum:** SG1
- **Damage:** section_weakening

### Objection 12: Silent misbehavior possible.

**Severity:** Low

**Quoted text:**
> which will either return a `std::optional<std::tuple<...>>`

(Line 67)

**Motivatio:**

- **Adversary:** Developers
- **Forum:** LEWG
- **Damage:** capital_cost

### Objection 13: No convenient error reporting.

**Severity:** Low

**Quoted text:**
> execution::start_detached(std::move(snd));

(Line 790)

**Motivatio:**

- **Adversary:** Users
- **Forum:** SG1
- **Damage:** capital_cost

### Objection 14: Poor readability in example.

**Severity:** Low

**Quoted text:**
> outstanding.start(...)

(Line 340)

**Motivatio:**

- **Adversary:** Readers
- **Forum:** LEWG
- **Damage:** capital_cost

---

## Notae Minores

- Typo in WSAOVERLAPPED
- Inconsistent naming in examples
- Missing member function in then_receiver
- Ambiguous get_env calls
- Inconsistent naming in struct vs. explanation
- Missing completion signatures
- Missing qualification in operation_state_concept

---

## Acta

### Candidate charges and outcomes

**Survived (14):**
- D-C1: Cancellation design complexity undermines ease of correct construction.
- D-C2: Omission of key use cases despite claims.
- D-C3: Senders without completions lack clarity.
- D-C5: Overstates awaitable compatibility.
- D-C6: Customization vs. composability tension.
- U-C1: High concept count for hello-world.
- U-C2: Verbose return type handling.
- U-C3: Confusing function names.
- U-C4: Excessive boilerplate for customization.
- U-C5: Complexity in Windows example.
- U-C7: Dependent types add overhead.
- U-C8: Silent misbehavior possible.
- U-C9: No convenient error reporting.
- U-C10: Poor readability in example.

**Killed (28):**
- P-C1: [Testimonium] Lack of benchmarks can be factually verified.
- P-C2: [Testimonium] Lack of benchmarks can be factually verified.
- P-C3: [Testimonium] Lack of measurements can be factually verified.
- P-C4: [Testimonium] Lack of measurements can be factually verified.
- P-C5: [Testimonium] Lack of benchmarks can be factually verified.
- P-C6: [Testimonium] Lack of benchmarks can be factually verified.
- P-C7: [Testimonium] Lack of evidence can be factually verified.
- P-C8: [Testimonium] Lack of measurements can be factually verified.
- D-C4: [Testimonium] Code example contains a bug.
- S-R1: [Testimonium] Constructor name error is factual.
- S-R2: [Testimonium] Typo is a factual error.
- S-R3: [Testimonium] Missing member function is factual.
- S-R4: [Testimonium] Unqualified call is a factual error.
- S-R5: [Testimonium] Unqualified call is a factual error.
- S-R6: [Testimonium] Unqualified call is a factual error.
- S-M7: [Testimonium] Inconsistent naming is factual.
- S-M8: [Testimonium] Missing signatures is factual.
- S-M9: [Testimonium] Missing qualification is factual.
- U-C6: [Testimonium] Inconsistent naming is factual.
- E-T1: [Testimonium] Feature deferral is factual.
- E-IO1: [Testimonium] Feature deferral is factual.
- E-TP1: [Testimonium] Omission is factual.
- E-TE1: [Testimonium] Omission is factual.
- E-PA1: [Testimonium] Feature deferral is factual.
- E-CO1: [Testimonium] Limitation is factual.
- E-MG1: [Testimonium] Omission is factual.
- E-NW1: [Testimonium] Dependency is factual.
- E-BK1: [Testimonium] Limitation is factual.
