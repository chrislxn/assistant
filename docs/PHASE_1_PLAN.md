# Phase 1 Plan — Long-Term Personal AI Memory System

> 日期：2026-05-08
> 状态：**planning**（reviewed）
> 前置：Phase 0.5 completed / sealed（tag: `phase-0.5-hermes-conservative`）

Phase 1 不是重写系统。Phase 1 不是一步到位。Phase 1 的主线是：

> **candidate resolver → memory_items → review queue → privacy gate → eval**

分三个子阶段交付：Phase 1.0 (Memory Lifecycle) → Phase 1.1 (Retrieval Safety) → Phase 1.2 (Cleanup & Policy)。

---

## Starting Point

Phase 1 从 Phase 0.5 + Hermes conservative integration 的当前状态出发：

| 层 | 当前状态 | Phase 1 角色 |
|---|---------|-------------|
| `memory_events` | Append-only，所有新写入经由此层 | 不变（provenance 基础） |
| `memory_candidates` | Shadow/proposal，`assistant_inferred` → `pending`，`pending_auto` 从未被消费 | Phase 1.0 成为 resolver 输入 |
| `memories` (legacy) | 当前主 committed layer，integer ID | Phase 1.0 compat dual-write 目标 |
| `core_blocks` | Versioned, curated, approval-gated，白名单注入 | 不变 |
| `memory_access_log` | Read/context 审计，fire-and-forget | 不变 |
| Hermes MCP | 5 restricted tools，只写 events + pending candidates | 不变 |

**关键前提：**
- `auto_commit_candidate()` 已定义但从未被调用 — candidates 永远停留在 pending。
- `privacy_level` / `actor_scope` 字段已存在，但检索时不根据它们过滤。
- `memory_items` 表不存在 — 无 UUID-based committed memory 层。
- 无 review queue、无 eval set、无 policy rules。

---

## 1. Phase 1.0 — Memory Lifecycle

### 1.1 目标

建立 candidate → memory_items 的生命周期管道：candidates 不再永远 pending，有一条明确的提交/拒绝路径。新的 committed memory 写入 `memory_items`（UUID），旧 `memories` 表保持兼容双写。

### 1.2 范围

**做：**

- memory_items schema（不含 embedding）
- database helper：`insert_memory_item()`, `get_memory_item()`
- candidate resolver：`resolve_candidate()`
- minimal review queue API（4 端点）
- auto_commit 双写 memory_items + memories（compat）
- resolver 异步触发（daily digest / dream / 手动端点）

**不做：**

- privacy-gated retrieval
- eval set / runner
- memory_type cleanup
- policy rules 抽离
- MCP 行为重构
- Hermes 权限扩展
- embedding 相关任何改动

### 1.3 memory_items Schema

```sql
-- 不含 embedding。embedding 留给未来独立表 memory_embeddings。
CREATE TABLE memory_items (
    memory_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    memory_type          TEXT NOT NULL,
    subject_key          TEXT,
    predicate_key        TEXT,
    rendered_text        TEXT NOT NULL,
    canonical_value      JSONB,
    source_event_ids     UUID[],
    source_candidate_id  UUID,           -- FK → memory_candidates
    source_trust         TEXT NOT NULL,
    privacy_level        TEXT NOT NULL DEFAULT 'personal',
    actor_scope          TEXT[] DEFAULT '{local_bot,claude_mcp}',
    confidence           FLOAT,
    importance           INT DEFAULT 5,
    heat                 FLOAT DEFAULT 1.0,
    status               TEXT DEFAULT 'active',  -- active / superseded / archived / disputed / redacted
    supersedes_memory_id UUID,
    valid_from           TIMESTAMPTZ,
    valid_to             TIMESTAMPTZ,
    access_count         INT DEFAULT 0,
    last_accessed_at     TIMESTAMPTZ,
    created_at           TIMESTAMPTZ DEFAULT NOW()
);
```

**memory_items.status 值：**

| 值 | 含义 |
|---|------|
| `active` | 当前有效，可被检索注入 |
| `superseded` | 被新版本取代（`supersedes_memory_id` 指向新记录） |
| `archived` | 不再有效但保留（如已完成的 project_state） |
| `disputed` | 存在未解决的冲突 |
| `redacted` | 内容被移除，保留 metadata 用于审计 |

