# Phase 0.5 Summary

> 封版日期：2026-05-08
> 状态：**completed / sealed**
> 下一阶段：Phase 1 planning（尚未开始）

---

## 1. Executive Summary

Phase 0.5 的目的是：

- **不追求一步到位**，不重写 kiwi-mem
- **先停止最危险的长期设计债**：单表记忆、AI 直写、core memory 靠 importance 排序、无 provenance、无审计
- 让所有新写入带 source_trust / source_event_ids / privacy_level / memory_type
- 让 core memory（response_policy、active_projects）从普通 memories 表独立出来，版本化管理
- 让 context injection 有 access log，可审计
- 保持旧系统兼容，不破坏现有 API 和 bot 行为

**一句话总结：**

> Phase 0.5 upgraded kiwi-mem from a simple memory plugin into the foundation of a long-term personal AI memory system — with provenance, versioned core blocks, whitelist-based core context injection, and access auditing.

---

## 2. Completed Milestones

### M1 — Schema Foundation

**新增 4 张表：**

| 表名 | 用途 | 性质 |
|------|------|------|
| `memory_events` | append-only 原始事件层 | 不可覆盖 |
| `memory_candidates` | AI 提取提案层 | proposal/shadow，非 truth |
| `core_blocks` | 版本化核心记忆 | curated, versioned, approval-gated |
| `memory_access_log` | 检索审计日志 | read audit |

**memories 表新增 9 列/变更：**

- `memory_type` — DEFAULT 改为 `'legacy'`（旧值 fragment/daily_digest/digested 保留）
- `status` — 生命周期状态
- `privacy_level` — 隐私级别
- `actor_scope` — 可见 actor 范围，默认 `'{local_bot,claude_mcp}'`
- `source_trust` — 来源可信度
- `source_event_ids` — 追溯到 raw events
- `subject_key` — 结构化主题键
- `predicate_key` — 结构化谓词键
- `confidence` — AI 置信度

**source_trust 回填：**

迁移时对 117 条已有记忆按 `source` 列推断回填：
- `system_generated`：69 条
- `user_direct`：42 条
- `assistant_inferred`：6 条
- 回填后无 `unknown` 残留

### M2 — Database Helper Functions

新增 7 个函数（`database.py`）：

| 函数 | 职责 |
|------|------|
| `append_event()` | 写 memory_events，返回 event_id (UUID)，支持 idempotency_key 幂等 |
| `create_candidate()` | 写 memory_candidates，pending_auto / pending 分流 |
| `auto_commit_candidate()` | v0.5 轻量自动提交辅助函数，仅用于 user_direct / system_generated 等低风险来源；不等同于完整 Phase 1 resolver。assistant_inferred 仍保持 pending |
| `get_active_core_block()` | 查最新 approved active core block |
| `create_core_block_version()` | 版本化更新，旧版本 superseded_at=NOW() |
| `log_memory_access()` | fire-and-forget 审计日志 |
| `migrate_persona_to_core_block()` | \_\_BOT_PERSONA\_\_ → core_blocks.response_policy 迁移（幂等） |

`save_memory()` 签名扩展：新增 `source_trust` / `source_event_ids` / `privacy_level` / `memory_type` 四个参数，均有向后兼容默认值。

### M3 — Write Pipeline Provenance

**三条写入路径全部接入 provenance：**

#### M3.1 — 手动保存（`POST /debug/memories`）
```
用户请求 → append_event(event_type='manual_note', source_trust='user_direct')
         → save_memory(source_event_ids=[event_id])
         → 响应含 event_id
```

#### M3.2 — 健康数据（`POST /data/health`）
```
iPhone → raw_health_data（原始存档）
       → append_event(event_type='health_data', source_trust='system_generated',
                       privacy_level='sensitive', actor='health_pipeline')
       → memories / candidates with provenance
```

#### M3.3 — AI 自动提取
```
对话 → append_event(event_type='chat_message', source_trust='assistant_inferred')
     → create_candidate(status='pending')  ← 不自动提交
     → legacy memories with source_event_ids + provenance
```

**关键规则：** AI 提取的 candidate 全部 `status='pending'`，不自动提交为 committed memory。Resolver 留给 Phase 1。

### M4 — Core Blocks and Persona Migration

**新增 core-blocks API：**

| 端点 | 方法 | 说明 |
|------|------|------|
| `/core-blocks` | GET | 列出所有 active approved core blocks |
| `/core-blocks/{block_key}` | GET | 查单个 active approved block，不存在返回 404 |
| `/core-blocks/{block_key}` | POST | 创建新版本（旧版本 superseded_at=NOW()） |

