# Architecture

> 对应版本：Phase 1.4（retrieval bridge in progress）
> 日期：2026-05-09

本文档描述当前系统的实际架构，不写未来计划。演进路线见 `PHASE_1_PLAN.md` 和 `MEMORY_ITEMS_RETRIEVAL_BRIDGE.md`。

---

## 1. Current Architecture: Phase 1.1

当前系统由五层组成：

```
legacy kiwi-mem（FastAPI + PostgreSQL + pgvector）
  + event provenance layer（memory_events）
  + candidate shadow layer（memory_candidates）
  + versioned core_blocks（core_blocks）
  + access audit layer（memory_access_log）
  + actor privacy gate（Phase 1.1 — retrieval safety）
```

Server topology and Hermes dual-path模型保持不变（见 §4）。

### 1.1 System Philosophy & Temporal Memory Model

The system is guided by long-term design principles documented in:

- **`VISION.md`** — project vision, memory hierarchy (7 layers), forgetting philosophy, agent boundaries, UI philosophy.
- **`KNOWN_RISKS.md`** — catalog of inherent risks (identity ossification, retrieval leakage, authority drift, scope creep) with current mitigations and unresolved gaps.

Key architectural commitments:

| Principle | Architectural expression |
|-----------|--------------------------|
| Raw event ≠ memory | `memory_events` table (append-only) separate from `memories`/`memory_items` |
| Candidate ≠ truth | `memory_candidates` with `pending`/`pending_auto`/`requires_review` states |
| Forgetting is a feature | Heat decay, Dream consolidation, `valid_until` expiry |
| Retrieval safety > recall | SQL-layer actor privacy gate (Phase 1.1), sealed exclusion, exclude_privacy |
| Provenance is mandatory | `source_event_ids` chain from event → candidate → committed memory |
| Agent boundary | Actor privacy matrix; Hermes restricted; no auto-commit for `assistant_inferred` |
| Temporal model | 7-layer memory hierarchy; emotional states not permanently identity |

**服务拓扑：**

```
iPhone (Health Auto Export)     Telegram User        Hermes Agent (~/.hermes/)
    │                               │                 │
    ▼                               ▼                 │
Cloudflare Tunnel (agent.xeon.im)  Telegram Bot       │
    │                               │                 │
    ▼                               ▼                 │
kiwi-mem :8080  ←─────────────  claude.ai MCP         │
(FastAPI 记忆网关)                 (locapmemo, 6 tools)│
    │                                                  │
    ▼                          ┌───────────────────────┘
PostgreSQL :5432 + pgvector   │
    ▲                          │
    │                          │
    └──── health-db MCP ───────┘    ←──── hermes_mcp.py (/hermes/mcp, 5 tools)
     (direct SQL: health_summary          (REST: POST /events, /candidates, /events/access-log)
      + raw_health_data only)             (filtered memories, whitelisted core_blocks)
```

**Hermes Agent 双路径读取模型：**

Hermes 有两条**独立**的读取路径，不可混淆：

| 路径 | 通道 | 读取内容 | 机制 |
|------|------|---------|------|
| **Health** | `health-db` MCP (PostgreSQL 直连) | `health_summary`, `raw_health_data` | hermes_readonly 用户, SELECT only |
| **Memory** | `hermes_mcp.py` → kiwi-mem HTTP | 过滤后的 memories, whitelisted core_blocks | 5 restricted tools, 不直连 DB |

- Health 路径是**健康数据专用**通道，不走 kiwi-mem HTTP，不走记忆检索逻辑
- Memory 路径通过 hermes_mcp.py 受限接入：只能 observe events、提案 pending candidates、读过滤记忆、获取白名单 context
- Health access **不应被误认为** default memory context access
- Hermes **不能**写入 core_blocks、不能 auto-commit candidates、不能注入 `health_baseline` 或 `relationship_context`
- 未来 Phase 2.5 / health architecture 应新增 `hermes_get_health_context` 或显式 health intent，**不应**扩展 default Hermes context whitelist

---

## 2. Core Tables

### memory_events — Append-only Provenance Layer

所有写入操作先产生 event。不可覆盖，不可删除（仅可 redact）。