**memory_candidates.status 值：**

| 值 | 含义 |
|---|------|
| `pending` | 等待 resolver 评估（`assistant_inferred` 默认状态） |
| `pending_auto` | 等待自动提交（`user_direct` / `system_generated` 默认状态） |
| `committed` | 已提交（resolver auto_commit 或人工 commit） |
| `rejected` | 人工拒绝 |
| `requires_review` | 标记为需要人工审核（high-stakes types） |

### 1.4 Candidate Resolver 规则

Resolver 对每条 candidate 评估 `(source_trust, memory_type)` → 决定 auto_commit / keep_pending / requires_review。

| # | source_trust | memory_type | 动作 |
|---|-------------|-------------|------|
| R1 | `user_direct` | low-risk, non-high-stakes | **auto_commit** |
| R2 | `system_generated` | `health_observation_summary` | **auto_commit** |
| R3 | `assistant_inferred`（含 `claude_mcp` / `hermes_agent`） | any | **keep_pending** |
| R4 | any | `health_pattern` | **requires_review** |
| R5 | any | `health_baseline` | **requires_review** |
| R6 | any（非 `user_direct`） | `identity_fact` | **requires_review** |
| R7 | any（非 `user_direct`） | `relationship_context` | **requires_review** |
| R8 | any | diagnosis-like（含 clinical/medical/diagnosis） | **requires_review** |
| R9 | any | 与已有 active memory `(subject_key, predicate_key)` 冲突 | **keep_pending**（flag conflict） |

**High-stakes types（R4-R8，必须 requires_review）：**
- `health_pattern` — 长期健康模式推断
- `health_baseline` — 基准值
- `identity_fact` — 身份事实（非 `user_direct`）
- `relationship_context` — 关系上下文（非 `user_direct`）
- diagnosis-like — 任何包含临床/诊断暗示的记忆

**Low-risk types（R1 可 auto_commit）：**
- `preference`, `project_state`, `project_decision`, `procedure`, `episodic_event`, `device_inventory`, `external_fact`, `project_knowledge`, `health_observation_summary`

**关键约束：**
- `assistant_inferred` **永远不** auto_commit，无论 confidence 多高。
- `assistant_inferred` 低风险自动提交最多写成 Phase 1.2 / Phase 2 的**可选未来方向**，Phase 1.0 不实现。
- 不允许仅因为 confidence 高就 auto_commit。
- 不允许把短期健康观测自动升级成长期健康模式。
- **auto_commit of a `health_observation_summary` does not update `health_baseline` and does not create a `health_pattern`.** 健康观测和长期模式之间有硬边界。`health_baseline` 和 `health_pattern` 必须走独立的 requires_review 路径。

### 1.5 Dual-Write 策略

resolver auto_commit 时：

```
resolve_candidate() → auto_commit
  ├── INSERT INTO memory_items (...)     ← 新 committed layer
  ├── INSERT INTO memories (...)          ← compat：旧 API / MCP / bot 仍可读
  └── UPDATE memory_candidates
      SET status = 'committed'
```

非 auto_commit 的 candidate 保持 `status = 'pending'`，等待 review queue。

#### valid_from / valid_to Rules（Pre-Flight Decision）

Phase 1.0 resolver 和 review queue 必须统一 `valid_from` / `valid_to` 行为：

**Auto-commit 时：**
- `new.valid_from = candidate.valid_from`（如果 candidate 提供了）否则 `now()`
- `new.valid_to = NULL`

**Manual commit 时（`POST /admin/candidates/{id}/commit`）：**
- 同上。

**Supersede 冲突时（new memory 取代 old active memory）：**
- `old.status = 'superseded'`
- `old.valid_to = now()`
- `new.valid_from = now()`（除非 candidate 提供 explicit `valid_from`）
- `new.supersedes_memory_id = old.memory_id`

**无冲突时：**
- `valid_to` 保持 `NULL`（表示当前有效，无截止时间）。

