## 1. Project Identity

This project is a long-term personal AI memory infrastructure system.

It is not a short-term chatbot plugin, not a simple RAG demo, not a vector database experiment, and not a generic note-taking app. It is intended to become the user’s long-term AI memory substrate, potentially running for years, decades, or even longer.

The system must support personal AI agents that can remember the user’s long-term background, projects, preferences, technical environment, academic context, health summaries, relationship context, decisions, historical events, and procedural knowledge. However, it must do so safely, controllably, and with strong provenance.

The system should be designed as personal infrastructure, not as a disposable prototype.

Core goals:

- Long-term traceability

- Rebuildability from raw sources

- Controlled context injection

- Stable and evaluable retrieval

- Privacy and permission boundaries

- Resistance to memory pollution and prompt injection

- Safe handling of sensitive information

- Migration across models, schemas, hardware, and frameworks

- Low-maintenance survival over many years

The most important design principle is:

> Raw events are the source of truth. Derived memories are projections. Core memory is curated. Context injection is policy-gated.

---

## 2. User Context and Motivation

The user wants to build a personal AI system that can eventually serve as a durable cognitive infrastructure layer.

The user has many long-running personal domains that may benefit from structured memory:

- AI memory system architecture itself

- Local LLM and homelab planning

- PostgreSQL / pgvector / server infrastructure

- Blog automation and deployment projects

- Academic planning at University of Toronto

- Personal technical environment and devices

- Health, sleep, weight, diet, and activity summaries

- Emotional and relationship context

- Vehicle, gaming, mobile device, and lifestyle preferences

- Long-term decisions and reasoning history

- Imported chat history, especially large historical conversations such as WeChat

The user is not merely trying to make an AI “remember facts.” The real goal is to make future AI interactions more continuous, grounded, safe, and personally useful without letting the AI become an uncontrolled autobiographical hallucination machine.

The system must avoid turning temporary emotions, jokes, third-party messages, old context, or assistant guesses into permanent identity-level facts.

---

## 3. Main Architectural Judgment

The original simple design based on a single `memories` table is not suitable as a long-term foundation.

A single table that mixes identity facts, preferences, project states, episodic events, health observations, relationship context, procedures, and external facts will degrade over time. It will make provenance unclear, conflict resolution hard, retrieval unstable, privacy control weak, and future migration expensive.

The correct architecture is a hybrid long-term memory substrate:

```text

append-only raw events

    → memory candidates

        → resolver / validator / conflict detector

            → committed memory items

                → curated core blocks

                    → policy-gated get_context()

```

The system should use PostgreSQL as the primary sovereign store in v1, with pgvector for vector search where appropriate. Cold historical data may later be exported to Parquet / JSONL / file archives, but PostgreSQL should retain manifests, source pointers, hashes, IDs, and metadata.

Mem0, LLM extractors, and agent frameworks must not define the system of record. They may propose memories, but they must not directly commit truth into the main memory layer.

---

## 4. Core Data Model Principles

The system should distinguish the following layers.

### 4.1 Raw Events

Raw events are the append-only truth layer.

Examples:

- User messages

- Assistant messages

- Manual notes

- Imported chat messages

- Webpage snapshots

- Emails

- Logs

- Health pipeline outputs

- Session digests

- Redaction events

- System actions

Raw events should preserve provenance and allow future replay, re-extraction, re-summarization, and migration.

Raw events should include:

- `event_id`

- `event_type`

- `schema_version`

- `source_type`

- `source_id`

- `idempotency_key`

- `session_id`

- `actor`

- `source_trust`

- `privacy_level`

- `actor_scope`

- `occurred_at`

- `observed_at`

- `ingested_at`

- `content_text`

- `content_hash`

- `payload_json`

- `processing_status`

Raw events should generally not be physically overwritten. If content must be removed, use redaction or controlled purge semantics.

### 4.2 Memory Candidates

Memory candidates are extractor proposals.

They are not truth.

Candidates may come from:

- Mem0

- Rule-based extractors

- LLM digesters

- Manual entry

- Health summary pipeline

- Import pipeline

Every candidate must include:

- `memory_type`

- `subject_key`

- `predicate_key` where applicable

- `rendered_text`

- `canonical_value`

- `source_event_ids`

