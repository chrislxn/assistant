# STATUS — 最后更新：2026-05-09

## 当前阶段
**Phase 1.5 in progress — M2 + M3a + M3b completed**（31/31 + 24/24 + 17/17 PASS）。
Phase 1.5-M3b completed — short_term_auto_write → observation_only in resolver (17/17 PASS)。
Phase 1.0/1.1/1.2/1.3/1.4 completed / sealed；Hermes conservative integration completed。

## 当前系统状态
- **Primary retrieval source**: legacy `memories` 表；`memory_items` 是 committed memory 主表 / lifecycle target，retrieval helpers 已存在但仅用于 shadow/eval，未接入生产检索
- memory_items (UUID) 作为新 committed memory 层，memories 兼容双写；Phase 1.4 已实现 shadow retrieval + comparison eval（14/14 PASS, mismatch=0）
- resolve_candidate() 实现 auto-commit / keep_pending / requires_review 三条路径
- review queue API 4 端点可用（list / detail / commit / reject）
- 所有新写入均带 source_trust / source_event_ids / privacy_level / memory_type
- core memory 已从 memories 表独立为版本化 core_blocks
- 读写路径均有 access log；外部 agent 写入不绕过 provenance 规则
- Phase 1.1: legacy retrieval 已有 SQL-layer actor privacy gate；所有 internal read path 显式传 actor="local_bot"

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

**Hermes Agent 双路径读取模型：**
- **Health Path**: `health-db` MCP → PostgreSQL 直连 (hermes_readonly, SELECT only) → `health_summary` + `raw_health_data`
- **Memory Path**: `hermes_mcp.py` → kiwi-mem HTTP → filtered memories + whitelisted core_blocks
- 两条路径独立，不可混淆。Health access 不是 memory context access。

Memory Path 具体交付：
- 新增 `kiwi-mem/hermes_mcp.py`：5 个受限工具（hermes_observe / hermes_propose_memory / hermes_search / hermes_get_recent / hermes_get_context）
- 新增 3 个 REST 端点：POST /events（append_event 写入观察）、POST /candidates（memory_candidates 提案）、POST /events/access-log（审计日志）
- GET /debug/memories 新增 `exclude_privacy` 参数，支持排除 sealed/restricted
- Hermes MCP 挂载路径：/hermes/mcp
- Security review 结论：7 PASS / 1 WARN（后修复），0 FAIL

### M7.1 — Hermes Core Block Whitelist（Security WARN #5 修复）
- Hermes get_context 只注入 core block whitelist：**response_policy + active_projects**
- Hermes **不注入**所有 approved core_blocks：health_baseline / relationship_context / test.block 不在默认 Hermes context 中
- 未来如需健康上下文注入 memory path，应新增 `hermes_get_health_context` 或 intent='health' 专用路径，而非扩展默认 whitelist
- HERMES_CORE_WHITELIST 定义在 `hermes_mcp.py`，与 main chat path 白名单（main.py:472）保持同步

### M7.2 — Hermes Health-DB Access Hardening
- health-db MCP 连接字符串改为 `hermes_readonly` 只读用户（替换原 `kiwi` 主账号）
- `hermes_readonly` 仅持有 `health_summary` + `raw_health_data` 的 SELECT 权限
- 明确 REVOKE: memories, memory_items, memory_candidates, core_blocks, memory_events, memory_access_log
- Hermes 不能通过 health-db 通道读取记忆表，不能写入任何表
- 密码保存于 `~/.hermes/.env`（`HERMES_DB_READONLY_PASSWORD`）

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

### Phase 1.0 M8 — Hermes → Phase 1.0 Lifecycle 端到端验证
- **场景**：hermes_propose_memory → memory_candidates (pending) → admin review API → commit → memory_items + legacy memories
- **验证通过**：
  - hermes_propose_memory 只写 memory_candidates (status=pending)，不自动写 memory_items / memories
  - admin review API (list/detail/commit) 完整可操作 Hermes-origin candidates
  - resolve_candidate(force_commit=True) → dual-write: memory_items (UUID, source_candidate_id) + legacy memories (source='candidate_commit')
  - 三表 (candidates / memory_items / memories) source_trust / privacy_level / actor_scope 一致
  - 重复 commit → HTTP 409 "already committed"，不重复写行
  - Hermes 仍不能写 core_blocks 或直接写 committed memory（provenance 约束保持）
  - 测试数据 SQL 清理干净，回归通过
