# STATUS — 最后更新：2026-05-08

## 当前阶段
**Phase 0.5 completed / sealed** — 系统已从单表记忆插件升级为带 provenance 的长期记忆基础设施

## 当前系统状态
所有新写入均带 source_trust / source_event_ids / privacy_level / memory_type；core memory 已从 memories 表独立为版本化 core_blocks；读写路径均有 access log；外部 agent 写入不绕过 provenance 规则。

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

## 最终验证
Phase 0.5 回归验证：**10/10 全通过**

## 下一阶段
**Phase 1 — planning（尚未开始）**

推荐方向：
- candidate resolver
- memory_items activation
- privacy-gated retrieval（基于 privacy_level + actor_scope）
- review queue（pending candidates）
- memory_type cleanup（减少 legacy 比例）
- basic policy rules
- minimal eval set

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

## 读这里开始下一个 session
CONTEXT.md → logs/2026-05-07.md → logs/2026-05-08.md → 本文件 → ARCHITECTURE.md → PHASE_0_5_SUMMARY.md