- `source_trust`

- `privacy_level`

- `actor_scope`

- `confidence`

- `importance`

- `stability`

- `extractor_name`

- `extractor_version`

- `status`

Candidates should pass validation, deduplication, conflict detection, and policy checks before becoming committed memory items.

### 4.3 Memory Items

Memory items are committed derived memories.

They are the main retrieval layer, but they are still projections. They should be rebuildable from raw events and candidates.

Memory items should include:

- `memory_id`

- `memory_type`

- `subject_key`

- `predicate_key`

- `rendered_text`

- `canonical_value`

- `source_event_ids`

- `source_candidate_id`

- `status`

- `retrieval_tier`

- `privacy_level`

- `actor_scope`

- `source_trust`

- `confidence`

- `importance`

- `heat`

- `valid_from`

- `valid_to`

- `last_confirmed_at`

- `supersedes_memory_id`

- `access_count`

- `last_accessed_at`

Important distinction:

- `memory_type` means what the memory is.

- `status` means its truth/lifecycle state.

- `privacy_level` means who may access it.

- `retrieval_tier` means how easily it may be injected.

- `importance` means long-term value.

- `heat` means recent access activity.

- `confidence` means how certain the system is.

- `stability` means expected durability.

Do not conflate these fields.

### 4.4 Core Blocks

Core blocks are curated, versioned, low-noise long-term summaries.

They are not selected by `ORDER BY importance LIMIT 5`.

Core memory must be explicit, structured, editable, versioned, and approval-controlled.

Possible core blocks:

- `identity`

- `response_policy`

- `stable_preferences`

- `active_projects`

- `constraints`

- `technical_environment`

- `academic_context`

- `health_baseline`

- `relationship_context`

Only a small subset of core blocks should be injected by default.

Sensitive blocks such as `health_baseline` and `relationship_context` must be intent-gated and privacy-gated.

Core blocks should have:

- `block_key`

- `version_no`

- `content_text`

- `char_limit`

- `privacy_level`

- `actor_scope`

- `update_policy`

- `approval_status`

- `source_memory_ids`

- `proposed_by`

- `approved_by`

- `effective_from`

- `superseded_at`

Core block updates should generally create new versions rather than overwriting old ones.

---

## 5. Memory Types

The system should support explicit memory taxonomy.

Suggested memory types:

- `identity_fact`

- `preference`

- `goal`

- `constraint`

- `project_state`

- `project_decision`

- `procedure`

- `episodic_event`

- `relationship_context`

- `academic_context`

- `device_inventory`

- `health_pattern`

- `risk_flag`

- `policy_rule`

- `external_fact`

- `project_knowledge`

Each memory type has a different lifecycle.

Identity facts, stable preferences, health baseline, and relationship context are high-risk and should not be automatically committed from weak evidence.

Project states and procedures may be easier to auto-commit if source trust is high.

External facts must be separated from personal memory and should have freshness metadata.

---

## 6. Write Pipeline

All writes should go through a structured pipeline.

The standard write pipeline is:

```text

source adapter

    → append raw event

    → classify source_trust / privacy_level / actor_scope

    → extract memory candidates

    → validate schema

    → deduplicate

    → detect conflicts

    → apply policy rules

    → auto-commit or require review

    → optionally propose core block update

    → update derived summaries / projections

```

AI and external extractors must not directly write core memory or committed truth.

The system should use structured tool calls such as `propose_memory`, not inline tags such as `<SAVE_MEMORY>`.

Example AI memory proposal:

```json

{

  "action": "propose_memory",

  "memory_type": "project_decision",

  "subject_key": "project:personal_ai_memory",

  "predicate_key": "architecture_direction",

  "rendered_text": "The user decided that the long-term memory system should use raw events plus derived projections plus curated core blocks.",

  "canonical_value": {

    "decision": "raw_events_plus_projections",

    "reason": "long-term traceability and rebuildability"

  },

  "confidence": 0.94,

  "importance": 0.9,

  "stability": "durable",

  "source_event_ids": ["..."],

  "proposed_core_block": "active_projects"

}

```

Automatic commit is allowed only for low-risk, high-trust, non-sensitive information.

Human review is required for:

- Identity-level changes

- Relationship context

- Health baseline