- **结果：25/25 PASS，0 WARN，0 FAIL** — Hermes conservative memory path 完整进入 Phase 1.0 生命周期，不需要代码改动

### Phase 1.1-M1 — Privacy Policy Helper

- 新增 `get_allowed_privacy_levels(actor: str) -> list[str]`
- actor → allowed privacy_levels 映射表 (`_PRIVACY_POLICY`)
- `_DEFAULT_PRIVACY` = `["public_like", "personal"]`（unknown actor fallback）
- `sealed` 永远不被任何 actor 返回
- 不做 actor_scope 过滤（留给后续）
- 不做 retrieval SQL 变更

### Phase 1.1-M2 — Legacy Memories Retrieval Privacy Gate

- **SQL-layer filtering**：`COALESCE(m.privacy_level, 'personal') = ANY(allowed::text[])` 应用于所有 legacy memories 检索路径
- **Retrieval 函数变更**：search_memories / _vector_search / _keyword_search / get_recent_memories / /debug/memories (含 title 路径) 全接入 actor gate
- **Actor 调用方传参**：
  - /v1/chat/completions → actor="api_client"
  - Telegram bot (search_memory, get_persona) → actor="telegram_bot"
  - Hermes MCP (4 calls) → actor="hermes_agent" (HERMES_ACTOR)
  - /debug/memories → actor query param，默认 "local_bot"
  - AI extraction / dedup (内部) → 默认 "local_bot"
- **Actor privacy matrix**（after BLOCK-1 fix）：
  - local_bot / api_client: public_like, personal, sensitive, restricted
  - telegram_bot: public_like, personal, sensitive（不含 restricted）
  - claude_mcp: public_like, personal, sensitive
  - hermes_agent / dev_agent / unknown: public_like, personal
  - sealed: never returned
- **exclude_privacy 交集语义**：`= ANY(allowed) AND != ALL(excluded)`（SQL 层取交集，非覆盖）
- **Bug fix (BLOCK-2)**：/debug/memories title + exclude_privacy 参数索引错误修复（${i+4}→${i+3}, LIMIT $3→$limit_idx）
- **WARN-4 注释**：Hermes EXCLUDE_PRIVACY=sealed,restricted 保留为二级防线；sensitive 排除完全依赖 actor gate
- Write path, resolver, core_blocks, health-db 全部未改
- Helper policy tests: 19/19 PASS
- Retrieval privacy gate tests: 25/25 PASS
- BLOCK-2 title+exclude 专项: 6/6 PASS

### Phase 1.1-M3 — Automated Retrieval Privacy Gate Test

- **新增 `scripts/test_privacy_gate_retrieval.py`**（447 行，stdlib only，无异步依赖）
- 通过 kiwi-mem HTTP API 端到端验证 legacy retrieval privacy gate：
  - `search_memories()` via `GET /debug/memories?q=`
  - `get_recent_memories()` via `GET /debug/memories` (no q)
  - `/debug/memories` title path via `GET /debug/memories?title=`
  - sealed global exclusion（7 actors × 2 paths）
  - actor privacy matrix（5 actors × 5 privacy levels × 3 paths）
  - `exclude_privacy` blocklist intersection（2 cases × 3 paths）
  - auto-cleanup with try/finally + fallback title search
- Token via `ACCESS_TOKEN` env var（不硬编码）
- **Bug fix**：`get_recent_memories` privacy/exclude 参数索引修复
  - category_id 分支：privacy `$2` / exclude `$3`（offset by category_id=`$1`）
  - non-category 分支：privacy `$1` / exclude `$2`（无 category_id 偏移）
  - 与 Phase 1.1-M2 中 `_vector_search` 同类修复（BLOCK-1 衍生）
- 测试结果：**109/109 PASS**（search 25 + recent 25 + title 25 + sealed 14 + exclude 15 + cleanup 5）
- 测试数据残留确认：0

### Phase 1.2-M1 — Test Helper Cleanup

- 移除 `_load_token_from_dotenv` 中硬编码路径 `/home/chris/assistant/.env`
- 移除 cleanup verification 中无实际作用的 dead code 空循环
- `recent_memories()` test helper 增加 `exclude_privacy` 参数，消除 exclude 交集测试中的裸 `_get` 调用
- 测试结果不变：109/109 PASS

