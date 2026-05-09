# Memory Items Retrieval Bridge — Design Document

> Phase 1.4-M1 | 2026-05-09 | Planning only

## 1. Current State

### 1.1 Two Committed Memory Tables

| | `memories` (legacy) | `memory_items` (Phase 1.0) |
|---|---|---|
| PK | `id` SERIAL (integer) | `memory_id` UUID |
| Content column | `content` TEXT | `rendered_text` TEXT |
| Embedding | `embedding` VECTOR(1024) | None (deferred to future table) |
| Privacy | `privacy_level` TEXT | `privacy_level` TEXT |
| Actor scope | `actor_scope` TEXT[] | `actor_scope` TEXT[] |
| Provenance | `source` TEXT, `source_trust` TEXT | `source_trust` TEXT, `source_event_ids` UUID[], `source_candidate_id` UUID |
| Lifecycle | `status` TEXT, `valid_until` | `status` TEXT, `valid_from`/`valid_to` TIMESTAMPTZ, `supersedes_memory_id` UUID (self-FK) |
| Created | Phase 0 (original kiwi-mem) | Phase 1.0 M1 |
| **Read by retrieval?** | **YES — primary source** | **NO — write-only** |

### 1.2 Current Dual-Write

`resolve_candidate()` (Phase 1.0 M2) writes to both tables in a single transaction:

```
candidate → memory_items (UUID, new layer) + memories (integer, compat)
```

Pre-Phase-1.0 data exists only in `memories`. Post-Phase-1.0 committed memories exist in both tables. No data has been migrated from `memories` → `memory_items`.

### 1.3 Retrieval Status

Every retrieval path reads `memories` exclusively. `memory_items` has zero retrieval callers. Phase 1.1 privacy gate (SQL-layer actor filter) is applied only to `memories` queries.

---

## 2. Legacy Retrieval Path Inventory

### 2.1 Direct Database Functions

| Path | Function | File:line | Mechanism |
|------|----------|-----------|-----------|
| Vector search | `_vector_search()` | database.py:1397 | `memories` SQL with `ORDER BY embedding <=>` |
| Keyword search | `_keyword_search()` | database.py:1849 | `memories` SQL with `ILIKE` + jieba |
| RRF merge | `search_memories()` | database.py:1280 | Merges vector + keyword, calls both above |
| Recent | `get_recent_memories()` | database.py:1960 | `memories` SQL `ORDER BY created_at DESC` |
| Title exact match | inline SQL in `/debug/memories` | main.py:2244 | `SELECT * FROM memories WHERE title = $1` |
| Internal dedup | `check_memory_duplicate()` | database.py:2070 | Calls `search_memories(actor="local_bot")` |

### 2.2 Entry Points and Their Actors

| Entry point | Retrieval call | actor |
|-------------|---------------|-------|
| `/v1/chat/completions` | `search_memories()` | `api_client` |
| `/debug/memories?q=` | `search_memories()` | query param, default `local_bot` |
| `/debug/memories` (no q) | `get_recent_memories()` | query param, default `local_bot` |
| `/debug/memories?title=` | inline SQL | query param, default `local_bot` |
| Hermes `hermes_search` | HTTP → `/debug/memories?q=` | `hermes_agent` |
| Hermes `hermes_get_recent` | HTTP → `/debug/memories` | `hermes_agent` |
| Hermes `hermes_get_context` | HTTP → `/debug/memories` ×2 | `hermes_agent` |
| Telegram bot `search_memory` | HTTP → `/debug/memories?q=` | `telegram_bot` |
| Telegram bot `get_persona` | HTTP → `/debug/memories?title=` | `telegram_bot` |
| AI extraction ×2 paths | `search_memories()` + `get_recent_memories()` | `local_bot` |
| API extract now | `search_memories()` + `get_recent_memories()` | `local_bot` |

All 12 call sites read `memories`. None read `memory_items`.

---

## 3. memory_items Schema and Helper Inventory

### 3.1 Table Schema (22 columns)

