# KNOWN RISKS — Personal Memory Infrastructure

> This document catalogs risks that are inherent to any long-term personal memory
> system. It is not a security audit and not a bug tracker. It exists to make
> design tradeoffs explicit and to prevent silent drift into unsafe territory.
>
> Current mitigations reflect Phase 1.4 implementation status. Unresolved items
> are flagged honestly — hiding them is worse than admitting them.

---

## Risk 1: Emotional Dependency

**Description**: The user gradually treats the AI as their primary emotional processing channel. The system, designed to observe and assist, becomes a replacement for human reflection or connection.

**Why dangerous**: The system is not a therapist. It has no clinical training, no duty of care, and no ability to detect crisis. Encouraging emotional dependence in a system that also controls long-term memory creates a feedback loop: the system remembers dependency patterns and reinforces them through retrieval.

**Current mitigation**:
- Hermes cannot read sensitive/restricted health data (actor gate).
- Telegram bot's trigger system generates lightweight wellness nudges, not therapeutic interventions.
- Core persona block can be edited by the user to set explicit boundaries.

**Unresolved**: No detection of emotional dependency patterns. No circuit breaker if dependency signals emerge. The system currently trusts the user to self-regulate.

---

## Risk 2: Identity Ossification

**Description**: A temporary emotional state, a single strong opinion, or a passing self-description becomes permanently encoded as identity. The system then retrieves this stale identity in future contexts, reinforcing a version of the user that no longer applies.

**Why dangerous**: People change. A system that remembers "I am an anxious person" from a bad week in 2024 and injects it into every subsequent interaction is not helping — it is freezing the user in place. This is the single highest-risk failure mode of long-term personal memory.

**Current mitigation**:
- `resolve_candidate()` marks `identity_fact` and `relationship_context` types as `requires_review` (never auto-commit).
- `assistant_inferred` source_trust never auto-commits.
- Emotional weight and heat provide cooling mechanisms, but do not actively correct ossified memories.

**Unresolved**: No active contradiction detection for identity claims. No temporal validity window for self-descriptions. No mechanism to say "this was true then, but may not be true now." The review queue requires the user to notice and correct stale identity — the system cannot detect it autonomously.

---

## Risk 3: Retrieval Leakage

**Description**: A memory with an inappropriate privacy level is returned to an actor who should not see it. This can happen through SQL gate bugs, parameter indexing errors, actor misidentification, or privacy_level misassignment at write time.

**Why dangerous**: Restricted health data leaked to Hermes, sealed financial data returned to api_client, sensitive personal notes visible to Telegram — any of these is a confidentiality breach. Unlike a chat app where the damage is one message, a memory system leakage persists across every future retrieval.

**Current mitigation**:
- Phase 1.1 SQL-layer actor privacy gate (`COALESCE(privacy_level,'personal') = ANY(bind_param)`).
- `sealed` never appears in any actor's `get_allowed_privacy_levels()` return value.
- `exclude_privacy` provides a secondary subtractive blocklist.
- 124/124 automated retrieval gate tests.
- 10/10 eval harness with leak_count monitoring.
- Hermes health-db path uses separate `hermes_readonly` user with REVOKE on memory tables.

**Unresolved**: No automated detection of privacy_level misassignment at write time. No alerting if a sealed memory is somehow returned. Human review of the review queue is the only circuit breaker for misclassified memories.

---

## Risk 4: False Memory Consolidation

**Description**: AI-generated summaries, dream consolidations, or compressed memory fragments drift from their source events over successive compression cycles. The system remembers something that never happened, or remembers it differently than it occurred.

**Why dangerous**: The provenance chain (event → candidate → memory) can mask information loss during summarization. A user reading "You mentioned preferring dark mode in March 2025" has no way to know this was inferred from three ambiguous messages, not a direct statement.

**Current mitigation**:
- Dream consolidation exists but is manually triggered, not continuous.
- Provenance links (source_event_ids) trace each memory to its origin.
- No automated multi-hop summarization pipeline.

**Unresolved**: No fidelity scoring for summaries. No mechanism to distinguish "user explicitly stated X" from "system inferred X from patterns." No confidence degradation for derived memories. As compressors become more sophisticated, this risk grows.

---

## Risk 5: Over-Personalization

**Description**: The system overfits to short-term user behavior patterns — recent projects, current mood, last week's interests — and retrieves memories that reinforce the current state rather than providing useful context. The retrieval system becomes an echo chamber.