### Phase 1.2-M2 — local_bot Full Positive Matrix Coverage

- `local_bot` 加入 test ACTORS dict（visible: public_like/personal/sensitive/restricted, hidden: sealed）
- 覆盖 search/recent/title 三路径
- 测试结果：109/109 → **124/124 PASS**

### Phase 1.2-M3 — Internal Retrieval Actor Boundary Audit

- 5 处 internal memory read path 显式传 `actor="local_bot"`：
  - `database.py` `check_memory_duplicate()` dedup search
  - `main.py` AI 提取对比 search + get_recent（×2 路径）
- 不改 SQL 语义、不改 retrieval policy
- **Deferred**: `get_recent_memories` 四分支（category_id × exclude_privacy）暂不重构 — 124/124 regression 已覆盖，避免引入新的参数索引风险
- Legacy `memories` 表仍是 primary retrieval source；`memory_items` 是 committed memory lifecycle target，尚未接入 retrieval
- 所有 internal read path 现在显式标注 actor boundary

### Phase 1.3-M1 — Eval Case Definitions

- 新增 `evals/retrieval_safety_minimal.jsonl`（10 cases, JSONL）
- Query-template format with placeholder expansion: `{anchor}` / `{<level>_tag}`
- 覆盖 5 actors × anchor query + 2 exclude_privacy intersection + 2 tag-specific + 1 sealed global
- All expected_visible / expected_hidden use real privacy level names

### Phase 1.3-M2 — Eval Runner

- 新增 `scripts/eval_retrieval_minimal.py`（303 lines, stdlib only）
- Standalone runner: reads JSONL, creates anchored test memories, runs query-based retrieval, outputs JSON summary
- Shared `kiwi_eval_alpha_<timestamp>` anchor across all 5 test memories
- Template expansion: `{anchor}` → shared anchor, `{<level>_tag}` → per-level tag
- Level tag matching via embedded `tag:<level_tag>` in content
- try/finally cleanup with fallback title search + residue verification
- Machine-readable JSON summary: total / passed / failed / leak_count / missing_expected_count / cases / cleanup_deleted / cleanup_remaining
- Exit 0 on all pass, exit 1 on any failure

### Phase 1.4-M1 — Design Document & Schema Audit

- 新增 `docs/MEMORY_ITEMS_RETRIEVAL_BRIDGE.md`（268 lines）
- Three bridge strategies compared: legacy-only (A), dual-read shadow (B), memory_items primary (C)
- **Selected Strategy B**: dual-read shadow mode
- `memories` remains primary retrieval source; `memory_items` retrieval is shadow/eval only
- No production retrieval switch in Phase 1.4
- Helper signatures defined: `search_memory_items()` / `get_recent_memory_items()`
- Safety invariants enumerated (9 rules, matching Phase 1.1)
- 12 explicit non-goals

### Phase 1.4-M2a — Shadow Retrieval Helpers

- `kiwi-mem/database.py`: 新增 `search_memory_items()` + `get_recent_memory_items()`（+144 lines）
- Both use `get_allowed_privacy_levels(actor)` — same Phase 1.1 `_PRIVACY_POLICY`
- SQL-layer privacy gate: `COALESCE(privacy_level, 'personal') = ANY(bind_param::text[])`
- `exclude_privacy` subtractive: `!= ALL(bind_param::text[])`
- Filters: `status = 'active'`, `(valid_to IS NULL OR valid_to > NOW())`
- Keyword-only search via `extract_search_keywords()` + `rendered_text ILIKE`
- Recent via `ORDER BY created_at DESC`
- No vector, no embedding, no RRF
- Return dict aligned to legacy format: `memory_id`, `id` (alias), `content` (mapped from `rendered_text`), `privacy_level`, `memory_type`, `subject_key`, `predicate_key`, `confidence`, `importance`, `created_at`, `source_candidate_id`
- **Zero production callers** — not wired to any endpoint or consumer

### Phase 1.4-M2b — Shadow Comparison Eval

- 新增 `scripts/eval_memory_items_shadow.py`（360 lines, stdlib + asyncpg）
- Direct paired INSERT into `memories` + `memory_items`（不经过 candidates/events/resolver/admin API）
- 比较 legacy retrieval 与 memory_items retrieval 的 privacy behavior
- 6 actors × search + 6 actors × recent + 2 exclude = **14 cases**
- 结果：**14/14 PASS**
  - `legacy_leak_count`: 0
  - `items_leak_count`: 0
  - `mismatch_count`: 0