```
memory_items (
    memory_id            UUID PK DEFAULT gen_random_uuid()
    memory_type          TEXT NOT NULL DEFAULT 'unknown'
    subject_key          TEXT DEFAULT ''
    predicate_key        TEXT DEFAULT ''
    rendered_text        TEXT NOT NULL DEFAULT ''         ← content equivalent
    canonical_value      JSONB DEFAULT '{}'
    source_event_ids     UUID[] DEFAULT '{}'
    source_candidate_id  UUID FK → memory_candidates
    source_trust         TEXT NOT NULL DEFAULT 'unknown'
    privacy_level        TEXT NOT NULL DEFAULT 'personal'
    actor_scope          TEXT[] DEFAULT '{local_bot,claude_mcp}'
    confidence           REAL DEFAULT 0.7
    importance           INTEGER DEFAULT 5
    heat                 REAL DEFAULT 1.0
    status               TEXT NOT NULL DEFAULT 'active'
    supersedes_memory_id UUID FK → memory_items (self)
    valid_from           TIMESTAMPTZ
    valid_to             TIMESTAMPTZ
    access_count         INTEGER DEFAULT 0
    last_accessed_at     TIMESTAMPTZ
    created_at           TIMESTAMPTZ DEFAULT NOW()
    updated_at           TIMESTAMPTZ DEFAULT NOW()
)
```

No `embedding` column. No text search index. No FTS vector.

### 3.2 Existing Helpers

| Helper | Phase | Purpose |
|--------|-------|---------|
| `insert_memory_item()` | 1.0 M1 | Write single committed memory → returns UUID |
| `get_memory_item(memory_id)` | 1.0 M1 | Lookup by UUID, returns dict or None |
| `resolve_candidate()` | 1.0 M2 | Auto-commit/review flow → dual-writes both tables |

No retrieval helpers exist: `search_memory_items` and `get_recent_memory_items` are not implemented.

### 3.3 Schema Gaps vs memories

| Feature | `memories` | `memory_items` | Impact |
|---------|-----------|-----------------|--------|
| Vector embedding | `embedding` VECTOR(1024) | None | No semantic/vector search |
| FTS / text index | None (ILIKE only) | None | Both keyword-only without embedding |
| `title` column | Yes | No (`subject_key` is not title) | Title exact-match path cannot be replicated |
| `source` | TEXT field | Normalized via `source_trust` + `source_candidate_id` | Different provenance semantics |

---

## 4. Strategy Comparison

### Strategy A — Legacy-only Continues

- `memory_items` is never read by retrieval
- No code changes
- `memory_items` serves only as lifecycle/provenance target

**Pros**: Zero risk, zero effort.
**Cons**: `memory_items` is dead storage for retrieval purposes. Dual-write cost with no read benefit. Schema divergence increases over time.

### Strategy B — Dual-read Shadow Mode (RECOMMENDED)

- `memories` remains primary retrieval source
- New `search_memory_items()` / `get_recent_memory_items()` implemented as shadow/eval path
- Shadow comparison script verifies privacy behavior matches between the two sources
- `memory_items` results are NOT injected into chat/Hermes/Telegram
- After observation period (Phase 1.5+), can be promoted to primary

**Pros**: Validates `memory_items` read path without production risk. Surfaces dual-write gaps. Incremental.
**Cons**: Two retrieval code paths to maintain. Dual-write divergence may cause confusing shadow results.

### Strategy C — memory_items Primary with Legacy Fallback

- `memory_items` becomes primary retrieval source
- `memories` is fallback for pre-Phase-1.0 data
- Full migration or dual-query union needed

**Pros**: Single source of truth for post-Phase-1.0 data.
**Cons**: High-risk migration. Dual-query complexity during transition. Embedding gap (no vector search on memory_items). Requires Phase 1.1 privacy gate replication. Rollback is difficult. **Not suitable for Phase 1.4.**

### Recommendation

**Strategy B for Phase 1.4.** No production retrieval switch. memory_items read path is eval-only. Shadow comparison detects divergence before any promotion decision.

---

## 5. Safety Invariants

All `memory_items` retrieval paths must enforce the same invariants as legacy:

| # | Invariant | Mechanism |
|---|-----------|-----------|
| 1 | SQL-layer actor privacy gate | `COALESCE(privacy_level, 'personal') = ANY($N::text[])` |
| 2 | sealed never auto-returned | `get_allowed_privacy_levels()` never includes `sealed` |
| 3 | Actor matrix unchanged | Same `_PRIVACY_POLICY` dict (Phase 1.1) |
| 4 | `exclude_privacy` is subtractive | `AND COALESCE(privacy_level, 'personal') != ALL($M::text[])` |
| 5 | Hermes: no sensitive/restricted/sealed | `hermes_agent` gate = `[public_like, personal]` |
| 6 | Telegram: no restricted/sealed | `telegram_bot` gate = `[public_like, personal, sensitive]` |
| 7 | `NULL` privacy_level → personal | `COALESCE(privacy_level, 'personal')` |
| 8 | No Python post-filter | All filtering in SQL WHERE clause |
| 9 | Bind parameters only | No string concatenation of privacy levels |

---

## 6. Planned Helper Signatures (Implementation Deferred to M2)

### `search_memory_items()`

```python
async def search_memory_items(
    query: str,
    limit: int = 10,
    *,
    actor: str = "local_bot",
    exclude_privacy: Optional[set[str]] = None,
) -> list[dict]:
```

- **Query type**: keyword-only (no vector — `memory_items` has no `embedding`)
- **Keywords**: reuse `extract_search_keywords()` from legacy path
- **SQL WHERE**: `status = 'active'` + `(valid_to IS NULL OR valid_to > NOW())` + privacy gate
- **ORDER BY**: `importance DESC, created_at DESC` (no RRF — single-source)
- **Returns**: list of dicts with `memory_id`, `rendered_text` (mapped to `content` key), `privacy_level`, `importance`, `memory_type`, `created_at`, `subject_key`

### `get_recent_memory_items()`

```python
async def get_recent_memory_items(
    limit: int = 20,
    *,
    actor: str = "local_bot",
    exclude_privacy: Optional[set[str]] = None,
) -> list[dict]:
```

- **SQL WHERE**: same as `search_memory_items` minus keyword match
- **ORDER BY**: `created_at DESC`
- **Returns**: same dict shape as `search_memory_items`

### Title equivalent

Not planned for Phase 1.4. `/debug/memories?title=` remains on `memories`. `memory_items` has no `title` column.

---

## 7. Shadow Comparison Plan

### Purpose

Verify that `search_memory_items()` and `get_recent_memory_items()` produce privacy-equivalent results to `search_memories()` and `get_recent_memories()` for the **same committed data**.

### Script: `scripts/eval_memory_items_shadow.py` (M2)

- Creates test data via `resolve_candidate()` → dual-writes to both tables
- Queries both tables with identical query/actor/exclude params
- Compares: visible privacy_levels, sealed exclusion, count overlap
- JSON summary: `legacy_visible`, `items_visible`, `match`, `discrepancies`
- try/finally cleanup of both `memory_items` and `memories` test data

### What shadow comparison does NOT do

- Does NOT inject `memory_items` results into chat/Hermes/Telegram
- Does NOT replace legacy retrieval
- Does NOT migrate data
- Does NOT measure recall/precision against a ground truth (eval harness handles that)

---

## 8. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Pre-Phase-1.0 data missing from `memory_items` | Medium | Shadow comparison logs "only in legacy" counts; does not fail on them |
| Dual-write divergence (race between resolver commit and shadow read) | Low | Shadow script creates fresh data and reads immediately |
| `status` semantics differ between tables | Low | Both use `active`/`superseded`; shadow filters `status='active'` |
| No embedding → keyword search quality differs from legacy RRF | Medium | Acceptable for eval phase; keyword-only is explicit design choice |
| Cleanup complexity (candidates + events from `resolve_candidate`) | Low | Shadow script cleans `memory_items` + `memories`; candidates/events are append-only and benign |

---

## 9. Explicit Non-goals

- memory_items-only retrieval switch
- embedding table or vector search on memory_items
- hybrid search / RRF on memory_items
- reranker
- data migration from `memories` to `memory_items`
- Web UI for memory_items
- WeChat import
- Health data rewrite
- Hermes permission expansion
- Immich / people graph integration
- `/debug/memories?title=` migration to memory_items
- Changes to `resolve_candidate()` dual-write behavior
