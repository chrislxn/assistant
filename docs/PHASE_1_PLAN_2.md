# Phase 1 Plan — Long-Term Personal AI Memory System

> 日期：2026-05-08
> 状态：**planning**
> 前置：Phase 0.5 completed / sealed（tag: `phase-0.5-hermes-conservative`）

---

## 1. Phase 1 目标

Phase 1 的目标是**补上 Phase 0.5 留下的最关键的缺失环节**，让系统从"有 provenance 的 legacy 系统"进化为"有 resolver、有 privacy gate、有 eval 的基础系统"。

**一句话：**

> Phase 1 activates the candidate→memory pipeline with a resolver, introduces privacy-gated retrieval, and establishes measurable retrieval quality.

具体：

1. **Candidate resolver** — 让 `memory_candidates` 的 pending candidates 有一条明确的提交/拒绝路径，而不是永远 pending。
2. **memory_items activation** — 建立新的 committed memory 表（UUID），新 committed memory 写入 memory_items，legacy memories 逐步退出。
3. **Privacy-gated retrieval** — 检索时根据 `privacy_level` + `actor_scope` 过滤，sensitive/restricted/sealed 记忆不注入无关上下文。
4. **Review queue** — 最小可行的审核队列（API + 管理面板基础），让 pending candidates 可被人工审核。
5. **Memory_type cleanup** — 减少 `legacy` 和 `fragment` 类型占比，迁移到 CONTEXT.md 分类法。
6. **Minimal eval set** — 10-20 条标注查询，可重复运行，覆盖 recall/precision/sensitive_leak。
7. **Basic policy rules** — 简单的规则引擎：source_trust ≥ threshold → auto-commit；high-stakes types → require review。

---

## 2. 非目标（Explicitly Out of Scope for Phase 1）

以下功能**明确不在 Phase 1 范围内**，禁止当作已实现或半实现：

- SpiceDB / ReBAC / Zanzibar-style 权限系统
- AGM graph-based conflict resolver
- Elasticsearch / OpenSearch
- WeChat 全量历史导入
- 复杂 Web UI / 审核仪表盘
- Fine-tuning / training export pipeline
- Multi-agent 自主写权限（Hermes 已是最受限形态，Phase 1 不扩展 agent 权限）
- Embedding shadow migration / model 更换
- RRF fusion / reranker / hybrid search
- Intent classifier（所有请求仍 `intent='chat'`）
- Health summary tiered architecture（仍保持 Phase 0.5 形态）
- Graph database / dedicated vector DB

---

## 3. 当前 v0.5 状态（Phase 1 起点）

### 已存在的基础设施

| 层 | 表 | 状态 |
|----|----|------|
| Raw events | `memory_events` | Append-only，所有新写入经由此层 |
| Candidates | `memory_candidates` | Shadow/proposal，assistant_inferred → pending |
| Committed memory | `memories` (legacy) | 仍是主 committed layer，integer ID |
| Core memory | `core_blocks` | Versioned, curated, approval-gated |
| Access audit | `memory_access_log` | Read/context 审计，fire-and-forget |

### 已接入的写入路径

| 入口 | source_trust | 写入目标 |
|------|-------------|---------|
| `POST /debug/memories` | `user_direct` | memory_events → memories |
| `POST /data/health` | `system_generated` | memory_events → memories |
| AI extraction (internal) | `assistant_inferred` | memory_events → memory_candidates (pending) + memories (compat) |
| MCP save_memory (claude_mcp) | `assistant_inferred` | memory_events → memories |
| Hermes observe | `assistant_inferred` | memory_events only |
| Hermes propose_memory | `assistant_inferred` | memory_candidates (pending) only |

### 当前缺失（Phase 1 要补的）

- `auto_commit_candidate()` 已定义但**从未被调用** — candidates 永远停留在 pending
- `privacy_level` / `actor_scope` 字段已存在于 memories 和 candidates，但检索时**不根据它们过滤**
- `memory_items` 表**不存在** — 无 UUID-based committed memory 层
- 无 review queue — pending candidates 只能通过 SQL 直接查看
- 无 eval set — 检索质量不可测量
- 无 policy rules — 所有 auto-commit 决策硬编码在 `create_candidate()` 的 CASE WHEN 中

---

## 4. Phase 1 数据流

### 4.1 Write Path（Phase 1 目标状态）