- Cleanup: legacy 5 deleted / 0 remaining, items 5 deleted / 0 remaining
- Fallback cleanup by `subject_key LIKE 'eval:shadow:%'` + source/source_trust

### Phase 1.4-M1.5 — Documentation & Philosophy Layer

- 新增 `docs/VISION.md` — 长期愿景与设计哲学
  - Project vision: personal cognitive infrastructure, not chat archive
  - 9 core principles (raw event ≠ memory, retrieval safety > recall, forgetting is a feature, etc.)
  - 7-layer memory temporal model (raw event → transient emotional cache → ... → core blocks)
  - Forgetting / compression philosophy
  - Agent boundary philosophy (what AI can/cannot do; Hermes constraints)
  - UI philosophy (review-first, not dashboard-first)
  - Long-term directions (non-committed)
- 新增 `docs/KNOWN_RISKS.md` — 长期风险目录
  - 8 risk categories: emotional dependency, identity ossification, retrieval leakage, false memory, over-personalization, authority drift, relationship substitution, scope creep
  - Each with: description, why dangerous, current mitigation, unresolved gaps
  - Mitigation summary table (prevention/detection/correction)
- 更新 `docs/ARCHITECTURE.md`
  - 新增 §1.1 System Philosophy & Temporal Memory Model（引用 VISION.md / KNOWN_RISKS.md）
  - §7 新增: memory decay, emotional compression, reflection layer, temporal summarization, Dream v2, event graph
- 这是 **documentation / philosophy phase**，不是 production feature phase
- Zero production code changes，zero schema changes，zero API changes

## 最终验证
Phase 0.5 回归验证：**10/10 全通过**
Hermes integration 验证：**6/6 全通过**（events / candidates / health / access_log / core_blocks / privacy filter）
Security review 验证：**test.block 过滤通过**（Hermes context 只含 response_policy + active_projects）
Phase 1.0 M1 验证：**schema + helpers + regression 全通过**
Phase 1.0 M2 验证：**28/28 resolver scenario tests + regression 全通过**
Phase 1.0 M3 验证：**15/15 review queue API tests + status guards + regression 全通过**
Phase 1.0 M8 验证：**25/25 Hermes → Phase 1.0 lifecycle 端到端通过**
Phase 1.1-M1 验证：**19/19 helper policy tests + regression 全通过**
Phase 1.1-M2 验证：**25/25 retrieval gate + 6/6 title+exclude + 3/3 regression 全通过**
Phase 1.1-M3 验证：**109/109 automated retrieval gate test + 3/3 regression 全通过**
Phase 1.2-M1 验证：**109/109 regression unchanged after cleanup**
Phase 1.2-M2 验证：**124/124 local_bot +15 coverage**
Phase 1.2-M3 验证：**124/124 + 19/19, no SQL logic change**
Phase 1.3-M1 验证：**10 cases defined, all fields valid**
Phase 1.3-M2 验证：**10/10 PASS, leak_count=0, missing_expected_count=0**
Phase 1.4-M1 验证：**design doc reviewed, strategy B selected**
Phase 1.4-M2a 验证：**py_compile OK, 19/19 + 124/124 + 10/10 all PASS, legacy unchanged**
Phase 1.4-M2b 验证：**14/14 PASS, leak=0, mismatch=0, cleanup 5+5/0+0**
- Test data residue: memories 0 / memory_candidates 0 / memory_items 0
- memory_events: benign append-only eval/test provenance records (non-removable, acceptable)
- Production callers for new helpers: 0

### Phase 1.5-M2 — Dry-Run Candidate Review Policy Classifier

