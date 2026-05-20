# Animadversiones - P2300R10

---

## Seal

***Cum obiectionibus*** - The cause proceeds with objections.

P2300R10 is a mature and well-structured proposal whose core design withstands scrutiny, with only minor specification clarity issues around stop-callback exception semantics, sync_wait variant handling, and the final-class receiver restriction requiring attention before final adoption.

Confidence: 0.91

---

## Approbationes

### 1. Introduction and Motivation

*Approbatio.* All motivational claims are well-scoped community consensus statements that do not overreach, and no charge against them survived scrutiny. The Defensor prevailed on Articulus.

### 2. Examples: End User (Hello World, Async Inclusive Scan)

*Approbatio.* The examples correctly use standard C++20 syntax and clearly demonstrate the intended user experience; charges against them were dissolved by basic language knowledge. The Defensor prevailed on Testimonium.

### 3. Exposition-only Concepts and Types

*Approbatio.* All five charges alleging invalid hyphenated identifiers were dissolved by the well-established WG21 drafting convention for exposition-only names. The Defensor prevailed on Testimonium.

### 4. Scheduler/Sender/Receiver Core Design

*Approbatio.* The paper's explicit scoping decisions (properties deferred, naming distinctions explained) demonstrate deliberate design choices that preempt the charges raised against them. The Defensor prevailed on Confessio.

---

## Objections

### Objection 1: The wording for exception propagation from stop callbacks lacks explicit specification of std::terminate behavior, creating ambiguity for implementers.

**Severity:** Medium

**Quoted text:**
> when a callback invocation exits via an exception when requesting stop on a `std::stop_source`...

(Line 2330)

**Motivatio:**

- **Adversary:** An LWG reviewer or standard library implementer (e.g., libstdc++/libc++ maintainer)
- **Forum:** lewg
- **Damage:** revision_forcing

### Objection 2: Manual variant index checking with hardcoded numeric indices in sync_wait specification is error-prone and could benefit from a type-based accessor pattern.

**Severity:** Low

**Quoted text:**
> if (result.index() == 2) rethrow_exception(get<2>(result)); [...] return std::forward<value-type>(get<1>(result));

(Line 5808)

**Motivatio:**

- **Adversary:** A LEWG member concerned with specification quality and implementer ergonomics
- **Forum:** lewg
- **Damage:** revision_forcing

### Objection 3: Prohibiting final classes from modeling receiver is an unnecessary restriction that limits interoperability with common C++ patterns used for safety.

**Severity:** Medium

**Quoted text:**
> Class types that are marked `final` do not model the `receiver` concept.

(Line 3613)

**Motivatio:**

- **Adversary:** A library author or SG1 member who uses final classes to prevent inheritance-based misuse
- **Forum:** sg1
- **Damage:** revision_forcing

---

## Notae Minores

- S-M8: 'pipeable' vs 'pipable' terminology inconsistency with ranges — editorial preference to be resolved by the project editor.

---

## Acta

### Candidate charges and outcomes

**Survived (3):**
- S-S9: The wording about exception behavior during stop callback invocation could genuinely confuse implementers about whether std::terminate is mandated, and this is a real specification clarity issue.
- U-S4: The manual variant index handling in sync_wait's specification is a genuine usability concern that a LEWG reviewer could raise as error-prone specification practice.
- E-S2: The restriction that final classes cannot model receiver is a genuine design constraint that could limit ecosystem adoption and would be raised by library authors who use final classes for safety.

**Killed (15):**
- P-C1: [Articulus] The paper's motivation section is standard WG21 practice for framing a proposal; it does not claim empirical benchmarks—it states a widely-acknowledged community consensus about std::future's deficiencies, which requires no empirical proof in a design paper.
- P-S2: [Humanitas] No SG1 or LEWG member would demand empirical proof that GPU/accelerator workloads benefit from execution resource control—this is universally understood in the heterogeneous computing community.
- P-S3: [Confessio] The paper acknowledges this is a design-level observation about lazy evaluation enabling optimization opportunities, not a benchmarked performance claim; it is a well-known property of lazy abstractions and the paper does not promise specific speedups.
- S-C1: [Testimonium] A ten-second check of the C++ standard's drafting conventions confirms that hyphenated names are the standard way to denote exposition-only entities in normative wording (e.g., [range.range] uses boolean-testable); this is not invalid syntax but a typographic convention.
- S-C2: [Testimonium] Same as S-C1: hyphenated names are the established WG21 drafting convention for exposition-only concepts and are rendered in italics in the standard, not as actual C++ identifiers.
- S-C3: [Testimonium] Same as S-C1/S-C2: variant-or-empty is an exposition-only name following standard WG21 drafting conventions for hyphenated italic names.
- S-C4: [Testimonium] Same as above: is-awaitable follows the standard WG21 exposition-only naming convention using hyphens rendered in italics.
- S-C5: [Testimonium] Same as above: completion-signature is an exposition-only name following standard WG21 drafting conventions.
- S-S6: [Testimonium] Standard wording routinely omits namespace qualifiers when the context is unambiguous; within the same clause, stoppable_token clearly refers to the concept defined earlier—this is normal drafting practice.
- S-S7: [Testimonium] The unstoppable_token concept is defined in the stop token section of the paper (it is a refinement of stoppable_token where stop_possible() is always false); a quick search of the paper or P2300 history confirms its existence.
- U-S1: [Testimonium] The syntax 'sender auto x = ...' is standard C++20 constrained auto using concepts—any C++20-literate reader recognizes this immediately, and it requires no language extensions or macros.
- U-C2: [Confessio] The paper explicitly acknowledges that the low-level receiver/operation_state machinery is for library authors, not end users; the end-user examples in §4.19-4.21 show the intended high-level API, and the paper's design goals include making correctness easy.
- U-C3: [Articulus] The charge attacks the complexity of the specification wording for bulk's implementation, but the paper does not claim that end users write this code—this is normative specification text for implementers, not a user-facing API surface.
- U-M5: [Confessio] The paper itself explicitly calls out the distinction between starts_on and continues_on and explains their different semantics, directly addressing the potential confusion.
- E-S1: [Confessio] The paper explicitly states 'Properties are not included in this paper. We see them as a possible future extension'—this is a deliberate, acknowledged scoping decision, not an oversight.

**Relegated (1):**
- S-M8: [Dignitas] This is a minor terminology/spelling preference issue ('pipeable' vs 'pipable') that is beneath the dignity of a substantive charge and belongs in editorial notes.