这些规则是 Phase 1.0 resolver 的最低要求。让 `valid_from` / `valid_to` 在 Phase 1.0 就进入数据模型，避免 Phase 1.1 或以后回填。

### 1.6 Review Queue API

Phase 1.0 只做 4 个端点，不做 UI：

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/admin/candidates?status=pending&limit=20` | 列出 pending candidates |
| `GET` | `/admin/candidates/{candidate_id}` | 查看单条 candidate 详情 |
| `POST` | `/admin/candidates/{candidate_id}/commit` | 人工批准 → 写入 memory_items + memories |
| `POST` | `/admin/candidates/{candidate_id}/reject` | 人工拒绝 → status='rejected' |

**权限：** 受 `AdminAuthMiddleware` 保护（`PROTECTED_PREFIXES` 加入 `/admin/candidates`）。

**不做：** edit、batch、complex UI、multi-user approval、notification、audit workflow。

#### assistant_inferred Backlog（Pre-Flight Decision）

`assistant_inferred`（含 `claude_mcp` / `hermes_agent`）默认 `keep_pending` 是正确的安全策略，但会产生 pending candidate 堆积（backlog）。

**Phase 1.0 应对：**

- Backlog 必须可见：`GET /admin/candidates` 响应包含 `created_at`、`source_trust`、`confidence`、`age_days`（`NOW() - created_at`），支持按 `source_trust` / `age_days` 排序。
- Phase 1.0 **不**因为 confidence 高就自动 commit `assistant_inferred`。
- Phase 1.0 **不**自动 archive stale pending candidates（可列为 Phase 1.2 / later 选项）。
- Phase 1.0 可选增加 stats 端点 `GET /admin/candidates/stats`，按 `source_trust` / `status` / `age_bucket` 分组计数。

**未来决策依赖观察：** 需要在真实运行中观察 pending 增长速度，再决定 Phase 1.2 是否引入 stale archive 策略（如 >90 天未审核的 assistant_inferred candidate 自动 `rejected` 或 `archived`）。

### 1.7 不改 MCP / Hermes

- Hermes conservative integration 行为完全不变。
- MCP `save_memory` 保持 Phase 0.5 兼容路径。
- Hermes 仍只写 `memory_events` + `memory_candidates`（pending），不触发 auto_commit。
- MCP/Hermes candidate-only 重构可放 Phase 1.2 讨论，不阻塞 Phase 1.0。

### 1.8 Resolver 触发时机

- **不**在 `POST /candidates` 内同步调用（保持 API 快速响应）。
- 异步触发：daily digest 时、Dream 整合时、手动 `POST /admin/resolve-candidates`。
- 不实时 resolve（避免每次 chat 都触发）。

---

## 2. Phase 1.1 — Retrieval Safety

### 2.1 目标

让 privacy_level + actor 在检索时真正生效。敏感记忆不进入无关 prompt。建立可测量的检索质量基线。

### 2.2 范围

**做：**

- privacy-gated retrieval（SQL 层）
- actor privacy matrix
- actor 参数传递统一
- minimal eval set（10-15 条）
- eval runner

**不做：**

- memory_type cleanup
- policy rules 抽离
- Hermes 权限变更
- embedding migration

### 2.3 Actor Privacy Matrix

| privacy_level | local_bot | telegram_bot | claude_mcp | hermes_agent | dev_agent |
|---|---|---|---|---|---|
| `public_like` | yes | yes | yes | yes | yes |
| `personal` | yes | yes | yes | yes | yes |
| `sensitive` | yes | yes | yes | **no** | **no** |
| `restricted` | yes | **no** | **no** | **no** | **no** |
| `sealed` | **no** | **no** | **no** | **no** | **no** |

**关键规则：**

- `sealed` 永远不自动返回（全 actor）。
- `restricted` 仅 `local_bot` 自动可见。
- `sensitive` 不给 `hermes_agent` / `dev_agent`。
- `telegram_bot` 不读 `restricted`。
- Hermes 现有 `EXCLUDE_PRIVACY = "sealed,restricted"` 与 SQL privacy gate 叠加，不冲突。

### 2.4 SQL-Layer Filtering（必须）

**过滤在 SQL retrieval 阶段执行，不在应用层后过滤（防止敏感数据进入 prompt 后再指望 LLM 忽略）。**

`search_memories()` / `get_recent_memories()` / `search_memory_items()` 接收 `actor` 参数，在 SQL WHERE 中构建隐私条件。

```
privacy_level IN (
    'public_like',
    'personal'
) OR (
    privacy_level = 'sensitive'
    AND $actor NOT IN ('hermes_agent', 'dev_agent')
) OR (
    privacy_level = 'restricted'
    AND $actor IN ('local_bot')
)
-- sealed 永远不在 IN 列表中
```

### 2.5 Actor 参数传递

| 入口 | actor 值 | privacy policy 映射 |
|------|---------|-------------------|
| `/v1/chat/completions` | `'api_client'` | 映射到 `local_bot` 权限（除非显式覆盖） |
| Telegram bot | `'telegram_bot'` | `telegram_bot` 列 |
| MCP claude_mcp | `'claude_mcp'` | `claude_mcp` 列 |
| MCP hermes_agent | `'hermes_agent'` | `hermes_agent` 列 |
| dev_agent | `'dev_agent'` | `dev_agent` 列（预留） |

`api_client` currently maps to `local_bot` policy unless explicitly overridden.

#### api_client Security Note（Pre-Flight Decision）

`api_client` 当前映射到 `local_bot` 权限**仅因为** `/v1/chat/completions` 被假定为可信本地/私有端点。

- 如果未来 `/v1/chat/completions` 暴露给第三方工具、外部服务或低信任 client，**不能**继续默认 `local_bot`。
- Future option：要求显式 `X-Actor` header，或默认 `api_client` 降为低权限策略（如 `personal`-only）。
- **Phase 1.1 实现前必须确认** `/v1/chat/completions` 的调用边界（谁在调、从哪里调、是否需要拆分端点）。

### 2.6 Eval Set

**10-15 条标注查询，可重复运行，不需要外部 LLM judge。**

Eval 查询分为两类：

- **Positive retrieval tests** — 验证检索能召回该命中的记忆（测 recall/precision）。
- **Negative leakage tests** — 验证 sensitive/restricted/sealed 记忆不泄露到不该看到的 actor（测 sensitive_leak_count）。

#### Positive Retrieval Tests

这些查询测试检索质量：该命中的记忆是否命中。

| # | 查询 | intent | 必须命中的 type |
|---|------|--------|-----------------|
| P1 | "我为什么不用单表记忆？" | project_status | `project_decision` |
| P2 | "我的树莓派上跑了哪些服务？" | technical_debug | `device_inventory`, `procedure` |
| P3 | "我的 MacBook 什么配置？" | device_inventory | `device_inventory` |
| P4 | "M5 阶段做了什么？" | project_status | `project_state` |
| P5 | "我喜欢喝什么？" | preference | `preference` |
| P6 | "我上次游泳是什么时候？" | episodic_event | `episodic_event` |
| P7 | "PostgreSQL 怎么备份？" | procedure | `procedure` |
| P8 | "我最近买了什么设备？" | device_inventory | `device_inventory` |
| P9 | "我的 AI 系统架构是什么？" | project_status | `project_decision` |
| P10 | "我最常用的编程语言是什么？" | preference | `preference` |

#### Negative Leakage Tests

这些查询测试隐私门：sensitive/restricted 记忆**不应**泄露到指定 actor。

| # | 查询 | 含有的敏感记忆类型 | 禁止返回的 actor |
|---|------|------------------|-----------------|
| L1 | "我这周睡得好吗？" | `health_pattern`, `health_observation_summary` (sensitive) | `hermes_agent`, `dev_agent` |
| L2 | "我今天步数多少？" | `health_observation_summary` (sensitive) | `hermes_agent`, `dev_agent` |
| L3 | "我和 Ellie 上次聊了什么？" | `relationship_context` (restricted) | `hermes_agent`, `dev_agent`, `claude_mcp`, `telegram_bot` |
| L4 | "我有什么健康问题吗？" | `health_pattern`, diagnosis-like (sensitive/restricted) | `hermes_agent`, `dev_agent` |
| L5 | "我的学术计划是什么？" | `academic_context` (personal) | 所有 actor 都应可见（验证 personal 不泄露规则没有过度过滤） |

**注意：** 关系/健康查询在 negative leakage tests 中验证的是"隐私门是否生效"，不是"记忆是否被召回"。sensitive_leak_count 必须为 0。

#### Eval Seed Data（Pre-Flight Decision）

**eval runner 不能只依赖现有真实 DB 数据，否则结果不可信。** 真实数据库中的数据可能缺失、类型不均、隐私级别标记不完整，导致 eval 结果不可复现。

Phase 1.1 必须配套 `scripts/seed_eval_data.py`（或 `tests/seed_eval_data.py`），写入受控的测试记忆：

- `source_type='eval_seed'`
- `source_trust='system_generated'`
- `actor='eval_seed'`
- `privacy_level` 按测试需要明确设置（`personal` / `sensitive` / `restricted` / `sealed`）
- `memory_type` / `subject_key` / `predicate_key` 明确可验证

**Seed 数据约束：**

- 必须可重复运行（幂等 — 重复 seed 不产生重复记录）。
- 必须可清理（`scripts/clean_eval_data.py` 或 `DELETE WHERE source_type='eval_seed'`）。
- 不应污染真实长期记忆（`source_type='eval_seed'` 标记确保检索和生产路径可区分）。
- Negative leakage tests 需要 seed `sensitive` / `restricted` / `sealed` 数据来验证不会泄漏到未授权 actor。

**注意：** 关系/健康查询在 negative leakage tests 中验证的是"隐私门是否生效"，不是"记忆是否被召回"。sensitive_leak_count 必须为 0。

#### Eval Seed Isolation（Pre-Flight Decision）

**Eval seed data must not leak into normal retrieval.** 否则 preference / health / relationship 类测试数据会污染真实记忆。

Any memory or event created for eval must be marked with:

- `source_type='eval_seed'`
- `actor='eval_seed'`
- `source_trust='system_generated'`

**Normal retrieval must exclude eval seed data by default.** 未来检索 SQL 必须遵循：

```sql
AND (
    source_type IS DISTINCT FROM 'eval_seed'
    OR $allow_eval_seed = TRUE
)
```

`allow_eval_seed` 仅由 eval runner 启用（`tests/run_eval.py --allow-eval-seed`）。生产路径永远不传此参数。

### 2.7 Eval Metrics

| 指标 | 定义 | 目标 |
|------|------|------|
| `recall@10` | 该命中的记忆命中比例 | ≥ 0.80 |
| `precision@10` | Top-10 中相关记忆比例 | ≥ 0.60 |
| `sensitive_leak_count` | 不该返回的 sensitive/restricted/sealed 记忆数量 | **= 0** |
| `wrong_core_block_count` | 注入了不该注入的 core block 数量 | = 0 |

### 2.8 Eval Runner

```bash
# 按 actor 分别跑
python tests/run_eval.py --eval-set docs/eval_set.json --actor local_bot
python tests/run_eval.py --eval-set docs/eval_set.json --actor hermes_agent
python tests/run_eval.py --eval-set docs/eval_set.json --actor claude_mcp
```

纯 Python stdlib 实现（不需要 pytest /外部框架）。每次 retrieval 逻辑变更后重新跑。

---

## 3. Phase 1.2 — Cleanup & Policy

### 3.1 目标

减少 legacy 类型比例，让 memory_type taxonomy 更稳定。resolver 规则从硬编码抽到可配置层。

### 3.2 范围

**做：**

- memory_type cleanup（legacy/fragment 减少）
- extraction prompt 更新为新 taxonomy
- basic policy rules 从 resolver 中抽离到 Python config

**不做：**

- 大规模历史重写
- 复杂 policy engine
- SpiceDB / ReBAC
- 旧 embedding 重建

### 3.3 Memory Type Cleanup

- 回填现有 `legacy` / `fragment` / `daily_digest` 类型
- LLM extraction prompt 更新为 CONTEXT.md 分类法
- 目标：新增记忆 `legacy` 比例 < 20%
- 不做大规模历史重写（旧 `legacy` 可保留）

### 3.4 Basic Policy Rules

- 将 auto-commit 规则从 `resolve_candidate()` 中抽到 Python dict/list 配置
- 不引入 `memory_policy_rules` 表
- 不引入复杂 rule engine
- 修改规则不需要改 resolver 代码

---

## 4. 实施顺序

Phase 1.0 的第一个完整闭环是 **M1 schema → M2 resolver → M3 review API**。三者共同构成 Memory Lifecycle closure。

实施时仍然必须小步执行，每步单独验证：

### Phase 1.0 — Memory Lifecycle

```
M1 — memory_items schema only
  │   建表（不含 embedding）
  │   helper functions：insert_memory_item(), get_memory_item()
  │   不改变现有写入路径
  │   不改变检索逻辑
  │   验收：\d memory_items + helper 可调用
  ▼