- `kiwi-mem/database.py`: 新增 `classify_candidate_review_policy(candidate: dict) -> dict`（pure function, zero side effects）
- `scripts/test_candidate_review_policy.py`: 31/31 PASS（stdlib only；含 B1/B2 fix: importance>=7 guard + source_trust allowlist）
- 7-gate rule-based baseline：high-stakes → manual_review, diagnosis → manual_review, negative inference → auto_reject, third-party inference → auto_reject, short-term types → short_term_auto_write, medium factual → auto_commit, default → keep_pending
- **Classifer is rule-based baseline only** — keyword/pattern matching, type gates, provenance gates
- **Not a production semantic judge** — keyword matching is brittle for Chinese expressions; durable emotional claims, implicit negative inferences, and ordinary-feeling-vs-hard-fact distinctions require semantic judgment beyond current rules
- **Zero resolver integration** — `resolve_candidate()` does not call this classifier; no candidate status changes; no auto_commit behavior changes
- **AI-assisted semantic triage required before production enablement** — future M3/M4 must add AI triage for semantic classification, structured JSON output, and eval coverage before enabling any automatic write behavior
- AI triage responsibilities: classify candidate type, estimate factuality/durability/usefulness/risk/recurrence/stability, produce structured recommendation with explanation
- AI triage constraints: must NOT commit high-risk memory, update core_blocks, infer relationship status from ordinary feelings, infer ability/personality from grades, diagnose health/mental health, or bypass policy resolver
- Final decision architecture: `hard rules + AI triage + policy resolver`

### Phase 1.5-M3a — Observation-Only Candidate Admin Support

- **Schema**: `memory_candidates.valid_to` nullable TIMESTAMPTZ added（no backfill, no new table）
- **Admin queue**: default → `pending + requires_review`；`observation_only` / `expired` excluded by default
- **Explicit status queries**: `?status=observation_only`, `?status=expired`, comma-separated `?status=pending,requires_review`
- **Commit protection**: `POST /admin/candidates/{id}/commit` rejects `observation_only` (409) and `expired` (409)；guard before `resolve_candidate(force_commit=True)`
- **Boundaries**: no resolver integration；classifier not called by `resolve_candidate()`；no observation_only write path；no memory_items/memories write；no retrieval changes；no Hermes/Telegram/chat changes；no expiry script；no digest job
- **Tests**: `test_observation_m3a.py` 24/24 PASS（real DB candidates via asyncpg, HTTP admin API, full cleanup）
- **Commits**: `9361e2c`, `c983e63`

### Phase 1.5-M3b — Resolver: short_term_auto_write → observation_only

- `resolve_candidate()` 调用 `classify_candidate_review_policy()`，仅对 `current_status IN ('pending','pending_auto')` 且 `not force_commit`
- 仅启用 `recommended_action == "short_term_auto_write"`；所有其他 channel 保持现有行为
- `short_term_auto_write` → `status='observation_only'`, `valid_to = NOW() + ttl_d days`
- TTL whitelist: 7 / 14 / 30，fallback 14
- SQL: `NOW() + ($1::int * INTERVAL '1 day')`（参数化）
- **Safety guards**: committed / rejected / observation_only / expired 均为 terminal（不可被 resolve 或 force-commit）；`requires_review` 不降级
- **Boundaries**: 不写 `memory_items` / `memories`；不改 retrieval / Hermes / Telegram / chat；不改 core_blocks
- **Tests**: `test_observation_m3b.py` 17/17 PASS（9 个真实 candidate，覆盖 routing / high-stakes / terminal guard / cleanup）
- **Commit**: `059193f`

## 下一阶段
**Phase 1.4 Memory Items Retrieval Bridge — completed (M1/M1.5/M2a/M2b).**

Important boundaries:
- legacy `memories` is still primary retrieval source
- `memory_items` read path exists only for shadow/eval
- no production endpoint uses `memory_items` retrieval
- no Hermes / Telegram / chat completions integration
- no `memory_items`-only switch
- no migration from legacy `memories` to `memory_items`