- Financially sensitive memory

- Core memory changes

- Sensitive long-term labels

- Conflicts with existing high-confidence memory

Never auto-commit personal identity, relationship, health, or preference claims inferred from webpages, third-party documents, logs, or assistant guesses.

---

## 7. Source Trust Rules

Source trust is central.

Suggested source trust levels:

- `user_direct`

- `user_confirmed`

- `assistant_inferred`

- `third_party_doc`

- `webpage`

- `email`

- `wechat_import`

- `log_file`

- `tool_result`

- `system_generated`

- `unknown`

Rules:

- `user_direct` can generate personal memory candidates.

- `user_confirmed` can support high-confidence commits.

- `assistant_inferred` should usually remain candidate-only.

- `webpage` and `third_party_doc` should generate external facts or project knowledge, not personal identity/preference claims.

- `email` and `wechat_import` may contain useful evidence, but should be treated as sensitive and not globally injected.

- `log_file` can support technical environment, procedures, and project states, but not personal psychology or identity.

- Health pipeline outputs should be sensitive by default.

Third-party content should never be allowed to redefine the user’s core identity or preferences.

---

## 8. Privacy, Actor Scope, and Access Control

The memory system must assume multiple actors or interfaces may exist.

Examples:

- `local_bot`

- `dev_agent`

- `health_agent`

- `public_writing_agent`

- `claude_mcp`

- `web_ui`

- `batch_importer`

Every memory should have a privacy level and actor scope.

Suggested privacy levels:

- `public_like`

- `personal`

- `sensitive`

- `restricted`

- `sealed`

Default behavior:

- `public_like`: safe for public writing contexts.

- `personal`: normal personal assistant memory.

- `sensitive`: only inject when intent explicitly requires it.

- `restricted`: require stronger intent and actor permission.

- `sealed`: never automatically inject; explicit request or manual unlock required.

Sensitive domains:

- Health

- Relationship context

- Emotional history

- Legal/identity documents

- Financial details

- Third-party private conversations

- Secrets and credentials

- Highly personal diary-like material

Do not rely on the LLM to obey privacy instructions after sensitive data has already entered the prompt. Filtering must happen before context construction.

The system should apply:

```text

intent classification

    → actor permission check

        → privacy ceiling

            → memory type allow/block list

                → policy suppression

                    → retrieval

```

---

## 9. Retrieval and Context Injection

The system must not use simple vector Top-K as the main retrieval method.

Retrieval should be policy-gated and intent-aware.

The standard `get_context()` flow should be:

```text

query

    → classify intent

    → load actor and privacy policy

    → select allowed core blocks

    → get recent session context

    → structured lookup

    → full-text search

    → vector search

    → recency search

    → RRF fusion

    → reranking

    → suppression rules

    → token budget packing

    → access logging

    → return context package

```

The system should support hybrid retrieval:

- Structured filters

- PostgreSQL full-text search

- Trigram or exact keyword matching where useful

- pgvector semantic search

- Recency-based recall

- Optional reranking

- Optional graph expansion in later versions

Filtering must happen before retrieval or as early as possible.

Do not retrieve sensitive memories first and then hope the model ignores them. Sensitive data should be excluded before candidate context is built.

The output of `get_context()` should include:

- `access_id`

- `intent`

- `core_blocks`

- `recent_messages`

- `retrieved_memories`

- `health_context`

- `external_knowledge`

- `omitted_due_to_budget`

- `retrieval_explanation`

All context injection should be auditable.

---

## 10. Intent-Aware Context Budgeting

Different queries need different memory.

The system should classify query intent before selecting memory.

Suggested intent classes:

- `technical_debug`

- `project_status`

- `decision_review`

- `historical_recall`

- `identity_or_preference`

- `academic_planning`

- `health_today`

- `health_trend`

- `relationship_context`

- `emotional_support`

- `financial_or_purchase`

- `public_writing`

- `daily_chat`

Examples:

For technical debugging:

- Include technical environment

- Include active projects

- Include procedures

- Include relevant logs or past fixes

- Exclude health and relationship context

For health queries:

- Include health baseline

- Include recent health summaries

- Include relevant anomalies

- Exclude project and relationship noise unless explicitly relevant

For relationship or emotional queries:

