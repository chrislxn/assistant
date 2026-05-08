# STATUS — 最后更新：2026-05-08

## 当前阶段
**Phase 1.0 Memory Lifecycle closure completed** — candidate resolver + memory_items + review queue API 全部就绪。
Phase 0.5 completed / sealed；Hermes conservative integration completed。

## 当前系统状态
- memory_items (UUID) 作为新 committed memory 层，memories 兼容双写
- resolve_candidate() 实现 auto-commit / keep_pending / requires_review 三条路径
- review queue API 4 端点可用（list / detail / commit / reject）
- 所有新写入均带 source_trust / source_event_ids / privacy_level / memory_type
- core memory 已从 memories 表独立为版本化 core_blocks
- 读写路径均有 access log；外部 agent 写入不绕过 provenance 规则

## 已完成模块

### M1 — Schema Foundation
- 4 张新表：memory_events、memory_candidates、core_blocks、memory_access_log
- memories 表 8 列扩展：status / privacy_level / actor_scope / source_trust / source_event_ids / subject_key / predicate_key / confidence
- memory_type DEFAULT 改为 'legacy'
- source_trust 回填 117 条（system_generated 69 / user_direct 42 / assistant_inferred 6）

### M2 — Database Helper Functions
- append_event() — append-only event 写入，支持 idempotency_key
- create_candidate() — memory_candidates 写入，pending_auto / pending 分流
- auto_commit_candidate() — user_direct / system_generated 自动提交到 memories
- get_active_core_block() — 查最新 approved active core block
- create_core_block_version() — 版本化更新，旧版本 superseded_at=NOW()
- log_memory_access() — fire-and-forget 审计日志
- migrate_persona_to_core_block() — __BOT_PERSONA__ → core_blocks.response_policy

### M3 — Write Pipeline Provenance
- M3.1: POST /debug/memories 手动保存路径 → append_event + source_event_ids
- M3.2: POST /data/health 健康数据路径 → append_event + source_trust=system_generated
- M3.3: AI 自动提取路径 → memory_events + memory_candidates + provenance

### M4 — Core Blocks + Persona Migration
- M4.1: core-blocks CRUD API（GET /core-blocks、GET /core-blocks/{key}、POST /core-blocks/{key}）
- M4.2: __BOT_PERSONA__ → core_blocks.response_policy 启动迁移（幂等）
- M4.3: get_persona() 改为优先读 core_blocks.response_policy，fallback memories

### M5 — Context Injection + Access Logging
- M5.1: context injection 加入 core_blocks（白名单：response_policy + active_projects）
- M5.2: memory_access_log 写入（api_client + telegram_bot 双入口）

### M6 — MCP External Entry Provenance
- M6.1: mcp_server.py save_memory 写入 source_trust=assistant_inferred / source_type=mcp_agent / actor=claude_mcp
- MCP 不允许直接写 core_blocks

### M7 — Hermes Conservative Integration（受限 Agent 接入）
- 新增 `kiwi-mem/hermes_mcp.py`：5 个受限工具（hermes_observe / hermes_propose_memory / hermes_search / hermes_get_recent / hermes_get_context）
- 新增 3 个 REST 端点：POST /events（append_event 写入观察）、POST /candidates（memory_candidates 提案）、POST /events/access-log（审计日志）
- GET /debug/memories 新增 `exclude_privacy` 参数，支持排除 sealed/restricted
- Hermes MCP 挂载路径：/hermes/mcp
- Security review 结论：7 PASS / 1 WARN（后修复），0 FAIL

### M7.1 — Hermes Core Block Whitelist（Security WARN #5 修复）
- Hermes get_context 只注入 core block whitelist：**response_policy + active_projects**
- Hermes **不注入**所有 approved core_blocks：health_baseline / relationship_context / test.block 不在默认 Hermes context 中
- 未来如需健康上下文，应新增 `hermes_get_health_context` 或 intent='health' 专用路径，而非扩展默认 whitelist
- HERMES_CORE_WHITELIST 定义在 `hermes_mcp.py`，与 main chat path 白名单（main.py:472）保持同步

### Phase 1.0 M1 — memory_items Schema
- 建表 `memory_items`（21 columns, UUID PK, 6 indexes, 2 FKs, 不含 embedding）
- helper functions：`insert_memory_item()`, `get_memory_item()`
- 不改变任何现有写入/检索路径