Phase 1.5+ candidates:
- Phase 1.5-M2: dry-run classifier **completed**（31/31 PASS）
- Phase 1.5-M3a: observation-only admin support **completed**（24/24 PASS）
- Phase 1.5-M3b: resolver short_term_auto_write → observation_only **completed**（17/17 PASS）
- Phase 1.5: Candidate Review Policy & Short-Term Observation Layer（deferred — see `docs/PHASE_1_5_REQUIREMENTS.md` for full requirements）
- Phase 1.6: Provider Boundary & Local Model Routing（Provider Boundary Policy not implemented yet）
- Future: `memory_items` primary retrieval switch planning, only after more shadow/eval confidence

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
- Hermes 双路径读取模型：Health Path (health-db MCP, hermes_readonly, 直连 PostgreSQL) 与 Memory Path (hermes_mcp.py, kiwi-mem HTTP) 是两条独立通道，不可混淆
- Health access 不是 memory context access。Health path 专用于健康数据查询，不走记忆检索；Memory path 不读取 health_summary / raw_health_data
- Hermes 不能通过 health-db 通道读取 memories / core_blocks 等记忆表（REVOKE 执行）
- health-db 使用 hermes_readonly 只读用户，替换原 kiwi 主账号（M7.2）
- 未来健康上下文注入 memory path 需新增 hermes_get_health_context 或 intent='health' 专用路径，不应扩展 default Hermes context whitelist
- Phase 1.1：actor → privacy_level 映射使用 hard-coded `_PRIVACY_POLICY` dict，不做查表
- Phase 1.1-M1：不做 actor_scope 过滤，不做 retrieval SQL 变更
- Phase 1.1-M2：所有 legacy memories 检索路径必须在 SQL 层用 `COALESCE(privacy_level, 'personal') = ANY(bind_param::text[])` 过滤，不依赖 Python post-filter
- telegram_bot 不应读取 restricted 级别记忆（BLOCK-1 fix：PHASE_1_PLAN.md §2.3 明确规定）
- Hermes 的 sensitive 排除完全依赖 actor privacy gate（SQL 层）；EXCLUDE_PRIVACY=sealed,restricted 是二级 blocklist，不涵盖 sensitive
- /debug/memories title + exclude_privacy 路径参数索引：$1=title, $2=allowed_privacy, $3..$N=excluded_levels, $limit_idx=limit
- Phase 1.1-M2 不改 write path、resolver、core_blocks、health-db 只读配置
- Phase 1.1-M3：`get_recent_memories` privacy/exclude 参数索引必须 per-branch（category_id 分支使用 `$2`/`$3`，non-category 分支使用 `$1`/`$2`），与 `_vector_search` 修复模式一致
- Phase 1.1-M3：`ACCESS_TOKEN` 不硬编码在测试脚本中，通过 env var 传入
- Phase 1.1-M3：测试 memory 创建使用 POST 返回的 `memory_id` 字段直接获取 ID（避免 sealed 等高级别记忆因隐私门控在 title lookup 中不可见）
- Phase 1.1-M3：测试脚本 447 行，stdlib only（`urllib.request` + `json`），零外部依赖，可在任何 Python 3.x 环境运行
- Phase 1.2-M1：测试脚本不再包含硬编码文件路径；cleanup verification 移除空循环 dead code
- Phase 1.2-M2：`local_bot` 纳入 full actor privacy matrix regression（三路径 15 checks，109→124）
- Phase 1.2-M3：所有 internal memory read path 显式传 `actor="local_bot"`（5 处：database.py check_memory_duplicate + main.py AI 提取 ×2 路径 search/recent）
- Phase 1.2-M3：`get_recent_memories` 四分支（category_id × exclude_privacy）暂不重构 — 124/124 regression 已覆盖，避免引入新的参数索引风险（deferred）
- Phase 1.2：legacy `memories` 表仍是 primary retrieval source；`memory_items` 是 committed memory lifecycle target，尚未接入 primary retrieval
- Phase 1.3-M1：eval cases 使用 query_template + placeholder expansion（`{anchor}` / `{<level>_tag}`），由 runner 展开，不写死物理查询
- Phase 1.3-M2：eval runner 不 import test_privacy_gate_retrieval.py 或 database.py；stdlib only；tag matching 通过嵌入 content 的 `tag:<level_tag>` 实现
- Phase 1.3-M2：shared anchor 保证所有 5 条 test memory 同时被关键词搜索命中，actor privacy gate 决定实际可见性
- Phase 1.4-M1：Strategy B (dual-read shadow mode) selected — `memories` primary, `memory_items` shadow/eval only, no switch
- Phase 1.4-M2a：`search_memory_items()` / `get_recent_memory_items()` zero production callers；keyword-only (no embedding)；return dict aligned to legacy format
- Phase 1.4-M2a：privacy gate identical to Phase 1.1 — same `_PRIVACY_POLICY`, same `COALESCE`, same `!= ALL` for exclude, same sealed exclusion
- Phase 1.4-M1.5：VISION.md 确立 7-layer memory hierarchy，明确 emotional states ≠ identity；KNOWN_RISKS.md 记录 9 类长期风险（含 provider exposure）及当前 mitigation gap；ARCHITECTURE.md §1.1 引用哲学文档
- Phase 1.4-M2b：shadow comparison 验证 legacy 与 memory_items retrieval privacy behavior 完全一致（14/14, mismatch=0, leak=0）
- Phase 1.4：`memory_items` 检索路径已存在但仅为 shadow/eval；legacy `memories` 仍是唯一 primary retrieval source；无生产切换