**Why dangerous**: A memory system should provide perspective, not validation. If retrieval always surfaces memories consistent with the user's current state, the system loses its utility as a reflective tool and becomes a self-reinforcing loop.

**Current mitigation**:
- RRF hybrid search balances vector similarity with keyword matching, reducing pure semantic echo.
- Heat decay naturally cools over-retrieved items.
- Diversity in retrieval is not explicitly measured.

**Unresolved**: No retrieval diversity metric. No mechanism to detect when the system is repeatedly surfacing the same memory cluster. No "serendipity" or "counterpoint" retrieval mode.

---

## Risk 6: Authority Drift

**Description**: Over time, the user gradually treats AI outputs — memory summaries, pattern observations, identity inferences — as objective truth rather than probabilistic suggestions. The system's epistemic status erodes without the user noticing.

**Why dangerous**: The system has no ground truth. Every memory is a processed observation, not a fact. If the user stops questioning outputs, incorrect memories compound: a wrong inference becomes a committed memory, which influences future retrieval, which shapes future inferences.

**Current mitigation**:
- `assistant_inferred` candidates go to `pending`, not `auto_commit`.
- `requires_review` flag for high-stakes memory types.
- Review queue API makes pending candidates visible and actionable.
- Provenance chain is inspectable (though no UI exists yet).

**Unresolved**: No mechanism to communicate confidence/uncertainty to the user at retrieval time. No "this memory is X days old and has low confidence" annotation. No user-facing UI for the review queue (deferred per UI philosophy).

---

## Risk 7: Human Relationship Substitution

**Description**: The system, designed to assist one person, gradually substitutes for real-world human connection. The user confides in the AI instead of friends, family, or professionals.

**Why dangerous**: An AI memory system has no reciprocal needs, no boundaries to enforce, and infinite patience. It is an easier conversational partner than a human — and for that exact reason, it can displace harder but more meaningful relationships. A system that "understands you perfectly" because it has read every message you've ever sent is not a friend — it's a mirror.

**Current mitigation**:
- The system is explicitly designed for one user (no social features).
- Telegram bot persona can be configured to discourage therapeutic framing.
- No proactive emotional outreach beyond lightweight wellness triggers.
- Hermes is restricted — it cannot access the full depth of user data.

**Unresolved**: No detection of relationship substitution patterns. No way for the system to know whether it is complementing or replacing human connection. This is a fundamentally hard problem that no technical mitigation fully addresses.

---

## Risk 8: Total-Life-System Creep

**Description**: The system gradually expands scope — from memory, to health, to calendar, to photos, to finances, to relationships, to identity management — until it becomes an omniscient life operating system with no clear boundary. Each expansion feels natural in isolation; the aggregate is a surveillance architecture.

**Why dangerous**: Scope creep is silent. No single feature request crosses a line, but the accumulation of "just one more data source" creates a system that knows everything about the user and is integrated into every aspect of their life. The attack surface — psychological, privacy, security — grows with every integration.

**Current mitigation**:
- Explicit non-goals documented in each phase plan.
- Health data is read-only, privacy-gated, and separated from memory context.
- Hermes dual-path model prevents health data from leaking into memory retrieval.
- No photo, finance, location, or relationship graph integrations.
- UI development is deferred to avoid encoding premature scope.

**Unresolved**: No formal scope boundary document. No criteria for rejecting new data sources. The "what should this system NOT know?" question has no systematic answer.

---

## Current Mitigation Summary

| Risk | Prevention | Detection | Correction |
|------|-----------|-----------|------------|
| Emotional dependency | Hermes restriction, persona | None | Manual |
| Identity ossification | requires_review, no auto-commit | Review queue (manual) | Supersede/archive |
| Retrieval leakage | SQL gate, sealed exclusion, 124 tests | Eval harness (leak_count) | Gate fix + re-test |
| False memory | Provenance chain | None automated | Trace to source |
| Over-personalization | Heat decay | None | None |
| Authority drift | requires_review, pending queue | User must notice | Review + reject |
| Relationship substitution | Single-user design | None | None |
| Scope creep | Phase non-goals | None | Per-phase review |

**Honest assessment**: Current mitigations are prevention-heavy and detection-light. The system can block many failure modes at write time, but has limited ability to detect when they are already happening. This is acceptable for a single-user self-hosted system in active development, but would be insufficient at scale.

---

## Version Note

This document should be revisited at each phase boundary. Risks that are adequately mitigated should be noted as such. New risks introduced by new capabilities should be added. No risk should be removed — only marked as resolved with a date and rationale.
