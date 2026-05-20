# Red Team - P2300R10

---

## Executive Summary

**Finding counts: 2 Critical, 10 Significant, 3 Minor.**

---

## I. Design Philosophy and Conceptual Model

### Significant

**D-C1. Sender Algorithm Customization Limitation**
Line 2175:
> The `bulk` sender adaptor is specialized for CUDA senders, but if the child sender overrides `bulk` by itself, the scheduler-specific specialization will not be selected.

This limitation undermines the goal of allowing senders to be customized by execution resources, as scheduler-specific optimizations may not be applied when child senders override algorithms.

**D-C2. Cancellation Complexity and Race Conditions**
Line 1432:
> The implementation of `execution::start()` needs to be careful to ensure that once a stop callback has been registered, there are no data races between a potentially concurrently executing stop callback and the rest of the `execution::start()` implementation.

The complexity and potential race conditions in cancellation implementation make it difficult to ensure correctness, contradicting the goal of making it easy to be correct by construction.

### Minor

**D-C3. Dropped Support for Untyped Senders**
Line 1024:
> Support for untyped senders is dropped; the `typed_sender` concept is renamed `sender`.

While the paper argues this is a minor limitation, it may restrict use cases where typed senders are not required or appropriate.

## II. Formal Specification

No findings.

## III. Usability and Learnability

### Significant

**U-S1. Excessive Concept Count in Simple Examples**
Line 48:
> 1.First we need to get a scheduler from somewhere, such as a thread pool. A scheduler is a lightweight handle to an execution resource. 2. To start a chain of work on a scheduler, we call Â§4.19.1 execution::schedule, which returns a sender that completes on the scheduler. A sender describes asynchronous work and sends a signal (value, error, or stopped) to some recipient(s) when that work completes. 3. We use sender algorithms to produce senders and compose asynchronous work. Â§4.20.2 execution::then is a sender adaptor that takes an input sender and an std::invocable, and calls the std::invocable on the signals sent by the input sender. The sender returned by then sends the result of that invocation. In this case, the input sender came from schedule, so its void, meaning it won't send us a value, so our std::invocable takes no parameters. But we return an int, which will be sent to the next recipient. 4. Now, we add another operation to the chain, again using Â§4.20.2 execution::then. This time, we get sent a value-the int from the previous step. We add 42 to it, and then return the result. 5. Finally, we are ready to submit the entire asynchronous pipeline and wait for its completion. Everything up until this point has been completely asynchronous; the work may not have even started yet. To ensure the work has started and then block pending its completion, we use Â§4.21.1 this_thread::sync_wait, which will either return a std::optional<std::tuple<...>> with the values sent by the last sender, or an empty std::optional if the last sender sent a stopped signal, or it throws an exception if the last sender sent an error.

The Hello World example introduces multiple concepts (scheduler, sender, then, sync_wait) that a user must understand to write a simple asynchronous program. This is overwhelming for newcomers compared to other languages where async Hello World is much simpler.

**U-S2. Confusing Naming**
Line 48:
> execution::schedulerautosch = thread_pool.scheduler(); //1 senderautobegin = schedule(sch);//2 senderautohi = then(begin,[]{//3 std::cout<<"Helloworld! Have an int.";//3 return 13;//3 });//3 senderautoadd_42 = then(hi,[](intarg){returnarg+42;});//4 auto[i] = this_thread::sync_wait(add_42).value();//5

The use of terms like 'sender', 'scheduler', and 'then' may be confusing for users familiar with other async models. The similarity in names for different concepts (e.g., 'then' in promises vs. 'then' here) could lead to confusion.

### Minor

**U-M3. Prohibitive Complexity for Common Tasks**
Line 73:
> The example builds an asynchronous computation of an inclusive scan: 1. It scans a sequence of double s (represented as the std::span<const double> input) and stores the result in another sequence of double s (represented as std::span<double> output). 2. It takes a scheduler, which specifies what execution resource the scans should be launched on. 3. It also takes a tile_count parameter that controls the number of execution agents that will be spawned.

The example for asynchronous inclusive scan is overly complex, requiring understanding of senders, schedulers, bulk operations, and more. This deters users from adopting the API for common tasks.

## IV. Performance and Scalability

### Significant

**P-S1. Unsubstantiated Performance Claim in Field Experience**
Line 890:
> A team at Meta has migrated from `folly::Future` to `unifex::task` and seen significant developer efficiency improvements.

This claim of significant developer efficiency improvements lacks supporting benchmark data or measurements to substantiate it.

**P-S2. Unsubstantiated Performance Claim in libunifex Usage**
Line 890:
> Its used to express the asynchrony in [rsys], and is therefore serving video calling to billions of people every month on Meta's social networking apps on iOS, Android, Windows, and macOS.

While this indicates widespread use, it does not provide performance benchmarks or measurements to support claims of performance benefits.

**P-S3. Unsubstantiated Performance Claim in Sender Factories and Adaptors**
Line 1538:
> We have arrived at the conclusion that a purely lazy model is enough for most algorithms, and users who intend to launch work earlier may write an algorithm to achieve that goal.

This claim about the efficiency of lazy execution lacks empirical data or benchmarks to support the assertion.

**P-S4. Unsubstantiated Performance Claim in Lazysenders**
Line 2238:
> Lazy senders fundamentally describe work, instead of describing or representing the submission of said work to an execution resource, and thanks to the flexibility of the customization of most sender algorithms, they provide an opportunity for fusing multiple algorithms in a sender chain together into a single function that can later be submitted for execution by an execution resource.

This claim about optimization opportunities lacks specific benchmarks or measurements to demonstrate the performance improvements.

## V. Ecosystem Interoperability and Missing Facilities

### Critical

**E-C1. Missing Type Erasure Support**
Line 1097:
> The proposal does not mention type erasure for senders, which is crucial for handling different types uniformly.

Type erasure is essential for generic programming with senders, allowing uniform handling of various types. Its omission limits the proposal's expressiveness and flexibility.

**E-C5. Dependencies on Unfinished Proposals**
Line 1276:
> The proposal depends on unfinished companion proposals for certain features.

Features like time-based scheduling and I/O rely on other proposals, which may delay their availability and completeness.

### Significant

**E-S2. Lack of Timer Facilities**
Line 1276:
> The proposal does not mention timer facilities, which are essential for many async operations.

Timers are fundamental for scheduling delayed or periodic tasks. Without them, the ecosystem lacks necessary functionality for real-world applications.

**E-S3. Incomplete I/O Support**
Line 191:
> While some I/O examples are given, comprehensive I/O support might be missing.

The proposal provides examples for async I/O but lacks a comprehensive framework, making it insufficient for robust I/O handling.

### Minor

**E-M4. No Standard Thread Pool Implementation**
Line 1276:
> The proposal refers to thread pools but doesn't provide a standard implementation.

A standard thread pool is essential for portability and ease of use. Its absence may lead to vendor lock-in or additional implementation effort.

---