```sql
event_id         UUID PRIMARY KEY DEFAULT gen_random_uuid()
event_type       TEXT NOT NULL          -- manual_note / health_data / chat_message / chat_memory
source_type      TEXT NOT NULL          -- manual / health_pipeline / conversation / mcp_agent
source_id        TEXT
idempotency_key  TEXT
session_id       TEXT
actor            TEXT NOT NULL          -- user / health_pipeline / claude_mcp
source_trust     TEXT NOT NULL          -- user_direct / system_generated / assistant_inferred
privacy_level    TEXT NOT NULL DEFAULT 'personal'
content_text     TEXT
content_hash     TEXT
payload_json     JSONB
occurred_at      TIMESTAMPTZ
observed_at      TIMESTAMPTZ
ingested_at      TIMESTAMPTZ DEFAULT NOW()
```

- `UNIQUE INDEX ON (source_type, idempotency_key) WHERE idempotency_key IS NOT NULL`

### memory_candidates — Proposal / Shadow Layer

AI 或外部 agent 提取的记忆候选。**不是 truth**。

```sql
candidate_id     UUID PRIMARY KEY DEFAULT gen_random_uuid()
memory_type      TEXT                   -- 来自 CONTEXT.md 分类法或 'unknown'
subject_key      TEXT
predicate_key    TEXT
rendered_text    TEXT
canonical_value  JSONB
source_event_ids UUID[]
source_trust     TEXT
privacy_level    TEXT DEFAULT 'personal'
actor_scope      TEXT[] DEFAULT '{local_bot,claude_mcp}'
confidence       FLOAT
importance       INT
status           TEXT DEFAULT 'pending' -- pending / pending_auto / committed / superseded / rejected
extractor_name   TEXT
extractor_version TEXT
created_at       TIMESTAMPTZ DEFAULT NOW()
```

**当前规则：** `assistant_inferred` → `status='pending'`，不自动提交。Resolver 留给 Phase 1。

### memories — Legacy Committed Memory Layer

当前仍是主 committed memory 层。Phase 1 将逐步迁移到 `memory_items` (UUID)。

```sql
id               SERIAL PRIMARY KEY
content          TEXT
title            TEXT
importance       INT
source           TEXT
source_session   TEXT
memory_type      TEXT DEFAULT 'legacy'  -- Phase 0.5 新增
status           TEXT                    -- Phase 0.5 新增
privacy_level    TEXT                    -- Phase 0.5 新增
actor_scope      TEXT[]                  -- Phase 0.5 新增
source_trust     TEXT                    -- Phase 0.5 新增
source_event_ids INT[]                   -- Phase 0.5 新增，指向 memory_events
subject_key      TEXT                    -- Phase 0.5 新增
predicate_key    TEXT                    -- Phase 0.5 新增
confidence       FLOAT                   -- Phase 0.5 新增
embedding        VECTOR(1024)
-- ... 其他原有字段
```

### core_blocks — Versioned Curated Core Memory

```sql
block_key        TEXT NOT NULL          -- response_policy / active_projects / identity / ...
version_no       INT NOT NULL
content_text     TEXT
char_limit       INT
privacy_level    TEXT
actor_scope      TEXT[]
update_policy    TEXT
approval_status  TEXT DEFAULT 'approved'
proposed_by      TEXT
approved_by      TEXT
source_memory_ids INT[]
effective_from   TIMESTAMPTZ
superseded_at    TIMESTAMPTZ
created_at       TIMESTAMPTZ DEFAULT NOW()
UNIQUE (block_key, version_no)
```

- Active block 查询：`WHERE superseded_at IS NULL AND approval_status = 'approved' ORDER BY version_no DESC LIMIT 1`
- 更新创建新版本，旧版本 `superseded_at=NOW()`

### memory_access_log — Read / Context Injection Audit Log

```sql
access_id         UUID PRIMARY KEY DEFAULT gen_random_uuid()
actor             TEXT NOT NULL          -- api_client / telegram_bot
retrieval_mode    TEXT                   -- chat_completions / telegram_bot
intent            TEXT                   -- chat
query_text        TEXT
legacy_memory_ids INT[]                  -- 当前 memories.id（Phase 0.5 主要使用）
memory_ids        UUID[]                 -- 未来 memory_items.memory_id
core_block_keys   TEXT[]                 -- 实际注入的 core block keys
session_id        TEXT
accessed_at       TIMESTAMPTZ DEFAULT NOW()
```

---

## 3. Write Architecture

所有写入遵循统一管道：

```
source adapter
  → append_event()         ← 不可跳过
  → provenance 字段赋值    ← source_trust / privacy_level / actor / source_event_ids
  → 写入 memories 或 memory_candidates
```