```
source adapter
  → append_event()                    ← 不可跳过
  → create_candidate()                ← assistant_inferred → pending
  → candidate_resolver()              ← NEW: Phase 1 核心
      ├── source_trust IN (user_direct, system_generated)
      │     AND memory_type NOT IN high_stakes_types
      │     AND privacy_level = 'personal'
      │     → auto_commit → memory_items (NEW) + memories (compat)
      │
      ├── source_trust = 'assistant_inferred'
      │     AND confidence >= 0.85
      │     AND memory_type IN low_risk_types
      │     → auto_commit → memory_items (NEW)
      │
      └── 其他所有情况
            → status='pending' → review queue
```

### 4.2 Read Path（Phase 1 目标状态）

```
chat request
  → load approved core_blocks (whitelist: response_policy + active_projects)
  → privacy gate: filter by actor_scope + privacy_level
      ├── actor='hermes_agent' → exclude sealed, restricted, sensitive
      ├── actor='telegram_bot' → exclude sealed, restricted
      └── actor='claude_mcp'   → exclude sealed
  → retrieve from memory_items (NEW, primary) + memories (legacy, compat)
  → assemble system prompt
  → log_memory_access() fire-and-forget
  → return response
```

### 4.3 Hermes 保持不变

- Hermes 仍只写 `memory_events` + `memory_candidates`（pending）
- Hermes 不调用 resolver，不触发 auto-commit
- Hermes get_context 仍用 whitelist core blocks
- Hermes 不读 sealed/restricted

---

## 5. Candidate Resolver 规则

### 5.1 Resolver 函数签名

```python
async def resolve_candidate(candidate_id: str) -> dict:
    """
    评估一条 candidate 是否应该自动提交。

    返回：
    {
        "action": "auto_commit" | "keep_pending" | "reject",
        "reason": "...",
        "memory_id": "uuid or None"
    }
    """
```

### 5.2 Auto-Commit 规则（初版）

**规则优先级从上到下，命中即停：**

| # | 条件 | 动作 |
|---|------|------|
| R1 | `source_trust = 'user_direct'` AND `privacy_level = 'personal'` AND `memory_type NOT IN ('identity_fact', 'relationship_context', 'health_pattern')` | auto_commit |
| R2 | `source_trust = 'system_generated'` AND `memory_type = 'health_observation_summary'` | auto_commit |
| R3 | `source_trust = 'assistant_inferred'` AND `confidence >= 0.85` AND `memory_type IN ('preference', 'project_state', 'project_decision', 'procedure', 'episodic_event', 'device_inventory', 'external_fact')` | auto_commit |
| R4 | `source_trust = 'assistant_inferred'` AND `confidence < 0.85` | keep_pending |
| R5 | `memory_type IN ('identity_fact', 'relationship_context', 'health_pattern')` AND `source_trust != 'user_direct'` | keep_pending |
| R6 | 与已有 active memory 存在 `(subject_key, predicate_key)` 冲突 | keep_pending (flag conflict) |

### 5.3 High-Stakes Memory Types（永远不进 auto-commit）

- `identity_fact` — 身份事实
- `relationship_context` — 关系上下文
- `health_pattern` — 健康模式
- `risk_flag` — 风险标记
- `policy_rule` — 策略规则

这些类型需要人工审核（review queue），无论 confidence 多高。

### 5.4 Low-Risk Memory Types（可 auto-commit）

- `preference` — 偏好
- `project_state` — 项目状态
- `project_decision` — 项目决策
- `procedure` — 流程
- `episodic_event` — 事件
- `device_inventory` — 设备
- `external_fact` — 外部事实
- `project_knowledge` — 项目知识

### 5.5 Dedup 规则（最小实现）

在 auto-commit 前检查 `(subject_key, predicate_key)` 是否已有 active memory：

- 无匹配 → 正常提交
- 完全匹配（相同 subject_key + predicate_key） → 比较 confidence，更高的 supersede 旧的
- 部分匹配（相同 subject_key，不同 predicate_key） → 正常提交（不冲突）

### 5.6 Resolver 调用时机

- **不**在 `POST /candidates` 内同步调用（保持 API 快速响应）
- 在以下时机异步触发：
  1. 每日 digest 时（`daily_digest_scheduler`）
  2. Dream 整合时（`auto_dream_scheduler`）
  3. 手动触发（`POST /admin/resolve-candidates`）
- 不实时 resolve（避免每次 chat 都触发 resolve 逻辑）

---

## 6. memory_items 启用策略

### 6.1 Schema