M2 — candidate resolver
  │   resolve_candidate()
  │   auto-commit：user_direct + non-high-stakes → commit
  │   auto-commit：system_generated + health_observation_summary → commit
  │   keep_pending：assistant_inferred / health_pattern / identity_fact / relationship_context
  │   dual-write：memory_items + memories (compat)
  │   异步触发：digest / dream / 手动端点
  │   不改 retrieval
  │   验收：user_direct auto_commit → memory_items + memories；assistant_inferred → pending
  ▼
M3 — review queue API
      4 端点：list / view / commit / reject
      受 AdminAuthMiddleware 保护
      不做 UI
      验收：API 4 端点均 200；commit → memory_items + memories
```

**M1/M2/M3 全部完成后：暂停，观察真实数据一段时间（建议 1-2 周），确认 candidate 产生速率、resolver 行为、backlog 增长速度均符合预期，再进入 Phase 1.1。**

### Phase 1.1 — Retrieval Safety

```
M4 — privacy-gated retrieval
  │   actor privacy matrix → SQL WHERE
  │   search_memories / get_recent_memories 接收 actor 参数
  │   search_memory_items 接收 actor 参数
  │   各入口传入 actor 值
  │   Hermes/Telegram/Claude/local_bot 行为验证
  ▼
M5 — eval set + runner
      docs/eval_set.json（15 条）
      tests/run_eval.py
      按 actor 分别跑
      sensitive_leak_count = 0
