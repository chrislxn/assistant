# VISION — Long-Term Memory Infrastructure

> This is a philosophy and design-intent document, not an implementation roadmap.
> It describes what the system aims to become, not what it currently is.
> Current capabilities are documented in ARCHITECTURE.md and STATUS.md.

---

## 1. Project Vision

This is not a chat log archive.
This is not a second-brain note-taking system.
This is not an emotional companion or AI soulmate.

The long-term goal is a **personal cognitive infrastructure** that is:

- **Traceable** — every committed memory has provenance back to a raw event.
- **Auditable** — a human can inspect why something was remembered and challenge it.
- **Evolvable** — memory schemas, taxonomies, and retrieval rules can change without losing integrity.
- **Privacy-governed** — what an agent sees is a function of who it is, not what exists.

The system exists to help one person understand themselves across time — not to construct a perfect replica of their mind, not to replace their judgment, and not to accumulate data without purpose.

**Memory semantics, identity safety, retrieval governance, temporal reasoning, and the human-agent boundary** are the core concerns. "More data" is not the goal.

---

## 2. Core Philosophical Principles

### 2.1 Raw event ≠ memory

A sensor reading, a chat message, a health data point — these are events, not memories. Memory is the result of extraction, filtering, and commitment. The system must preserve the distinction between "something happened" and "we decided to remember it this way."

### 2.2 Candidate ≠ truth

An AI-proposed memory is a hypothesis. Until reviewed and committed, it carries no authority. The candidate layer exists precisely to make this gap visible and actionable.

### 2.3 Committed memory ≠ immutable truth

A committed memory is the system's best-known state at a point in time. It can be superseded, invalidated, or archived when better information arrives. The system must support revision without losing provenance.

### 2.4 Emotional state is not stable identity

A moment of sadness, excitement, or anxiety is real — but it is not who the user is. The system must treat emotional signals as transient observations, not identity-defining facts. Summarization over time windows is acceptable; permanent emotional labels are not.

### 2.5 AI may propose, not define the user

The system can observe patterns, surface trends, and suggest candidates. It cannot silently rewrite identity claims, redefine long-term narratives, or infer stable personality traits from short-term behavior. The user is the sole authority on who they are.

### 2.6 Retrieval safety > recall

In the current phase, preventing the wrong memory from being seen is more important than ensuring every relevant memory is found. A privacy leak (Hermes seeing restricted health data) is worse than a missed retrieval (failing to surface a relevant project note). This priority may shift as the system matures, but safety gates must never weaken.

### 2.7 Forgetting is a feature, not a failure

A system that remembers everything is not intelligent — it is a database. Forgetting stale information, compressing low-signal chatter, and allowing memories to cool are essential functions. See §4.

### 2.8 Provenance is mandatory

Every committed memory must trace back to one or more raw events. No memory appears from nowhere. This chain enables audit, challenge, and correction.

### 2.9 Long-term memory must remain inspectable and reviewable

As the system accumulates years of data, the user must retain the ability to review, search, challenge, and delete memories. Opaque vector stores with no human-readable surface are not acceptable as the sole retrieval layer.

---

## 3. Memory Temporal Model

Memories exist at different levels of permanence, review, and compression. The hierarchy below is a design target — not all levels are implemented.

| Layer | Example | Lifecycle | Review | Auto-compress | Long-term retrieval | Risk |
|-------|---------|-----------|--------|---------------|---------------------|------|
| **1. Raw event** | Chat message, heart rate reading, calendar entry | Append-only, immutable | Never (source of truth) | No | No (used for provenance) | Low |
| **2. Transient emotional cache** | "User sounds frustrated today" | Hours to days, auto-expire | No | Yes (→ layer 3) | No | Medium — must not persist as identity |
| **3. Summarized emotional trend** | "User showed elevated stress over 2 weeks in March" | Weeks to months | Optional | Yes (→ layer 4) | Yes, but low weight | Medium — aggregation window matters |
| **4. Soft memory** | "User mentioned preferring dark mode" | Months, access-cooled | No | Yes (→ layer 5 or forget) | Yes, gated by heat | Low |
| **5. Factual memory** | "User lives in Shanghai, uses Arch Linux" | Long-term, rarely invalidated | Available for review | No (manual edit only) | Yes | Low-medium |
| **6. Identity / relationship / health baseline** | "User identifies as introverted", "Father passed away 2023" | Long-term, very rarely revised | Requires review | **Never** | Yes, high caution | **High** |
| **7. Core blocks** | Response policy, active projects | Versioned, approval-gated | Explicit review required | **Never** | Yes, always injected | **High** |

**Critical rule**: Short-term emotional states must never be permanently solidified as identity (layer 2 → layer 6 without review is a design violation). Emotional compression happens across time windows, not from single events.

**Current implementation status**: Layer 1 (memory_events) ✓, Layer 4-5 (memories/memory_items) ✓, Layer 7 (core_blocks) ✓. Layers 2-3 (emotional cache/summarization) are deferred. Layer 6 (identity baseline) partially exists as high-importance memories but lacks dedicated schema.

