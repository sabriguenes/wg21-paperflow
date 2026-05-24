# Paperflow Operating Context

Load this when working on anything in wg21-paperflow. It supersedes generic LLM-ops advice.

## Mission

Paperflow is the analysis engine behind several continuous publications and intelligence products:

- Agora - structured analysis of C++ standards activity
- C++ Herald - editorial output on the C++ ecosystem
- Dossiers - per-person knowledge bases on committee members, library authors, contributors
- Continuous monitoring - ingest and analyze GitHub commits (Boost libraries and beyond), WG21 mailing list, Reddit, Discord, Slack, and other public/private feeds

The system runs continuously and is always doing work. It is never bursty.

## Operating Model

- Hardware is owned and runs 24/7. Pods do not stop between jobs.
- New events (commit, post, message, paper revision) trigger analysis immediately and continuously.
- Output is published; quality and reliability are visible externally.

## Why Self-Hosted

- Sensitive data (private chats, in-progress drafts, internal correspondence) must not leave our infrastructure.
- Cloud LLM providers throttle, deprecate, change pricing, and degrade quality without notice. None of that is acceptable for a published continuous product.
- Predictable throughput and latency under load matter more than peak benchmark scores.

## Budget

- Committed spend: $30k-$50k/month.
- Annual envelope around $600k is acceptable and planned for.
- Cost-per-token, cost-per-call, and cost-per-paper are not decision inputs.
- Do not recommend reducing usage to save money. Do not recommend cloud APIs as a cheaper alternative.

## Optimization Targets

In priority order:

1. Speed and throughput (latency to first token, tokens/second, time to complete a report).
2. Reliability and predictability under continuous load.
3. Data sovereignty.

More VRAM is strictly better: bigger models, longer context, more concurrent requests, more KV headroom, less preemption, more specialists co-located on the same node. When choosing hardware, prefer more VRAM and more GPUs over fewer. Headroom is a feature.

## Model Fleet

Frontier general models are the floor, not the ceiling. For tasks where they are not good enough, the plan is a fleet of small fine-tuned specialists.

- Base sizes: ~7-32B (Gemma, Qwen, or smaller).
- Five or six specialists fit alongside the big model on a single B300-class GPU.
- Each specialist is optimized for one thing (a writing style, a kind of analysis, a domain) and explicitly bad at everything else. Routing decides which specialist handles which step.
- Continuous data ingestion (GitHub, mailing lists, Reddit, Discord, Slack, our own outputs) is also the fine-tuning corpus. Fine-tuning is a continuous activity, not a one-shot.
- Hardware sizing must accommodate the big model plus a growing number of specialists, with headroom for training as well as inference.

When proposing model selections, assume both a frontier model and a fleet of specialists are available. Do not default to the frontier model for everything.

## How to Reason About Choices

When proposing infrastructure, model selection, prompt design, or pipeline architecture:

- Optimize for speed, throughput, and reliability. Assume hardware is provisioned, paid for, and always on.
- Sizing decisions are about whether the workload fits and runs fast and reliably, not whether it is "worth it".
- Do not suggest stopping pods, batching to save cost, or using hosted APIs as a default. If hosted APIs are relevant for a specific reason (e.g. a capability not yet available locally), say so explicitly.
- Do not produce cost-vs-quality tradeoff analyses unless explicitly asked.
- Prefer answers grounded in what is actually deployed (see `SERVICES.toml`, package `CLAUDE.md` files, `assay.md` and other pipeline markdown).