```sql
CREATE TABLE memory_items (
    memory_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    memory_type     TEXT NOT NULL,
    subject_key     TEXT,
    predicate_key   TEXT,
    rendered_text   TEXT NOT NULL,
    canonical_value JSONB,
    source_event_ids UUID[],
    source_candidate_id UUID,          -- FK → memory_candidates
    source_trust    TEXT NOT NULL,
    privacy_level   TEXT NOT NULL DEFAULT 'personal',
    actor_scope     TEXT[] DEFAULT '{local_bot,claude_mcp}',
    confidence      FLOAT,
    importance      INT DEFAULT 5,
    heat            FLOAT DEFAULT 1.0,
    status          TEXT DEFAULT 'active',  -- active / superseded / deprecated
    supersedes_memory_id UUID,
    valid_from      TIMESTAMPTZ,
    valid_to        TIMESTAMPTZ,
    access_count    INT DEFAULT 0,
    last_accessed_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    embedding       VECTOR(1024)
);
```

### 6.2 迁移策略（不是一次性切换）

**Phase 1 采用双写 + 渐进式读取：**

1. **Step 1**：建表 `memory_items`，不改变任何写入路径。
2. **Step 2**：resolver auto_commit 时**同时写** `memory_items`（新）和 `memories`（旧 compat）。
3. **Step 3**：检索时**优先读** `memory_items`，fallback `memories`。
4. **Step 4**：验证 memory_items 覆盖率（`memory_items_count / (memories_count + memory_items_count)`）。
5. **Step 5**：覆盖率 >95% 后，新写入**只写** `memory_items`，停止双写 `memories`。
6. **Step 6**：Phase 2+ 逐步将旧 `memories` 迁移到 `memory_items`。

**不追求** Phase 1 内完成全量迁移。

### 6.3 向后兼容

- `search_memories()` 新增可选参数 `include_memory_items=True`
- `get_recent_memories()` 同上
- 所有现有 API 响应格式不变（仍返回 `id` 字段，memory_items 返回 `memory_id` 作为 `id`）
- `log_memory_access()` 的 `memory_ids` 字段开始填充 memory_items 的 UUID

---

## 7. Privacy-Gated Retrieval 最小规则

### 7.1 Actor-Privacy Matrix

| privacy_level | local_bot | claude_mcp | hermes_agent | dev_agent |
|---|---|---|---|---|
| `public_like` | ✅ | ✅ | ✅ | ✅ |
| `personal` | ✅ | ✅ | ✅ | ✅ |
| `sensitive` | ✅ | ✅ | ❌ | ❌ |
| `restricted` | ✅ | ❌ | ❌ | ❌ |
| `sealed` | ❌ | ❌ | ❌ | ❌ |

### 7.2 实现方式

**在检索 SQL 层过滤，不在应用层后过滤（防止敏感数据进入 prompt 后再指望 LLM 忽略）：**

```sql
-- 在 search_memories / get_recent_memories 查询中添加：
AND (
    privacy_level IS NULL
    OR privacy_level = 'public_like'
    OR privacy_level = 'personal'
    OR (privacy_level = 'sensitive' AND $actor NOT IN ('hermes_agent', 'dev_agent'))
    OR (privacy_level = 'restricted' AND $actor IN ('local_bot', 'telegram_bot'))
)
-- sealed 永远不自动返回
```

### 7.3 不实现

- actor_scope 数组匹配（`'{local_bot,claude_mcp}'::text[] && ARRAY[$actor]`）— 字段已存在但 Phase 1 不做数组交集过滤，只用 privacy_level 硬规则
- Intent-based privacy relaxation — 所有 intent 用同一套 privacy gate
- 动态权限变更 — 无 SpiceDB/ReBAC

### 7.4 Hermes 保持不变

Hermes 已有的 `EXCLUDE_PRIVACY = "sealed,restricted"` 与新的 privacy gate 叠加：Hermes 请求传入 `actor='hermes_agent'`，SQL 层直接排除 sensitive/restricted/sealed。

---

## 8. Review Queue 最小 API

### 8.1 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/admin/candidates?status=pending&limit=20` | 列出 pending candidates |
| `GET` | `/admin/candidates/{candidate_id}` | 查看单条 candidate 详情 |
| `POST` | `/admin/candidates/{candidate_id}/commit` | 人工批准 → 写入 memory_items |
| `POST` | `/admin/candidates/{candidate_id}/reject` | 人工拒绝 → status='rejected' |

### 8.2 权限

- 所有 `/admin/candidates` 端点受 `AdminAuthMiddleware` 保护（加入 `PROTECTED_PREFIXES`）
- 当前 single-token 模型已足够
- 不需要 multi-user approval workflow
- 不需要 UI — 管理面板可后续添加简单的列表页，Phase 1 只要求 API

### 8.3 不在 Phase 1 实现