> tag: phase-1.2-retrieval-cleanup
> tag: phase-1.3-minimal-eval
> tag: phase-1.4-memory-items-bridge

---

## Phase 1.5-M3a Hard Boundaries

M3a does NOT:
- call `classify_candidate_review_policy()` from `resolve_candidate()`
- enable `short_term_auto_write` write behavior
- create `observation_only` candidates automatically
- write `observation_only` into `memory_items`
- write `observation_only` into legacy `memories`
- expose `observation_only` to retrieval
- expose `observation_only` to Hermes / Telegram / chat completions
- implement expiry script (M3c)
- implement digest job (M5)
- enable `medium_factual_auto_commit` (M4)
- enable `auto_reject_or_expire` (deferred)

## 验证结果（2026-05-09 end-of-day）

| Test | Result |
|------|--------|
| `py_compile database.py` | OK |
| `py_compile main.py` | OK |
| `test_candidate_review_policy.py` | 31/31 PASS |
| `test_observation_m3a.py` | 24/24 PASS |
| `test_privacy_policy.py` | 19/19 PASS |
| `test_privacy_gate_retrieval.py` | 124/124 PASS |
| `eval_retrieval_minimal.py` | 10/10 PASS |
| `eval_memory_items_shadow.py` | 14/14 PASS |

## Immediate Next Step: Phase 1.5-M3c

Phase 1.5-M3c candidate — expiry script:
- `scripts/expire_observations.py`
- `observation_only` with `valid_to < NOW()` → `status='expired'`
- Preserve provenance (no deletion)
- No digest
- No retrieval
- No medium factual auto-commit

## Future Phase Candidates

**Phase 1.5-M3c** — expiry script:
- `scripts/expire_observations.py`
- `observation_only` + `valid_to < NOW()` → `status='expired'`
- Preserve provenance (no deletion)

**Phase 1.5-M4** — medium factual auto-commit:
- Only after more review and tests
- `source_trust`/provenance-gated
- No high-risk facts

**Phase 1.5-M5** — digest planning/prototype:
- Weekly `observation_only` → digest candidate
- Batch review, no direct commit

**Future AI-assisted triage**:
- Rule-based classifier is baseline only
- Production enablement needs AI semantic triage + eval coverage
- AI triage suggests；policy resolver decides

**Future Provider Boundary / Local Model Routing**:
- Cloud provider exposure documented (KNOWN_RISKS.md Risk 9)
- `sealed`/`restricted` local-only policy
- Local model routing before sending sensitive content to third-party LLM/embedding providers

## Future Codebase Cleanup Debt

`kiwi-mem/database.py` (~5000 lines) and `kiwi-mem/main.py` (~3100 lines) should eventually be split into focused modules. This is a no-behavior-change refactor — do NOT perform inside Phase 1.5 functional milestones.

**database.py proposed split**:
- `schema/` — migrations, init_tables
- `candidate_store.py` — memory_candidates CRUD
- `memory_item_store.py` — memory_items CRUD + retrieval
- `legacy_memory_store.py` — memories CRUD + retrieval
- `privacy_policy.py` — `get_allowed_privacy_levels()`, `_PRIVACY_POLICY`
- `candidate_policy.py` — `classify_candidate_review_policy()`
- `access_log.py` — `log_memory_access()`

**main.py proposed route split**:
- `routes/admin_candidates.py`
- `routes/core_blocks.py`
- `routes/debug_memory.py`
- `routes/chat.py`
- `routes/health.py`
- `routes/hermes.py`
- `app_startup.py`

## Tag Status

No new tag yet — Phase 1.5 is in progress (M1/M2/M3a done; M3b/M3c/M4/M5 pending).

## 读这里开始下一个 session
CONTEXT.md → logs/2026-05-07.md → logs/2026-05-08.md → 本文件 → ARCHITECTURE.md → PHASE_0_5_SUMMARY.md