**四条写入路径的 provenance 赋值：**

| 入口 | source_type | source_trust | actor | privacy_level |
|------|-------------|-------------|-------|---------------|
| `/debug/memories` (manual) | `manual` | `user_direct` | `user` | `personal` |
| `/data/health` | `health_pipeline` | `system_generated` | `health_pipeline` | `sensitive` |
| AI extraction | `conversation` | `assistant_inferred` | `user` | `personal` |
| MCP save_memory | `mcp_agent` | `assistant_inferred` | `claude_mcp` | `personal` |

**MCP 不直接写 core_blocks，不走数据库直写。**

---

## 4. Read Architecture

```
chat request
  → 加载 approved core_blocks
      → 白名单过滤: response_policy + active_projects
      → 排除: test.block / health_baseline / relationship_context
  → 检索 legacy memories
      → heat decay 热记忆（全文注入）
      → vector semantic search（相关记忆）
  → 组装 system prompt
      → [Core memory: response_policy]
      → [Core memory: active_projects]（如存在）
      → 相关记忆
      → 最近动态
      → SYSTEM_PROMPT_RULES
  → LLM 生成回复
  → log_memory_access() fire-and-forget
  → 返回响应
```

**Bot 侧特殊处理：**
- `get_persona()` 优先读 `GET /core-blocks/response_policy`（5 分钟缓存），fallback 旧 memories 查询
- `active_projects` 单独请求 `GET /core-blocks/active_projects`
- 请求 kiwi-mem `/v1/chat/completions` 时传 `skip_core_blocks=True` 避免重复注入

**Hermes Agent 双路径读取（独立于主 chat path）：**

```
Hermes turn
  ├── Health Path (独立，不经过 kiwi-mem HTTP)
  │     → health-db MCP (PostgreSQL 直连, hermes_readonly)
  │     → SELECT FROM health_summary, raw_health_data
  │     → 专用于健康数据查询，不走记忆检索
  │
  └── Memory Path (受限，经过 kiwi-mem HTTP)
        → hermes_mcp.py (/hermes/mcp)
        → 5 restricted tools:
            hermes_get_context
              → 加载 core_blocks → 白名单过滤: response_policy + active_projects
              → 排除: test.block / health_baseline / relationship_context
              → 搜索 memory (actor gate = hermes_agent: public_like+personal)
              → 获取 recent (actor gate = hermes_agent: public_like+personal)
              → EXCLUDE_PRIVACY=sealed,restricted 为二级 blocklist
            hermes_search / hermes_get_recent
              → 读 memories, actor gate = hermes_agent
              → 只允许 public_like + personal（SQL 层 actor privacy gate）
              → sensitive/restricted/sealed 全部被拦截
            hermes_observe / hermes_propose_memory
              → POST /events (append-only event)
              → POST /candidates (status=pending, source_trust=assistant_inferred)
              → 不直接写 memories / core_blocks
```

**关键约束：**
- Health path 和 Memory path 是**两条独立通道**，不可互相替代
- Hermes 不能通过 health-db MCP 读取 `memories`、`core_blocks` 等表（已被 REVOKE）
- Hermes 不能通过 hermes_mcp.py 读取 `health_summary`、`raw_health_data`（不在此路径的 API 范围内）
- `health_baseline` 和 `relationship_context` 不在 Hermes default context whitelist 中，不注入
- Hermes Memory path 的 `sensitive` 排除完全依赖 actor privacy gate（`hermes_agent` policy = `public_like`+`personal`）；`EXCLUDE_PRIVACY="sealed,restricted"` 是二级 blocklist，不涵盖 `sensitive`
- 未来如需健康上下文注入 Memory path，应新增 `hermes_get_health_context` 或显式 health intent，**不应**扩展 default Hermes context whitelist

### 4.1 Phase 1.1 — Actor Privacy Gate (Retrieval Safety)

所有 legacy memories 检索路径在 SQL 层按 actor 过滤 `privacy_level`。

**Actor Privacy Matrix：**

| actor | public_like | personal | sensitive | restricted | sealed |
|-------|:---:|:---:|:---:|:---:|:---:|
| local_bot | ✅ | ✅ | ✅ | ✅ | ❌ |
| api_client | ✅ | ✅ | ✅ | ✅ | ❌ |
| telegram_bot | ✅ | ✅ | ✅ | ❌ | ❌ |
| claude_mcp | ✅ | ✅ | ✅ | ❌ | ❌ |
| hermes_agent | ✅ | ✅ | ❌ | ❌ | ❌ |
| dev_agent | ✅ | ✅ | ❌ | ❌ | ❌ |
| unknown / default | ✅ | ✅ | ❌ | ❌ | ❌ |

