# Phase 1.5-M3 Plan — Observation-Only / TTL Support

> Status: Planning. Do not implement until reviewed.
> Dependencies: Phase 1.5-M2 dry-run classifier (completed).
> M2 classifier is a rule-based baseline; AI-assisted triage is deferred to pre-M3b/M4 eval.

---

## 1. Goal

Enable `short_term_auto_write` candidates to enter an `observation_only` state instead of `pending`/`requires_review`. Observation-only candidates are time-limited (7-14 day TTL), excluded from the default manual review queue, and serve as source material for future digest aggregation (M5).

**Scope**: Reduce review queue burden for ordinary feelings, thoughts, and transient emotional expressions. Do not change behavior for high-risk or medium-factual candidates.

---

## 2. Current Schema

`memory_candidates` has no `valid_to` column. Resolver at line 4358 notes: "candidates don't carry valid_from yet."

Existing fields sufficient for M3: `status`, `stability`, `created_at`, `memory_type`, `source_trust`, `privacy_level`, `importance`, `source_event_ids`, `confidence`.

Existing indexes: `idx_candidates_status(status, created_at DESC)`, `idx_candidates_type_status(memory_type, status)`.

---

## 3. Storage Decision

**Reuse `memory_candidates`.** Do not create a new `short_term_observations` table. Do not reuse `memory_events`.

Rationale:
- `memory_candidates` already has the fields needed for observation lifecycle (status, created_at, source_event_ids, memory_type, rendered_text).
- A new table duplicates candidate structure with no architectural benefit at this stage.
- `memory_events` is append-only raw provenance, not a reviewable proposal layer.
- One nullable column (`valid_to`) is a minimal, low-risk addition.

---

## 4. M3a — Schema + Admin Queue Filter (No Resolver Change)

### 4.1 Schema change

```sql
ALTER TABLE memory_candidates
ADD COLUMN IF NOT EXISTS valid_to TIMESTAMPTZ;
```

- Nullable. NULL = no TTL (existing behavior for all non-observation candidates).
- No backfill required for existing rows.
- No default value — `valid_to` is only set for `observation_only` candidates.

### 4.2 New status values

| Status | Meaning | Terminal? |
|--------|---------|-----------|
| `observation_only` | Short-term observation, TTL-gated, excluded from default review queue | No — can transition to `expired`, `rejected`, or `digested` (M5) |
| `expired` | Observation TTL elapsed | Yes — idempotent on re-expiry |

Existing statuses (`pending`, `pending_auto`, `requires_review`, `committed`, `rejected`) are unchanged.

### 4.3 Admin queue default filter

Change default admin list query from `status = 'pending'` to:

```sql
WHERE status IN ('pending', 'requires_review')
```

Observation-only and expired candidates are excluded from the default view but remain queryable via `?status=observation_only` or `?status=expired`.

### 4.4 Admin commit protection

`POST /admin/candidates/{id}/commit` must reject `observation_only` candidates. `resolve_candidate()` already has a status guard that rejects non-`pending`/`pending_auto`/`requires_review` statuses. `observation_only` does not match any auto-commit path, so the existing guard suffices. Add an explicit check for clarity.

### 4.5 `list_candidates()` update

Extend to support comma-separated multi-status filter:

```python
async def list_candidates(*, status: str = "pending,requires_review", ...):
    statuses = [s.strip() for s in status.split(",")]
    where = [f"status = ANY($1::text[])"]
    params = [statuses]
```

Backward-compatible: callers passing a single status string still work.

### 4.6 M3a non-goals

- No resolver integration.
- No candidate write behavior change.
- No observation retrieval.
- No expiry job.
- No `valid_to` populated for any existing or new candidates (schema-only).

---

## 5. M3b — Resolver: short_term_auto_write → observation_only

### 5.1 Integration point

In `resolve_candidate()`, before the existing guard checks, call `classify_candidate_review_policy(candidate)`. If the result is `short_term_auto_write`:

1. Set `status = 'observation_only'`.
2. Set `valid_to = NOW() + INTERVAL '{ttl_days} days'` where `ttl_days` comes from classifier's `suggested_ttl_days` (7 or 14).
3. Return immediately — do NOT call `insert_memory_item` or `save_memory`.
4. Write a `memory_access_log` or debug log entry recording the classification.

If the classifier returns any other action (`medium_factual_auto_commit`, `manual_review`, `auto_reject_or_expire`, `keep_pending`): **fall through to existing behavior unchanged.**

### 5.2 Only short_term_auto_write is enabled

| Classifier output | M3b behavior |
|------------------|-------------|
| `short_term_auto_write` | **Enabled**: set `observation_only` + TTL |
| `medium_factual_auto_commit` | **Not enabled**: fall through to existing resolver (→ `keep_pending` or `pending_auto`) |
| `manual_review` | **Not changed**: existing resolver handles |
| `auto_reject_or_expire` | **Not enabled**: fall through (→ `keep_pending`) |
| `keep_pending` | **Not changed**: existing resolver handles |