- 端点受 `AdminAuthMiddleware` 保护（`PROTECTED_PREFIXES` 包含 `/core-blocks`）
- GET 只返回 `approval_status='approved' AND superseded_at IS NULL` 的最新版本
- POST 每次调用创建新版本，不原地覆盖

**\_\_BOT_PERSONA\_\_ 迁移：**

- 原 `memories` 中 `title='__BOT_PERSONA__'` 的记忆 → `core_blocks.response_policy`（version 1, approved）
- 原 memory 标记 `status='archived'`, `memory_type='core_legacy'`，不删除
- 迁移幂等：重启多次只产生 1 个 active response_policy
- `get_persona()` 优先读 `GET /core-blocks/response_policy`，404 或空内容 → fallback 旧 memories 查询
- 5 分钟 TTL 缓存不变

### M5 — Context Injection and Access Logging

**Context injection 白名单：**

```
chat 请求
  → 注入 core_blocks: response_policy + active_projects
  → 不注入: test.block / health_baseline / relationship_context
  → 注入 legacy memories（热记忆全文 + 相关记忆向量搜索）
  → 记录 memory_access_log
```

- Bot 侧传 `skip_core_blocks=True` 避免 kiwi-mem 重复注入 response_policy（bot 已通过 get_persona() 自行注入）
- Bot 侧单独请求 `GET /core-blocks/active_projects`，存在则注入

**Access log 双入口：**

| 入口 | actor | retrieval_mode | session_id |
|------|-------|----------------|------------|
| `/v1/chat/completions` | `api_client` | `chat_completions` | 自动生成 UUID |
| Telegram Bot | `telegram_bot` | `telegram_bot` | `CHAT_ID` |

每条 log 记录：`actor` / `retrieval_mode` / `intent` / `query_text` / `legacy_memory_ids` / `memory_ids` / `core_block_keys` / `session_id`

Logging 为 fire-and-forget（`asyncio.create_task`），失败 catch + warning，不中断聊天回复。

### M6 — MCP External Entry Provenance

**MCP `save_memory` 工具写入规则：**

- 调用 `POST /debug/memories`，传入 provenance 字段：
  - `source_trust='assistant_inferred'`
  - `source_type='mcp_agent'`
  - `actor='claude_mcp'`
  - `memory_type='legacy'`
  - `privacy_level='personal'`
- 对应 `memories` 记录有 `source_event_ids`
- MCP 不直接写 core_blocks，不走数据库直写

**`/debug/memories` 端点改造：**

- 新增可选参数 `source_trust` / `source_type` / `actor` / `memory_type` / `privacy_level`
- 默认值保持旧行为（`user_direct` / `manual` / `user` / `legacy` / `personal`），向后兼容

---

## 3. Final Validation

**Phase 0.5 回归验证 — 10/10 全部通过：**

| # | 检查项 | 结果 |
|---|--------|------|
| 1 | `/debug/memories` 新写入 → manual_note event | ✅ manual / user_direct / user |
| 2 | `/data/health` 新写入 → health_data event | ✅ health_pipeline / system_generated |
| 3 | AI 自动提取 → chat_message event + candidate | ✅ conversation / assistant_inferred / pending |
| 4 | MCP save_memory → mcp_agent event | ✅ mcp_agent / assistant_inferred / claude_mcp |
| 5 | core_blocks.response_policy 只有 1 个 active | ✅ version 1, approved |
| 6 | \_\_BOT_PERSONA\_\_ archived | ✅ archived / core_legacy |
| 7 | active_projects 能注入 | ✅ 在 core_block_keys 中 |
| 8 | test.block 不注入 | ✅ 不在 core_block_keys 中 |
| 9 | `/v1/chat/completions` 写 access_log | ✅ api_client / 38 IDs |
| 10 | telegram_bot 写 access_log | ✅ 全部 7 字段正确 |

Code review 两轮通过（ai/review-sonnet），non-blocking issues 已修复。

---

## 4. Current Data Flow

### Write Paths

**Manual write:**
```
POST /debug/memories
  → append_event(event_type='manual_note', source_trust='user_direct')
  → save_memory(source_event_ids=[event_id])
  → memories.source_event_ids 非空
```

**Health write:**
```
POST /data/health
  → raw_health_data（原始存档）
  → append_event(event_type='health_data', source_trust='system_generated',
                  privacy_level='sensitive', actor='health_pipeline')
  → memories with source_event_ids + memory_type='health_observation_summary'
```