- `sealed` 永远不通过普通 retrieval 自动返回
- `api_client` 当前映射 trusted local/private endpoint；未来 external clients 需显式 `X-Actor` 或降权
- 实现：`get_allowed_privacy_levels(actor)` → `_PRIVACY_POLICY` dict（`database.py`）
- Phase 1.1 不做 `actor_scope` 数组过滤

**SQL-layer Gate：**

```
effective_visible = get_allowed_privacy_levels(actor) − exclude_privacy
```

所有 retrieval 路径使用 bind-parameter 安全写法：

```sql
COALESCE(m.privacy_level, 'personal') = ANY($N::text[])
-- + optional:
AND COALESCE(m.privacy_level, 'personal') != ALL($M::text[])
```

- `exclude_privacy` 是减法 blocklist，`actor` gate 是 allowlist；两者取交集
- 旧数据 `privacy_level = NULL` → `COALESCE(…, 'personal')`
- 不做 Python post-filter（SQL 层过滤）

**Covered retrieval paths：**

| 函数 | 所在文件 | actor 参数 |
|------|---------|-----------|
| `search_memories()` | database.py | `actor="local_bot"` (default) |
| `_vector_search()` | database.py | via `allowed_privacy` list |
| `_keyword_search()` | database.py | via `allowed_privacy` list |
| `get_recent_memories()` | database.py | `actor="local_bot"` (default) |
| `/debug/memories` (含 title path) | main.py | `actor=` query param, 默认 `"local_bot"` |

**Actor 调用方传参：**

| 入口 | actor | 路径 |
|------|-------|------|
| `/v1/chat/completions` | `"api_client"` | `build_system_prompt_with_memories()` |
| Telegram bot (`search_memory`, `get_persona`) | `"telegram_bot"` | `GET /debug/memories` |
| Hermes MCP (`hermes_search`, `hermes_get_recent`, `hermes_get_context`) | `"hermes_agent"` | `GET /debug/memories` |
| AI extraction / dedup (内部) | `"local_bot"` (默认) | 直调 DB helpers |

**Hermes 双路径中的 Privacy Gate：**

- **Health path**（health-db MCP, hermes_readonly）：不变，仍只读 `health_summary` + `raw_health_data`
- **Memory path**（hermes_mcp.py）：`hermes_agent` actor gate → 只允许 `public_like` + `personal`
  - `sensitive` / `restricted` / `sealed` 三者均被 actor gate 拦截
  - `sensitive` 的排除**完全依赖 actor gate**（不是 `EXCLUDE_PRIVACY`）
  - `EXCLUDE_PRIVACY="sealed,restricted"` 保留为二级防线（与 actor gate 取交集）
  - 未来如需健康上下文注入 Memory path，应新增 `hermes_get_health_context` 或显式 health intent

**Verification：**

| 脚本 | 覆盖 | 结果 |
|------|------|------|
| `scripts/test_privacy_policy.py` | helper unit tests | 19/19 PASS |
| `scripts/test_privacy_gate_retrieval.py` | end-to-end retrieval gate | 109/109 PASS |

---

## 5. Core Block Policy

| block_key | 当前状态 | 注入策略 | 来源 |
|-----------|---------|---------|------|
| `response_policy` | active (v1, approved) | 所有 chat 注入 | \_\_BOT_PERSONA\_\_ 迁移而来 |
| `active_projects` | active (v1, approved) | 存在则注入 | 手动创建 |
| `test.block` | 不得 active approved | 不注入 | — |
| `health_baseline` | 未创建 | 不注入 | — |
| `relationship_context` | 未创建 | 不注入 | — |

- Core block 更新创建新版本，旧版本 `superseded`
- `GET /core-blocks` 和 `GET /core-blocks/{key}` 只返回 `approved + not superseded`
- API 受 `AdminAuthMiddleware` 保护
- 白名单为 hard-coded（`["response_policy", "active_projects"]`），非查表

---

## 6. Agent Entry Rules