### Phase 1.0 M2 — Candidate Resolver
- `resolve_candidate()` 实现 auto_commit / keep_pending / requires_review 规则
- auto_commit 双写 memory_items + memories (compat)
- conflict detection + supersede 安全（transaction 包裹）
- assistant_inferred 永远 keep_pending；user_direct + low-risk → auto_commit
- health_pattern / health_baseline / high-stakes types → requires_review
- scripts/test_resolver_scenarios.py（28 assertions, 7 scenarios）
- `force_commit=True` 参数预留给 M3 review queue

### Phase 1.0 M3 — Review Queue API
- 4 端点：`GET /admin/candidates` (list), `GET /admin/candidates/{id}` (detail), `POST .../commit`, `POST .../reject`
- `list_candidates()`, `get_candidate()`, `reject_candidate()` helpers
- `force_commit=True` 人工批准路径（跳过自动 guard，保留 transaction + conflict/supersede）
- 状态保护：committed → recommit/reject 拒绝；rejected → commit 拒绝；re-reject 幂等
- HTTP 状态码：404/409/400 按 error reason 分流
- 所有 admin endpoints 受 `AdminAuthMiddleware` 保护

## 最终验证
Phase 0.5 回归验证：**10/10 全通过**
Hermes integration 验证：**6/6 全通过**（events / candidates / health / access_log / core_blocks / privacy filter）
Security review 验证：**test.block 过滤通过**（Hermes context 只含 response_policy + active_projects）
Phase 1.0 M1 验证：**schema + helpers + regression 全通过**
Phase 1.0 M2 验证：**28/28 resolver scenario tests + regression 全通过**
Phase 1.0 M3 验证：**15/15 review queue API tests + status guards + regression 全通过**

## 下一阶段
**Phase 1.0 Memory Lifecycle closure — completed.**
按照 PHASE_1_PLAN.md §4 实施顺序，M1/M2/M3 全部完成后进入观察期（建议 1-2 周）。
观察期通过后进入 Phase 1.1 — Retrieval Safety（privacy-gated retrieval + eval set）。

Phase 1.1 推荐方向：
- privacy-gated retrieval（SQL 层，actor privacy matrix）
- minimal eval set（10-15 条，positive retrieval + negative leakage）
- eval runner + seed data

Phase 1.2（后续）：
- memory_type cleanup（减少 legacy 比例）
- basic policy rules 抽离

---

## 关键决策记录
- memory_candidates 纳入 Phase 0.5，assistant_inferred → pending 不自动提交
- core_blocks 独立版本化，create_core_block_version()，旧版本 superseded 不覆盖
- idempotency_key：partial unique index(source_type, key) WHERE NOT NULL
- 所有写入失败（event/candidate）非致命，catch + print，不中断主流程
- Low-risk additive migration，执行前必须 pg_dump
- core-blocks API 受 AdminAuthMiddleware 保护（加入 PROTECTED_PREFIXES）
- create_core_block_version() 新增 approved_by 参数
- __BOT_PERSONA__ 迁移后标记 archived + core_legacy，不删除
- get_persona() 读 core_blocks 优先，5 分钟 TTL 缓存不变
- context injection 白名单：response_policy + active_projects；test.block / health_baseline / relationship_context 不注入
- bot 传 skip_core_blocks=True 避免 kiwi-mem 侧重复注入
- logging fire-and-forget；失败 catch + warning，不影响聊天回复
- bot 侧 legacy_memory_ids 暂传空数组（HTTP API 返回格式化文本，无 IDs）
- Hermes MCP 所有写入强制 source_trust=assistant_inferred / actor=hermes_agent / source_type=hermes_agent（由 hermes_mcp.py 硬编码，不信任外部输入）
- Hermes 写入走 POST /events（append-only event）+ POST /candidates（status=pending），不直接写 memories / core_blocks
- Hermes 读取默认排除了 sealed + restricted（EXCLUDE_PRIVACY = "sealed,restricted"）
- Hermes get_context core block whitelist = {response_policy, active_projects}；health_baseline / relationship_context 不属于默认 Hermes context
- 未来健康上下文需新增 hermes_get_health_context 或 intent='health' 专用路径

## 读这里开始下一个 session
CONTEXT.md → logs/2026-05-07.md → logs/2026-05-08.md → 本文件 → ARCHITECTURE.md → PHASE_0_5_SUMMARY.md