**AI extraction:**
```
chat/session
  → append_event(event_type='chat_message'/'chat_memory',
                  source_trust='assistant_inferred')
  → create_candidate(status='pending')
  → legacy memories with provenance
```

**MCP write:**
```
claude_mcp save_memory tool
  → POST /debug/memories (source_type='mcp_agent',
     source_trust='assistant_inferred', actor='claude_mcp')
  → append_event(...)
  → memories.source_event_ids 非空
```

### Read Path

```
chat request
  → load approved core_blocks (whitelist: response_policy + active_projects)
  → retrieve legacy memories (heat decay + vector search)
  → inject into system prompt
  → log_memory_access() fire-and-forget
  → return response
```

---

## 5. Known Technical Debt

以下为 Phase 0.5 封版时明确记录的技术债，Phase 1 应逐步处理：

| 项目 | 说明 |
|------|------|
| **Legacy memories 仍是主 committed layer** | `memory_items` 尚未建立，当前 committed memory 仍在 `memories` 表中；Phase 1 需要正式引入 `memory_items`（UUID）作为新 committed 层 |
| **Candidate resolver 未实现** | `memory_candidates` 目前是 shadow/proposal 层，`assistant_inferred` 全部 pending，无自动提交/冲突检测 |
| **Privacy-gated retrieval 未实现** | `privacy_level` 和 `actor_scope` 字段已存在，但检索时不根据这两个字段过滤 |
| **Intent classifier 未实现** | 所有 chat 请求统一用 `intent='chat'`，无差异化注入策略 |
| **Hybrid search / RRF / rerank 未实现** | 检索仍为 heat decay + 向量搜索，无 FTS、结构化查找、融合排序 |
| **Review UI 未实现** | 管理面板只有 core-blocks CRUD API，无 pending candidates 审核界面 |
| **Embedding migration 未实现** | embedding 模型更换路径未建立（shadow deployment、dual-read、eval） |
| **Health pipeline 仅补 provenance** | 尚未重构为 health_observations + summaries 分层架构 |
| **Bot legacy_memory_ids 可能不完整** | Bot 侧通过 HTTP API 获取搜索结果（已格式化为文本），无法提取实际 memory IDs，暂传空数组 |
| **旧记录 source_event_ids 为空** | Phase 0.5 之前的数据无 provenance，这是预期行为，不可逆 |
| **自动化测试覆盖不足** | 当前验证全部为手工测试，`build_system_prompt_with_memories()`、`create_core_block_version()` 等函数缺少单元测试 |

---

## 6. Explicitly Out of Scope for Phase 0.5

以下功能**明确不在 Phase 0.5 范围内**，禁止当作已实现：

- full memory_items migration（尚未建立）
- candidate resolver / conflict detector
- privacy-gated retrieval（字段已建，过滤未实现）
- intent-aware retrieval
- hybrid search (FTS + vector + structured)
- RRF fusion / reranker
- AGM graph-based conflict resolver
- SpiceDB / ReBAC
- WeChat full import
- complex review UI
- training export pipeline
- embedding shadow migration
- health summary tiered architecture

---

## 7. Recommended Phase 1 Direction

Phase 1 推荐按以下优先级推进（**尚未开始**）：

1. **Candidate resolver** — 最关键的缺失环节。实现 auto-commit rules（user_direct / system_generated → commit；assistant_inferred → pending → review）、dedup、basic conflict detection（same subject_key + predicate_key）
2. **Memory_items activation** — 新 committed memory 写入 `memory_items`（UUID），逐步减少对 legacy `memories` 表的依赖
3. **Privacy-gated retrieval** — 检索时根据 `privacy_level` + `actor_scope` 过滤，敏感记忆不注入无关 chat
4. **Review queue** — 管理面板或 CLI 方式审核 pending candidates
5. **Memory_type cleanup** — 清理 `legacy` 和 `fragment` 类型，迁移到 CONTEXT.md 分类法（identity_fact / preference / project_state 等）
6. **Basic policy rules** — `memory_policy_rules` 表 + 简单规则引擎（source_trust ≥ threshold → auto-commit 等）
7. **Minimal eval set** — 10-20 条标注查询，覆盖 recall@10 / precision@10 / sensitive_leak_count

**Phase 1 仍然不要：**
- 上复杂图数据库
- 上 SpiceDB / ReBAC
- 上 Elasticsearch / OpenSearch
- WeChat 全量导入
- 多 agent 自主写权限
- 从聊天历史 fine-tune 模型
