# `std::channel<T>` for Inter-Thread Communication

| Document | P9999R0 |
|---|---|
| Date | 2026-05-15 |
| Audience | LEWG |
| Author | Test Fixture |

## Abstract

We propose `std::channel<T>` as a vocabulary type for inter-thread message passing.

## 1. Motivation

C++ lacks a standard channel abstraction. Every codebase rolls its own mutex-and-condition-variable pattern, and most contain subtle bugs around shutdown ordering and bounded-buffer backpressure. A vocabulary type for inter-thread communication should be added to the C++ standard library.

The callback approach is a relic of single-threaded thinking.

## 2. Default Capacity

The default channel constructor must require a capacity argument; an implicit unbounded form must not exist. Unbounded queues cause buffer-bloat hangs that are hard to diagnose, and the standard library should not invite that hazard.

## 3. Allocator Choice

Two positions on allocator handling have been advocated by reviewers of earlier drafts.

**Position A.** The standard channel must allocate from the global `std::allocator`. The convention across every allocating standard container is that the default allocator is `std::allocator`, and a new container type should not break that pattern.

**Position B.** The standard channel must default to a user-supplied `std::pmr::polymorphic_allocator`. Independent measurements taken on three production deployments of pmr-defaulted message queues report a 2x throughput regression compared to the equivalent default-allocator implementation.

Positions A and B are mutually exclusive; both cannot be the default.

## 4. Prior Art

This section documents verifiable facts about prior implementations of channel-like primitives in other languages and libraries. None of the statements below are arguments for adoption; they are background.

Boost.Lockfree first published its SPSC queue in version 1.49, released in February 2012.

The Java standard library introduced `java.util.concurrent.BlockingQueue` in JDK 1.5, released September 30, 2004.

## 5. Out of Scope

Integration with `std::execution` senders is left to a companion paper P9001R0.

Any threading library without channels is fundamentally incomplete.

## 6. References

- N4860 (ISO C++ working draft)
- P9001R0 (fictional companion paper)