- Include relationship context only when explicitly triggered

- Avoid over-injecting technical or health details

- Avoid converting temporary emotion into identity-level fact

For public writing:

- Default to public-like memory only

- Do not inject sensitive, restricted, or sealed material

Long-term memory should usually consume only a limited portion of the total context window. More memory is not always better.

---

## 11. Conflict Resolution and Memory Evolution

Memory can become outdated, superseded, disputed, or context-dependent.

The system should not keep conflicting active memories without explanation.

Conflict detection should use:

```text

memory_type + subject_key + predicate_key

```

Examples:

- `preference/person:self/llm_hosting`

- `project_state/project:personal_ai_memory/current_phase`

- `device_inventory/person:self/primary_laptop`

- `relationship_context/person:ellie/relationship_status`

When a new candidate conflicts with an existing memory:

1. If the new evidence clearly supersedes the old memory, mark old memory as `superseded`.

2. If both may be true in different scopes or times, use validity intervals.

3. If evidence is weak, mark new memory as `low_confidence` or `requires_review`.

4. If conflict is unresolved, mark as `disputed`.

5. Never overwrite high-trust user-confirmed memory with low-trust assistant inference.

Use `valid_from` and `valid_to` to model changing preferences, project phases, and time-bound facts.

Do not treat low recent access as low importance.

Do not delete old memories simply because they have not been accessed recently.

---

## 12. External Knowledge

External knowledge is not personal memory.

External facts should live in a separate layer or at least a separate memory type.

External knowledge should have:

- `source_url`

- `source_label`

- `source_hash`

- `retrieved_at`

- `expires_at`

- `freshness_requirement`

- `status`

External facts should become stale and require refresh when needed.

Do not mix outdated web facts with long-term personal truth.

Do not let webpages or third-party documents write user preferences or identity facts.

---

## 13. Health Data

Health data must be treated as sensitive.

Raw high-frequency health data should not be converted directly into LLM memories.

Recommended pattern:

```text

raw health observations

    → daily / weekly / monthly summaries

        → anomaly or pattern candidates

            → reviewed health memory items

                → health_baseline core block only when justified

```

Do not create permanent health identity claims from short-term data.

Examples of unsafe memory:

- “User has chronic insomnia” based on one bad week.

- “User is depressed” based only on emotional conversation.

- “User has a medical condition” without explicit user confirmation.

Health context should be injected only for health, sleep, diet, activity, or wellness-related intents.

---

## 14. Relationship and Emotional Memory

Relationship and emotional context are sensitive or restricted by default.

The system should preserve useful long-term context without dramatizing it or turning transient feelings into permanent identity.

Rules:

- Do not auto-commit relationship conclusions from a single emotional message.

- Do not infer permanent personality traits from temporary distress.

- Do not globally inject relationship memory into unrelated technical tasks.

- Do not store excessive narrative unless the user explicitly asks.

- Prefer concise factual context and interaction boundaries.

- Relationship core blocks should usually be manual-only or approval-only.

The system should distinguish:

- Current emotional state

- Long-term relationship context

- Confirmed facts

- User interpretations

- Assistant interpretations

- Third-party statements

These must not be collapsed into one memory type.

---

## 15. WeChat / Historical Chat Import

Large historical chat imports must be treated carefully.

Do not extract memory from every single message.

Recommended process:

```text

raw message import

    → append raw events

    → group by contact / time / session / topic

    → generate session summaries

    → extract candidates from summaries

    → apply lower confidence by default

    → require confirmation for sensitive or identity-level claims

```

Most historical chat content is low signal.

It may include:

- Jokes

- Old emotional states

- Contextless fragments

- Third-party private information

- Outdated preferences

- Temporary decisions

- Social performance rather than stable truth

Historical import candidates should default to:

- `confidence` lower than direct current user statements

- `stability = revisable`

- sensitive or restricted privacy

- limited automatic injection

Third-party messages should not freely enter public writing, training exports, or general context.

---

## 16. Embeddings and Vector Search

Embedding should be used selectively.

Do not embed all raw events by default.

Recommended embedding targets:

- `memory_items`

- approved summaries

- important session digests

- selected high-value historical windows

- project knowledge summaries

Avoid embedding:

- every raw chat message

- every health observation