```

### Phase 1.2 — Cleanup & Policy

```
M6 — memory_type cleanup
  │   legacy/fragment 减少
  │   extraction prompt 更新
  ▼
M7 — basic policy rules
      resolver 规则抽离到 Python config
      不引入复杂引擎
```

---

## 5. 验收标准

### Phase 1.0 验收

| # | 检查项 | 标准 |
|---|--------|------|
| 1 | memory_items 表存在 | `\d memory_items` 正常 |
| 2 | helper 函数可用 | `insert_memory_item()` / `get_memory_item()` 可调用 |
| 3 | resolver 可用 | `resolve_candidate()` 可调用 |
| 4 | user_direct + low-risk candidate auto_commit | 产生 memory_items 记录 + memories 记录 |
| 5 | system_generated + health_observation_summary auto_commit | 产生 memory_items 记录 |
| 6 | assistant_inferred candidate 保持 pending | hermes_agent / claude_mcp candidate 不自动提交 |
| 7 | high-stakes types 不 auto_commit | identity_fact / relationship_context / health_pattern → pending |
| 8 | review API 可用 | list / view / commit / reject 均 200 |
| 9 | 双写验证 | auto_commit 同时写入 memory_items 和 memories |
| 10 | 现有功能不受影响 | /debug/memories /data/health /v1/chat/completions / Telegram / Hermes 不崩 |

### Phase 1.1 验收

| # | 检查项 | 标准 |
|---|--------|------|
| 1 | privacy gate SQL 层生效 | SQL WHERE 含 privacy_level 过滤 |
| 2 | hermes_agent 不返回 sensitive/restricted/sealed | 手动测试 + eval 确认 |
| 3 | dev_agent 不返回 sensitive/restricted/sealed | 手动测试 |
| 4 | telegram_bot 不返回 restricted/sealed | 手动测试 |
| 5 | sealed 全 actor 不自动返回 | 手动测试 + eval 确认 |
| 6 | eval runner 可重复运行 | `python tests/run_eval.py` 正常 |
| 7 | sensitive_leak_count = 0 | eval 确认 |

### Phase 1.2 验收

| # | 检查项 | 标准 |
|---|--------|------|
| 1 | 新增记忆 legacy 比例 < 20% | 抽样验证 |
| 2 | extraction prompt 使用新 taxonomy | prompt 检查 |
| 3 | resolver policy 可配置 | 修改 config 后行为变化 |
| 4 | 旧 legacy memories 仍兼容 | /debug/memories 仍可查询旧记忆 |
| 5 | 不影响 Phase 1.0/1.1 行为 | 回归验证 |

---

## 6. Explicitly Out of Scope（全 Phase 1）

以下功能**明确不在 Phase 1 范围内**，禁止当作已实现或半实现：

- SpiceDB / ReBAC / Zanzibar-style 权限系统
- AGM graph-based conflict resolver
- Elasticsearch / OpenSearch
- WeChat 全量历史导入
- Hybrid search / RRF / reranker
- Embedding shadow migration / model 更换
- Embedding 表（memory_embeddings）创建或迁移
- Complex review UI / 审核仪表盘
- Fine-tuning / training export pipeline
- Health system full rewrite（health_observations + summaries 分层架构）
- Multi-agent 自主写权限扩展
- Intent classifier（所有请求仍 `intent='chat'`）
- Graph database / dedicated vector DB

---

## 7. 工作方式

继续保持 Phase 0.5 的小步实施：

```
plan → one small milestone → code → verify (SQL + API) → review → only then continue
```

每一步必须明确：
- 改哪些文件
- 不改哪些文件
- 验收 SQL / API 测试
- 回滚风险
- 是否影响现有 bot/API

每个 milestone 开始前 pg_dump。每个 milestone 结束后 commit + 验证。

**Phase 1.0 结束后、Phase 1.1 开始前：暂停观察。** M1-M3 全部完成后，让系统在真实负载下运行 1-2 周，确认：
- candidate 产生速率合理
- resolver auto_commit / keep_pending 分流符合预期
- assistant_inferred backlog 增长可管理
- 旧 API / bot / Hermes 不受影响

观察期通过后，再启动 Phase 1.1。

---

## 8. 文件改动预估

| Phase | 文件 | 改动量 |
|-------|------|--------|
| M1 | `database.py` | +50 行（schema + `insert_memory_item` + `get_memory_item`） |
| M2 | `database.py`, `main.py` | +120 行（resolver + `POST /admin/resolve-candidates` + digest/dream 触发） |
| M3 | `main.py` | +80 行（4 端点 + protected prefix） |
| M4 | `database.py`, `main.py`, `bot.py` | +50 行（actor 参数 + SQL WHERE） |
| M5 | `docs/eval_set.json`, `tests/run_eval.py`, `scripts/seed_eval_data.py` | +350 行 |
| M6 | `memory_extractor.py`, `database.py` | +50 行 |
| M7 | `database.py`（或独立 config） | +30 行 |
| **Total** | | **~730 行** |

---

## Appendix: 与 Phase 0.5 的关键差异

| 维度 | Phase 0.5 | Phase 1.0 | Phase 1.1 |
|------|----------|-----------|-----------|
| Committed memory | `memories` (legacy) | `memory_items` + `memories` (dual-write) | 不变 |
| Candidate fate | 永远 pending | resolver → commit / keep_pending / reject | 不变 |
| assistant_inferred | 直接写 memories（compat） | **keep_pending**（不再直接写 memories） | 不变 |
| Privacy filter | 字段存在，不执行 | 字段存在，不执行 | **SQL 层 enforcement** |
| Hermes privacy | hardcoded exclude_privacy | 不变 | 与 SQL gate 叠加 |
| Review | 无 | 4 端点 API | 不变 |
| Eval | 无 | 无 | 15 条 + runner |
| Policy rules | 硬编码 CASE WHEN | 硬编码 `resolve_candidate()` | 不变（Phase 1.2 抽离） |