| Agent | 写入方式 | 可写 memories | 可写 candidates | 可写 core_blocks | 读取路径 | actor gate (retrieval) |
|-------|---------|:---:|:---:|:---:|---------|------------------------|
| Manual user (via API) | `POST /debug/memories` | ✅ user_direct | — | — | HTTP API | local_bot (admin) |
| Health pipeline | `POST /data/health` | ✅ system_generated | — | — | HTTP API | local_bot (internal) |
| AI extraction (internal) | background task | ✅ assistant_inferred | ✅ pending | — | 直连 DB | local_bot (internal) |
| MCP (Claude.ai) | `POST /debug/memories` via mcp_server | ✅ assistant_inferred | — | ❌ | memory MCP | claude_mcp |
| Telegram Bot | `POST /debug/memories` via HTTP | ✅ user_direct | — | ❌ | HTTP API | telegram_bot |
| **Hermes Agent** | `POST /events` + `POST /candidates` via hermes_mcp | ❌ | ✅ pending only | ❌ | **双路径**: health-db MCP (健康) + hermes_mcp (记忆) | hermes_agent |

**当前规则：**
- 外部 agent 写入均为 `assistant_inferred`，应保持 candidate-only（Phase 1 resolver 接管前暂时落到 memories 表，带 provenance）
- 外部 agent 不允许直接写 `core_blocks`
- 不允许 MCP 或外部 agent 调用数据库直写
- **Actor privacy gate（Phase 1.1）**：所有 legacy memories retrieval 在 SQL 层按 actor 过滤 privacy_level（见 §4.1 actor privacy matrix）
- **Hermes Agent 特殊规则：**
  - 不直接写 memories，只通过 `POST /events` (append-only) + `POST /candidates` (pending)
  - health-db MCP 专用于健康数据读取，与 memory context 完全分离
  - 不注入 `health_baseline` 或 `relationship_context` 到 default context
  - Memory path 受 hermes_agent actor gate → 只读 public_like + personal

---

## 7. Future Architecture Direction

Phase 1.0 已交付（**当前已实现**）：

```
memory_items (UUID, new committed layer)          ← Phase 1.0 M1
candidate resolver (pending → committed / rejected) ← Phase 1.0 M2
review queue API                                   ← Phase 1.0 M3
```

Phase 1.1 已交付（**当前已实现**）：

```
actor privacy gate (SQL-layer, privacy_level filter) ← Phase 1.1 M1/M2
retrieval gate automated test script                  ← Phase 1.1 M3
```

Phase 1.2 已交付（**当前已实现**）：

```
retrieval cleanup: hardcoded path removal, dead code, actor audit ← Phase 1.2 M1/M2/M3
local_bot full positive matrix coverage (109 → 124 checks)
all internal read paths explicit actor="local_bot"
```

Phase 1.3 已交付（**当前已实现**）：

```
evals/retrieval_safety_minimal.jsonl — 10 query-template eval cases
scripts/eval_retrieval_minimal.py — stdlib-only eval runner
10/10 query-based safety eval (minimal, not full recall/precision benchmark)
```

Phase 1.4 — in progress（**当前已实现 M1/M2a**）：

```
docs/MEMORY_ITEMS_RETRIEVAL_BRIDGE.md — design doc, Strategy B selected
search_memory_items() / get_recent_memory_items() — shadow helpers (zero callers)
M2b shadow comparison script — pending
```

**Phase 1.5+ 推荐方向（未实现）：**

- `actor_scope` 数组过滤（当前 Phase 1.1 只做 privacy_level）
- memory_type cleanup（减少 legacy 比例）
- basic policy rules 抽离
- privacy-gated retrieval for `memory_items` primary retrieval（Phase 1.4 shadow → future promotion）
- eval expansion: recall@10 / precision@10 benchmark (Phase 1.3 covers minimal safety only)
- **memory decay / temporal cooling**: automated heat decay with configurable cooling curves
- **emotional compression**: transient emotional cache → summarized trend pipeline (VISION.md layer 2→3)
- **reflection layer**: periodic agent-led review for contradiction, staleness, and consolidation
- **temporal summarization**: time-windowed compression of low-signal fragments

**未来可选增强（不在 Phase 1 必须范围内）：**
- FTS + vector + structured hybrid search with RRF reranker
- Graph-based conflict resolution (AGM)
- SpiceDB / ReBAC for fine-grained access control
- WeChat historical import pipeline
- Embedding shadow migration
- Training export with de-identification
- Memory consolidation (Dream v2): structured fragment fusion with provenance linking
- Event graph: navigable causal graph linking events, candidates, and committed memories