- 管理面板审核 UI（可在 API 就绪后快速追加一个静态列表页，但不阻塞 Phase 1）
- 批量审核（单条 commit/reject 已足够）
- 审核日志 / 审核者身份记录
- 邮件/通知提醒

---

## 9. Eval Set 草案

### 9.1 设计原则

- 10-20 条标注查询
- 可重复运行，纯 SQL + API 验证
- 不需要外部 LLM judge
- 每次 retrieval 逻辑变更后重新跑

### 9.2 查询模板

```json
{
  "query": "Why did I decide not to use a single memories table?",
  "actor": "local_bot",
  "intent": "technical_debug",
  "should_retrieve_types": ["project_decision", "project_state", "procedure"],
  "should_not_retrieve_types": ["relationship_context", "health_pattern"],
  "must_include_core_blocks": ["active_projects"],
  "max_sensitive_leaks": 0
}
```

### 9.3 建议的 15 条 Eval 查询

| # | 查询 | intent | 关键约束 |
|---|------|--------|---------|
| 1 | "我为什么不用单表记忆？" | project_status | 必须命中 project_decision，必须注入 active_projects |
| 2 | "我的树莓派上跑了哪些服务？" | technical_debug | 必须命中 device_inventory + procedure |
| 3 | "我这周睡得好吗？" | health_today | 可命中 health_pattern，不可泄露给 hermes_agent |
| 4 | "我和 Ellie 上次聊了什么？" | relationship_context | 敏感记忆，local_bot 可见，claude_mcp 不可见 |
| 5 | "我的 MacBook 什么配置？" | device_inventory | 必须命中 device_inventory |
| 6 | "M5 阶段做了什么？" | project_status | 必须命中 project_state，注入 active_projects |
| 7 | "我喜欢喝什么？" | preference | 必须命中 preference |
| 8 | "我上次游泳是什么时候？" | episodic_event | 必须命中 episodic_event |
| 9 | "我的学术计划是什么？" | academic_planning | 可命中 academic_context |
| 10 | "我今天的步数是多少？" | health_today | 可命中 health 数据，Hermes agent 不可见 |
| 11 | "PostgreSQL 怎么备份？" | procedure | 必须命中 procedure |
| 12 | "我最近买了什么设备？" | device_inventory | 必须命中 device_inventory |
| 13 | "我的 AI 系统架构是什么？" | project_status | 必须命中 project_decision + active_projects |
| 14 | "我有什么健康问题吗？" | health_trend | sensitive，claude_mcp 不可见 |
| 15 | "我最常用的编程语言是什么？" | preference | 必须命中 preference |

### 9.4 Metrics

| 指标 | 定义 | 目标 |
|------|------|------|
| `recall@10` | 该命中的记忆命中比例 | ≥ 0.80 |
| `precision@10` | Top-10 中相关记忆比例 | ≥ 0.60 |
| `sensitive_leak_count` | 不该返回的 sensitive/restricted 记忆数量 | = 0 |
| `wrong_core_block_count` | 注入了不该注入的 core block 数量 | = 0 |
| `stale_memory_count` | 返回了 superseded/deprecated 记忆的数量 | ≤ 1 |

### 9.5 运行方式

```bash
# 每次 retrieval 变更后运行
python tests/run_eval.py --eval-set docs/eval_set.json --actor local_bot
python tests/run_eval.py --eval-set docs/eval_set.json --actor hermes_agent
```

---

## 10. 实施顺序

Phase 1 分 7 个子阶段，按依赖关系排列：

```
M1: Schema (memory_items 建表)
  ↓
M2: Candidate Resolver
  ↓
M3: Privacy-Gated Retrieval
  ↓
M4: Review Queue API
  ↓
M5: Memory Type Cleanup
  ↓
M6: Basic Policy Rules
  ↓
M7: Eval Set + Runner
```

### 详细步骤

**M1 — memory_items Schema（预计 1 session）**
- 建表 `memory_items`（含 embedding 列）
- 新增 database.py helper：`insert_memory_item()`, `get_memory_item()`, `search_memory_items()`
- 不改变任何现有写入路径
- 验收：表存在，helper 函数可调用

**M2 — Candidate Resolver（预计 1-2 sessions）**
- 实现 `resolve_candidate()` 函数
- 异步触发：digest + dream + 手动端点
- 新增 `POST /admin/resolve-candidates`
- auto_commit 走双写（memory_items + memories）
- 验收：user_direct candidate → auto_commit；assistant_inferred/low_confidence → pending；identity_fact → pending