### 5.3 Safety guarantees

- Gate 1-4 (high-stakes, diagnosis, negative inference, third-party) fire before Gate 5. No high-risk candidate can reach `short_term_auto_write`.
- `observation_only` never writes to `memory_items` or legacy `memories`.
- `observation_only` never enters retrieval (see §7).
- Misclassification impact is bounded: false positive expires in 7-14 days; false negative is re-extracted later.

### 5.4 M3b non-goals

- No `medium_factual_auto_commit` enabled.
- No `auto_reject_or_expire` enabled.
- No AI triage integration.
- No observation retrieval.
- No digest job.

---

## 6. M3c — Expiry Script

### 6.1 `scripts/expire_observations.py`

Stdlib-only script. Run manually or via cron (weekly recommended).

```sql
UPDATE memory_candidates
SET status = 'expired',
    reviewed_at = NOW(),
    reviewed_by = 'expiry_job'
WHERE status = 'observation_only'
  AND valid_to IS NOT NULL
  AND valid_to < NOW()
```

### 6.2 Provenance

Expired observations are status-changed, not deleted. Source events remain in `memory_events` (append-only). The provenance chain is preserved.

### 6.3 M3c non-goals

- No automatic digest generation.
- No background job scheduler integration (cron is manual setup).
- No deletion of expired observations.

---

## 7. Retrieval Behavior

**Observation-only candidates are not retrievable through any production path.**

| Path | Observation visible? |
|------|---------------------|
| `/v1/chat/completions` (`search_memories`) | No — queries `memories` table |
| Hermes context (`hermes_get_context`) | No — queries `memories` via `/debug/memories` |
| Telegram context (`search_memory`) | No — queries `memories` via `/debug/memories` |
| `/debug/memories` | No — queries `memories` table |
| Admin review queue (default) | No — excluded from default filter |
| Admin review queue (explicit) | Yes — `?status=observation_only` |
| Future digest job (M5) | Yes — queries `memory_candidates` directly |

---

## 8. Admin Queue Summary

| Status | Default list? | Explicit query? | Commit allowed? | Reject allowed? |
|--------|:---:|:---:|:---:|:---:|
| `pending` | Yes | Yes | Yes | Yes |
| `requires_review` | Yes | Yes | Yes | Yes |
| `observation_only` | No | Yes | No | Yes |
| `expired` | No | Yes | No | Idempotent |
| `committed` | No | Yes | No (409) | No (409) |
| `rejected` | No | Yes | No (409) | Idempotent |

---

## 9. Implementation Order

| Sub-phase | Content | Files | Risk |
|-----------|---------|-------|------|
| **M3a** | `valid_to` column, status values, multi-status filter, admin default filter, commit protection | `kiwi-mem/database.py`, `kiwi-mem/main.py` | Low |
| **M3b** | Resolver: `short_term_auto_write` → `observation_only` + TTL; all other channels unchanged | `kiwi-mem/database.py` | Low-Medium |
| **M3c** | `scripts/expire_observations.py` | `scripts/expire_observations.py` | Low |

M3a → M3b → M3c. Each is independently testable.

---

## 10. Test Plan

`scripts/test_observation_only.py` (delivered with M3b):

| # | Test | Expected |
|---|------|----------|
| 1 | emotional_observation → `short_term_auto_write` | Classifier returns correct action |
| 2 | Resolver sets `status='observation_only'` | Candidate status = observation_only |
| 3 | TTL 7d for importance < 4 | `valid_to ≈ NOW() + 7d` |
| 4 | TTL 14d for importance >= 4 | `valid_to ≈ NOW() + 14d` |
| 5 | No write to `memory_items` | Zero rows |
| 6 | No write to `memories` | Zero rows |
| 7 | Excluded from default admin list | Not in `?status=pending,requires_review` results |
| 8 | Visible with explicit filter | Present in `?status=observation_only` |
| 9 | `identity_fact` still → `requires_review` | Gate 1 not bypassed |
| 10 | `relationship_context` still → `requires_review` | Gate 1 not bypassed |
| 11 | `grade_fact` unchanged (dry-run only) | Classifier output logged, not acted on |
| 12 | Commit endpoint rejects `observation_only` | HTTP 409 |
| 13 | Expiry script sets `status='expired'` | Expired candidates filtered from queue |
| 14 | Cleanup: 0 test residue | All test candidates deleted |

---

## 11. Explicit Non-Goals (M3)

- No `short_term_observations` new table
- No `medium_factual_auto_commit` enabled
- No `auto_reject_or_expire` enabled
- No digest job (`batch_review` / `digest_pending` deferred to M5)
- No observation retrieval in any production context
- No `memory_items` or legacy `memories` write for observation_only
- No AI-assisted triage
- No health mood inference
- No Web UI
- No Hermes/Telegram behavior changes
- No provider routing
- No background job scheduler (cron is manual setup)