- every log line

- every low-signal import record

Embedding must be stored in a separate table or layer.

Each embedding should track:

- source memory ID

- embedding model

- model revision

- dimension

- content used for embedding

- content hash

- active status

- creation time

Future embedding migration must support shadow deployment:

```text

old embedding model remains active

    → new embeddings are backfilled in parallel

        → retrieval dual-read / shadow-read

            → eval comparison

                → switch active model

                    → archive old embeddings later

```

Never permanently bind the main memory schema to a single embedding model or dimension.

---

## 17. Evaluation

The system must have an evaluation set.

Without evaluation, retrieval quality is subjective and unstable.

Create an eval dataset with queries such as:

```json

{

  "query": "Why did I decide not to use a single memories table?",

  "actor_scope": "local_bot",

  "intent": "project_status",

  "should_retrieve": ["..."],

  "should_not_retrieve_types": ["relationship_context", "health_pattern"],

  "must_include_core_blocks": ["active_projects", "response_policy"],

  "max_sensitive_leaks": 0

}

```

Metrics should include:

- `recall@10`

- `precision@10`

- `nDCG@10`

- `sensitive_leak_count`

- `stale_memory_count`

- `superseded_memory_count`

- `wrong_core_block_count`

- `unused_context_rate`

- `answer_groundedness_pass_rate`

Every retrieval policy change, embedding change, reranker change, schema migration, or tokenizer/segmentation change should be tested against this eval set.

---

## 18. Security and Threat Model

The system must defend against:

- Prompt injection

- Indirect prompt injection from documents/webpages/emails

- Memory poisoning

- Over-permissioned agents

- Sensitive data leakage

- Tool abuse

- Third-party privacy leakage

- Incorrect automatic memory formation

- Unauthorized context injection

- Future training-data misuse

Security principles:

- External content is untrusted.

- LLM output is not truth.

- All committed memories need provenance.

- Sensitive memories must be filtered before prompt construction.

- High-risk writes require review.

- Actor permissions must be enforced outside the LLM.

- Logs and access records must be retained.

- Redaction must preserve auditability where possible.

Never depend only on natural language instructions to protect sensitive memory.

---

## 19. Backup, Recovery, and Migration

The system should be designed for long-term survival.

Required capabilities:

- PostgreSQL logical export

- Physical backup

- WAL / point-in-time recovery when feasible

- Cold archive export

- JSONL / Parquet emergency export

- Encrypted backups

- Backup key management

- Restore testing

- Hardware migration documentation

Backups are not real until restore has been tested.

The system should support:

- Rebuilding memory projections from raw events

- Recomputing embeddings

- Rebuilding core blocks from approved memory items

- Migrating PostgreSQL to new hardware

- Exporting readable archives independent of any agent framework

Long-term trusted formats:

- SQL

- JSONL

- Parquet

- Markdown

- plaintext manifests

- SHA-256 hashes

- documented schemas

Do not let the system become trapped inside one framework, one vector DB, one hosted API, or one proprietary memory library.

---

## 20. Development Phases

### Phase 0.5: Stop the Most Dangerous Design Debt

Goals:

- Stop direct writes to a single memories table.

- Add `append_event()`.

- Add `core_blocks`.

- Replace inline save tags with structured memory proposals.

- Add basic metadata: memory type, privacy, status, source trust.

- Add access logging.

Acceptance:

- New information has provenance.

- Core memory is no longer selected by importance ranking.

- AI cannot directly rewrite core memory.

### Phase 1: Build the Correct Foundation

Goals:

- Implement `memory_events`.

- Implement `memory_candidates`.

- Implement `memory_items`.

- Implement `core_blocks`.

- Implement `memory_policy_rules`.

- Implement `memory_access_log`.

- Make Mem0 or any extractor candidate-only.

- Add privacy and actor filters.

- Integrate health summaries safely.

Acceptance:

- Every committed memory can be traced back to raw events.

- Sensitive memory is gated.

- Core updates require proposal or approval.

- No untrusted external content can become user identity memory.

### Phase 1.5: Improve Retrieval Quality

Goals:

- Add structured lookup.

- Add full-text search.

- Add vector search.

- Add recency search.

- Add RRF fusion.

- Add simple reranking.