**M3 — Privacy-Gated Retrieval（预计 1 session）**
- 修改 `search_memories()` / `get_recent_memories()` 加入 privacy_level 过滤
- 调用方传入 `actor` 参数
- `/v1/chat/completions` 传入 `actor='api_client'`
- Telegram bot 传入 `actor='telegram_bot'`
- Hermes 传入 `actor='hermes_agent'`
- 验收：sensitive 记忆不对 hermes_agent 返回

**M4 — Review Queue API（预计 1 session）**
- 实现 4 个 `/admin/candidates` 端点
- 管理面板可追加静态列表页（可选，不阻塞）
- 验收：可列出 pending candidates，可 commit/reject

**M5 — Memory Type Cleanup（预计 1 session）**
- 回填现有 `legacy` / `fragment` / `daily_digest` 类型到 CONTEXT.md 分类法
- LLM memory extraction prompt 更新为使用新分类法
- 验收：新增记忆 legacy 比例 < 20%

**M6 — Basic Policy Rules（预计 1 session）**
- 将 auto-commit 规则从 `resolve_candidate()` 中抽到简单的规则配置
- Python dict/list 配置即可，不需要 policy_rules 表
- 验收：修改规则不需要改 resolver 代码

**M7 — Eval Set + Runner（预计 1 session）**
- 创建 15 条标注查询（`docs/eval_set.json`）
- 实现 `tests/run_eval.py`
- 按 actor 分别跑 eval
- 验收：5 项指标全部达标

---

## 11. 验收标准

### 11.1 Phase 1 整体验收

| # | 检查项 | 标准 |
|---|--------|------|
| 1 | memory_items 表存在且有数据 | `SELECT count(*) FROM memory_items` > 0 |
| 2 | resolver 运行过 | `SELECT count(*) FROM memory_candidates WHERE status IN ('committed', 'rejected')` > 0 |
| 3 | privacy gate 生效 | hermes_agent 请求不返回 sensitive 记忆（eval 确认 sensitive_leak_count = 0） |
| 4 | review queue 可用 | GET /admin/candidates 返回 pending 列表 |
| 5 | memory_type cleanup | 新记忆 legacy 比例 < 20% |
| 6 | eval 全绿 | 5 项指标全部达标 |
| 7 | 现有功能不受影响 | /data/health, /v1/chat/completions, Telegram bot, MCP 全部正常 |
| 8 | Hermes 行为不变 | Hermes 仍只写 events + pending candidates，不读写 sealed/restricted，不写 core_blocks |
| 9 | 双写验证 | resolver auto_commit 同时写入 memory_items 和 memories |
| 10 | pg_dump 备份 | 每次 schema 变更前执行 |

### 11.2 不阻塞 Phase 1 完成的条件

以下可以延后到 Phase 1.5 或 Phase 2：

- memory_items 覆盖率 >95%（Phase 1 只要求有数据，不要求全覆盖）
- 管理面板审核 UI
- 旧 memories 全量迁移
- eval set 扩展到 >15 条
- intent classifier
- 自动化 CI 运行 eval

---

## Appendix A: 与 Phase 0.5 的差异总结

| 维度 | Phase 0.5 | Phase 1 |
|------|----------|---------|
| Committed memory | `memories` (legacy) | `memory_items` (new) + `memories` (compat) |
| Candidate fate | 永远 pending | resolver → auto_commit 或 keep_pending 或 reject |
| Privacy filter | 字段存在，不执行 | SQL 层 enforcement |
| Hermes privacy | hardcoded exclude_privacy | SQL 层 actor-based gate（与 Hermes 叠加） |
| Review | 无 | 4 端点 API |
| Eval | 无 | 15 条 + runner |
| Policy rules | 硬编码 CASE WHEN | 可配置规则 |

## Appendix B: 文件改动预估

| Phase | 文件 | 改动量 |
|-------|------|--------|
| M1 | `database.py` | +60 行（schema + helpers） |
| M2 | `database.py`, `main.py` | +120 行（resolver + 端点 + digest/dream 触发） |
| M3 | `database.py`, `main.py`, `bot.py`, `hermes_mcp.py` | +40 行（actor 参数传递 + SQL filter） |
| M4 | `main.py` | +80 行（4 端点） |
| M5 | `memory_extractor.py`, `database.py` | +50 行（分类法映射 + prompt 更新） |
| M6 | `database.py` | +40 行（规则配置 + 从 resolver 中抽离） |
| M7 | `docs/eval_set.json`, `tests/run_eval.py` | +300 行（eval 数据 + runner） |
| **Total** | | **~690 行** |