---

## 4. Forgetting and Compression Philosophy

The system's goal is not infinite preservation. Memory infrastructure without forgetting is surveillance, not intelligence.

### Design mechanisms (current and planned)

- **Temporal decay**: Heat decays over time. Cold memories are retrieved less often.
- **Summarization**: Multiple low-signal fragments can be compressed into a single summary.
- **Stale memory cooling**: Memories not accessed for extended periods lose retrieval priority.
- **Access-based heat**: Frequently recalled memories stay warm; unused ones cool.
- **Confidence degradation**: Memories derived from single low-confidence observations degrade faster.
- **Periodic consolidation**: Dream-like processes can fuse related fragments and discard noise.
- **Lossy compression for low-risk chatter**: Casual conversation fragments can be summarized with information loss; factual and identity memories cannot.

### What should be forgotten

- Transient emotional states after the observation window closes.
- Duplicate or near-duplicate factual observations.
- Low-signal daily chatter with no extractable pattern.
- Obsolete project states after explicit archival.
- Stale preferences that have been superseded by newer observations.

### What must not be silently forgotten

- Identity-level facts (review-gated).
- Health baselines (review-gated, privacy-restricted).
- Relationship context (review-gated).
- Core blocks (explicit versioning, no auto-deletion).

**The real danger is not that the AI forgets the user — it is that the AI permanently remembers a version of the user that was transient.**

---

## 5. Agent Boundary Philosophy

### 5.1 What AI agents can do

- Observe events and record them with provenance.
- Summarize patterns across time windows.
- Retrieve memories within their actor privacy boundary.
- Assist the user in reflection, planning, and recall.
- Propose memory candidates for human review.
- Flag contradictions, staleness, or potential privacy issues.

### 5.2 What AI agents cannot do

- Silently rewrite identity-level facts.
- Auto-commit high-stakes memory types (health patterns, identity, relationships).
- Redefine long-term narrative without user awareness.
- Self-expand their own permissions or actor scope.
- Bypass the review queue for `requires_review` candidates.
- Infer stable identity traits from transient emotional signals.
- Access memories outside their privacy level gate.

### 5.3 Hermes specifically

Hermes is a restricted external agent. It can observe, propose, and read within `public_like` + `personal` only. It cannot read `sensitive`/`restricted`/`sealed`. It cannot write committed memories directly. It cannot write core_blocks. These constraints are architectural, not advisory.

---

## 6. UI Philosophy

Web UI development has been intentionally deferred. Memory semantics are not yet stable enough to commit to a UI paradigm. Building a UI too early would encode assumptions that later prove wrong and constrain the memory model to fit the interface.

### First-generation UI should be

- **Candidate review stream**: A feed of pending memory proposals, with accept/reject/edit actions.
- **Provenance inspector**: Trace any committed memory back to its source events.
- **Memory debugger**: Search, inspect privacy levels, check heat/access, validate actor gates.
- **Core block editor**: Versioned editing of response policy, active projects, and other curated blocks.

### First-generation UI should not be

- An AI soulmate dashboard with emotional state visualizations.
- An omniscient life graph claiming to represent the user's entire existence.
- A total-life timeline suggesting the system knows everything that ever happened.
- A personality profiler inferring traits from conversation patterns.

These may or may not be useful one day. They are not the starting point. The first UI must make the system's decisions inspectable and correctable — not impressive.

---

## 7. Long-Term Directions (Non-Committed)

The following directions are noted as natural extensions of the philosophy above. None are currently implemented or committed to a timeline.

- **memory_items primary retrieval**: Promote memory_items from shadow/eval to primary retrieval source.
- **Embeddings on memory_items**: Enable vector/semantic search alongside keyword (deferred schema decision).
- **Temporal reasoning**: Query memories by time range, detect gaps, understand sequences.
- **Reflection layer**: Periodic agent-led review of recent memories for contradiction, staleness, or consolidation.
- **Memory consolidation (Dream v2)**: Structured fusion of fragments into higher-level memories with explicit provenance linking.
- **Event graph**: Link events, candidates, and committed memories into a navigable causal graph.
- **Planner agent**: Use memory context to assist with task planning while respecting the agent boundary.
- **Health trend integration**: Temporal analysis of health data with privacy-gated retrieval.
- **Multimodal memory**: Images, voice notes, and documents as memory sources (with appropriate extraction pipelines).

These are aspirational, not prescriptive. Each must be evaluated against the principles in §2 before implementation.

---

## 8. Version Note

This document describes design intent, not current implementation. For what the system actually does today, see:

- `ARCHITECTURE.md` — current system architecture.
- `STATUS.md` — current phase and verification status.
- `KNOWN_RISKS.md` — known risks and mitigations.
- `MEMORY_ITEMS_RETRIEVAL_BRIDGE.md` — Phase 1.4 bridge design.