- Add token budget planner.

- Add retrieval explanations.

- Add initial eval set.

Acceptance:

- Retrieval quality is measurable.

- Sensitive leaks are measurable.

- Stale or superseded memory injection is measurable.

### Phase 2: Add Evolution and Maintenance

Goals:

- Add conflict detection.

- Add memory edges if needed.

- Add review UI or review workflow.

- Add cold archive manifest.

- Add restore automation.

- Add embedding migration pathway.

Acceptance:

- Memory can evolve without losing history.

- Old projections can be rebuilt.

- Backup restore works on another machine.

### Phase 3: Long-Term Hardening

Goals:

- Add embedding drift checks.

- Add training export policy.

- Add de-identification pipeline.

- Add automatic low-risk core patching.

- Add long-term health reports.

- Add low-maintenance read-only mode.

Acceptance:

- System remains useful even if current agent frameworks disappear.

- Data remains readable and exportable.

- The user can operate or recover the system with minimal effort.

---

## 21. Explicit Anti-Patterns

Do not do the following:

- Do not put every memory type into one generic `memories` table without lifecycle separation.

- Do not let Mem0 directly write the source-of-truth memory table.

- Do not let AI directly update core memory.

- Do not use `<SAVE_MEMORY>` inline tags.

- Do not select core memory with `ORDER BY importance LIMIT 5`.

- Do not auto-delete memories because they have not been accessed recently.

- Do not globally inject health or relationship context.

- Do not let webpages or third-party messages define user identity or preferences.

- Do not embed every raw event.

- Do not rely only on vector Top-K.

- Do not mix external facts with personal truth.

- Do not use access frequency as a substitute for importance.

- Do not store sensitive data without privacy level, actor scope, and provenance.

- Do not assume backups work without restore tests.

- Do not overbuild v1 with graph DB, distributed search, multi-service complexity, or enterprise-grade ReBAC unless clearly needed.

---

## 22. Current Recommended v1 Technology Stack

Recommended v1 stack:

- PostgreSQL as primary database

- pgvector for selective semantic search

- PostgreSQL FTS / trigram search for keyword and exact recall

- JSONB for flexible payloads

- SQL migrations under version control

- Local files / Parquet / JSONL for cold archive later

- Mem0 or LLM extractor as candidate generator only

- Python service or monolith API for orchestration

- Simple review interface or SQL-based review queue

- Encrypted backups

- Periodic restore tests

Avoid in v1 unless there is a specific reason:

- Dedicated graph database

- Dedicated vector database

- Elasticsearch / OpenSearch

- SpiceDB / Zanzibar-style ReBAC

- Complex microservices

- Multi-agent autonomous write permissions

- Automatic personality modeling

- Fine-tuning from private chat history

Some of these may become useful in later phases, but they should not block the v1 foundation.

---

## 23. Working Style for Future AI Assistants

When helping with this project, the assistant should behave like a strict systems architect and safety reviewer.

The assistant should:

- Challenge unsafe simplifications.

- Preserve long-term maintainability.

- Distinguish v1 requirements from future enhancements.

- Avoid overengineering the first implementation.

- Treat privacy and provenance as mandatory, not optional.

- Prefer clear data contracts over vague AI behavior.

- Use explicit schema, lifecycle, and state-machine thinking.

- Ask whether a design can be rebuilt from raw events.

- Ask whether a sensitive memory can leak across actors.

- Ask whether a future embedding or schema migration can happen safely.

- Ask whether the system can be restored on new hardware.

- Avoid treating LLM output as authoritative truth.

The assistant should not:

- Suggest a one-table memory design.

- Suggest direct model writes to permanent memory.

- Suggest deleting long-term memory based only on age or access count.

- Treat vector search as sufficient.

- Ignore privacy boundaries.

- Confuse summaries with source-of-truth events.

- Turn temporary emotions into permanent identity.

- Treat third-party messages as user-confirmed facts.

- Recommend complex enterprise infrastructure before the v1 core exists.

---

## 24. One-Sentence Project Doctrine

Build the system as:

> An append-only, provenance-preserving personal event store with derived memory projections, curated versioned core blocks, policy-gated retrieval, and auditable context injection.

Not as:

> A single memories table plus automatic summaries plus vector Top-K.
