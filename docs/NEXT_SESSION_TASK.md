# Handoff Prompt — Long-Term Personal AI Memory System

你正在继续协助我开发我的长期个人 AI 记忆系统。这个系统基于现有 kiwi-mem 改造，目标不是短期 RAG demo，而是长期个人 AI 记忆底座。

## 当前阶段

Phase 0.5 已完成并封版。

完成内容：

- M1 Schema foundation ✅
- M2 DB helper functions ✅
- M3 write pipeline provenance ✅
- M4 core_blocks + __BOT_PERSONA__ migration ✅
- M5 context injection + access logging ✅
- M6 MCP external entry provenance ✅
- M7 Hermes conservative integration ✅
- Final validation 10/10 ✅
- 两轮 code review 通过 ✅

当前系统状态：

kiwi-mem 已经从普通 memories 插件升级为长期记忆系统 v0.5 地基。

现在架构是：

```text
legacy kiwi-mem runtime
+ memory_events provenance layer
+ memory_candidates shadow/proposal layer
+ core_blocks versioned core memory
+ memory_access_log read/context audit
+ Hermes restricted agent MCP module (5 tools, /hermes/mcp)
```

## Phase 0.5 已实现

### 写入侧

新写入已经 event-first：

```text
append_event()
  → memory_events
  → memories.source_event_ids
  → source_trust / privacy_level / memory_type / status
```

已接入路径：

- `/debug/memories` manual save
  - `event_type='manual_note'`
  - `source_type='manual'`
  - `source_trust='user_direct'`
  - `actor='user'`

- `/data/health`
  - `event_type='health_data'`
  - `source_type='health_pipeline'`
  - `source_trust='system_generated'`
  - `privacy_level='sensitive'`
  - `actor='health_pipeline'`

- AI 自动提取
  - `source_trust='assistant_inferred'`
  - creates `memory_events`
  - creates `memory_candidates(status='pending')`
  - still writes legacy memories with provenance for compatibility

- MCP save_memory
  - `source_type='mcp_agent'`
  - `source_trust='assistant_inferred'`
  - `actor='claude_mcp'`
  - not allowed to write core_blocks directly

- Hermes conservative integration（M7 — Post Phase 0.5）
  - 5 restricted MCP tools：hermes_observe / hermes_propose_memory / hermes_search / hermes_get_recent / hermes_get_context
  - 3 new REST endpoints：POST /events / POST /candidates / POST /events/access-log
  - MCP mount：/hermes/mcp
  - 所有写入强制 `source_trust='assistant_inferred'` / `actor='hermes_agent'` / `source_type='hermes_agent'`（hermes_mcp.py hardcoded）
  - 只写 memory_events + memory_candidates（status=pending），不直接写 memories / core_blocks
  - 读取默认排除 sealed + restricted（EXCLUDE_PRIVACY = "sealed,restricted"）
  - **Hermes get_context 只注入 whitelist core blocks：response_policy + active_projects**
  - **health_baseline / relationship_context / test.block 不注入 Hermes default context**
  - **Hermes health context must use a future dedicated health path（hermes_get_health_context 或 intent='health'），不是扩展 default whitelist**
  - tag：`phase-0.5-hermes-conservative`
  - security review：7/7 PASS（WARN #5 已修复）

### Core memory

`__BOT_PERSONA__` 已迁移：

```text
memories.__BOT_PERSONA__
  → core_blocks.response_policy
```

旧记录状态：

```text
status='archived'
memory_type='core_legacy'
```

`get_persona()` 已经优先读：

```text
GET /core-blocks/response_policy
```

失败时 fallback 到旧 memories 查询。

core block 机制：

- versioned
- `approval_status='approved'`
- old versions use `superseded_at`
- migration is idempotent

当前可注入 core blocks：

- `response_policy`
- `active_projects`

明确不注入：

- `test.block`
- `health_baseline`
- `relationship_context`

Hermes get_context 遵循相同 whitelist（HERMES_CORE_WHITELIST = {response_policy, active_projects}），health context 需走 future dedicated path。

### 读取侧

chat context injection 已经写 `memory_access_log`。

记录字段包括：

- actor
- retrieval_mode
- intent
- query_text
- legacy_memory_ids
- memory_ids
- core_block_keys
- session_id

`/v1/chat/completions` 和 Telegram bot 都已验证会写 access log。

## 当前已知技术债

这些还没有实现，不要误判成已完成：

- legacy `memories` 仍是主 committed memory layer
- `memory_items` 尚未正式接管
- `memory_candidates` 只是 shadow/proposal layer
- full candidate resolver 未实现
- privacy-gated retrieval 未实现
- actor_scope 尚未真正强制过滤
- intent classifier 未实现，当前 intent 基本是 `chat`
- hybrid search / RRF / rerank 未实现
- review queue / review UI 未实现
- embedding migration 未实现
- health pipeline 只是补 provenance，未重构成 health_observations + summaries
- WeChat full import 未开始
- eval set 未建立

## 下一阶段：Phase 1 Planning

不要直接开始大改。先做 Phase 1 计划。

Phase 1 推荐方向：

1. candidate resolver
2. memory_items activation
3. privacy-gated retrieval
4. review queue for pending candidates
5. memory_type cleanup
6. basic policy rules
7. minimal eval set

Phase 1 暂时不要做：

- SpiceDB / ReBAC
- AGM graph conflict resolver
- WeChat 全量导入
- Elasticsearch / OpenSearch
- 复杂 Web UI
- fine-tuning / training export
- multi-agent full autonomous write permissions

## 工作方式要求

继续保持小步实施：

```text
plan
→ one small milestone
→ code
→ verify with SQL / endpoint test
→ review
→ only then continue
```

不要一次性改多个系统。

每一步必须明确：

- 改哪些文件
- 不改哪些文件
- 验收 SQL / API 测试
- 回滚风险
- 是否影响现有 bot/API

当前最推荐的下一步：

先写 `docs/PHASE_1_PLAN.md`，不要先改代码。

Phase 1 plan 应该重点回答：

- memory_candidates 如何进入 memory_items
- 什么 source_trust 可以 auto-commit
- assistant_inferred 为什么默认 pending
- privacy_level / actor_scope 如何先做最小过滤
- legacy memories 如何逐步迁移
- review queue 最小实现是 SQL/CLI 还是 API
- eval set 最小 10 条怎么设计

## 下次使用方式

开新会话后直接说：

```text
继续我的长期个人 AI 记忆系统项目。下面是 handoff prompt：
[粘贴本文件全文]
```

建议把本文件放到项目中：

```text
docs/NEXT_SESSION_PROMPT.md
```
