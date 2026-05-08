# Architecture

> 对应版本：Phase 0.5（completed / sealed）
> 日期：2026-05-08

本文档描述当前系统的实际架构，不写未来计划。Phase 1+ 的演进方向见 `PHASE_0_5_SUMMARY.md` 第 7 节。

---

## 1. Current Architecture: Phase 0.5

当前系统由四层组成：

```
legacy kiwi-mem（FastAPI + PostgreSQL + pgvector）
  + event provenance layer（memory_events）
  + candidate shadow layer（memory_candidates）
  + versioned core_blocks（core_blocks）
  + access audit layer（memory_access_log）
```

**服务拓扑：**

```
iPhone (Health Auto Export)     Telegram User
    │                               │
    ▼                               ▼
Cloudflare Tunnel (agent.xeon.im)  Telegram Bot (bot.py)
    │                               │
    ▼                               ▼
kiwi-mem :8080  ←─────────────  claude.ai MCP (locapmemo)
(FastAPI 记忆网关)                 6 tools
    │
    ▼
PostgreSQL :5432 + pgvector
(memory_events + memory_candidates + memories + core_blocks + memory_access_log + raw_health_data)
```

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

| Agent | 写入方式 | 可写 memories | 可写 candidates | 可写 core_blocks |
|-------|---------|:---:|:---:|:---:|
| Manual user (via API) | `POST /debug/memories` | ✅ user_direct | — | — |
| Health pipeline | `POST /data/health` | ✅ system_generated | — | — |
| AI extraction (internal) | background task | ✅ assistant_inferred | ✅ pending | — |
| MCP (Claude.ai) | `POST /debug/memories` via mcp_server | ✅ assistant_inferred | — | ❌ |
| Telegram Bot | `POST /debug/memories` via HTTP | ✅ user_direct | — | ❌ |

**当前规则：**
- 外部 agent 写入均为 `assistant_inferred`，应保持 candidate-only（Phase 1 resolver 接管前暂时落到 memories 表，带 provenance）
- 外部 agent 不允许直接写 `core_blocks`
- 不允许 MCP 或外部 agent 调用数据库直写

---

## 7. Future Architecture Direction

Phase 1 将逐步引入（**当前未实现**）：

```
memory_items (UUID, new committed layer)
  ← candidate resolver (pending → committed / rejected)
  ← privacy-gated retrieval (privacy_level + actor_scope filter)
  ← policy rules engine
  ← review queue
  ← minimal eval set
```

**未来可选增强（不在 Phase 1 必须范围内）：**
- FTS + vector + structured hybrid search with RRF fusion
- Reranker
- Graph-based conflict resolution (AGM)
- SpiceDB / ReBAC for fine-grained access control
- WeChat historical import pipeline
- Embedding shadow migration
- Training export with de-identification
