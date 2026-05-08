# Long-Term Phases Roadmap

> 日期：2026-05-08
> 状态：**living document**
> 范围：Phase 0 — Phase 6 的阶段性愿景，不替代具体实施计划。

本文档回答一个问题：**Phase 1 为什么这样设计，以及它之后是什么。**

Phase 1 的详细实施计划见 `docs/PHASE_1_PLAN.md`。

---

## 0. Current Baseline

| 项 | 状态 |
|---|------|
| Phase 0.5 | completed / sealed |
| Hermes conservative integration | completed（`phase-0.5-hermes-conservative`） |
| Phase 1 planning | in progress |
| Current runtime | legacy kiwi-mem + long-term memory control plane |

**一句话：**

> Phase 0.5 built provenance, core blocks, candidate shadow layer, Hermes restricted integration, and access logging. Phase 1 will activate memory lifecycle and retrieval safety.

当前系统已经具备了 event-first 写入、provenance tracing、core block 版本化、MCP agent 接入和 read audit 的基础能力。但 committed memory 仍停留在 legacy `memories` 表，candidates 永远 pending，privacy 字段存在但检索不执行。

---

## 1. Phase 1 — Memory Lifecycle and Retrieval Safety

### 做

- candidate resolver（auto_commit / keep_pending / requires_review）
- memory_items activation（UUID-based committed memory，不含 embedding）
- minimal review queue（4 端点 API）
- privacy-gated retrieval（SQL 层，actor privacy matrix）
- minimal eval set（10-15 条，positive retrieval + negative leakage）
- memory_type cleanup（legacy / fragment 减少）
- basic policy rules（Python config 抽离）

### 不做

- hybrid retrieval
- RRF / reranker
- intent classifier
- WeChat full import
- embedding migration
- health_observations full rewrite
- cold archive
- restore test automation
- training export

### 设计钩子（为后续 Phase 预留）

| 当前决策 | 为未来预留什么 |
|---------|-------------|
| `memory_items` 不含 embedding | Phase 2 用独立 `memory_embeddings` 表 |
| `source_event_ids` 全程保留 | Phase 3 历史导入 + reprocessing 可重建 |
| `privacy_level` / `actor_scope` 字段存在 | Phase 2.5 intent-aware retrieval |
| `memory_candidates` shadow layer | Phase 2/3 不同 extractor 可并行提案 |
| eval set（Phase 1.1） | Phase 2+ 每次 retrieval 变更的回归基础 |
| `valid_from` / `valid_to`（Phase 1.0） | Phase 5 conflict graph 的时间维度基础 |

---

## 2. Phase 2 — Retrieval Quality and Embedding Architecture

**目标：** 从"安全可控检索"升级到"高质量可评估检索"。

### 做

- `memory_embeddings` 独立表：`embedding_model`, `model_revision`, `embedding_dim`, `content_hash`, `is_active`
- shadow embedding migration framework（新模型后台 re-embedding batch jobs，dual-read 或 shadow-read during migration，eval 比较后切换 active model）
- Embedding model upgrade does **not** require `memory_items` migration（表独立）
- pgvector index tuning（ivfflat → hnsw when cluster size grows beyond early thresholds）
- structured search + PostgreSQL FTS + pgvector vector search
- RRF fusion
- optional lightweight reranker
- `retrieval_explanation` 更完整（哪种检索路径贡献了哪些结果）
- eval set 扩展（从 15 条到 50+ 条）

### 不做

- 专用向量数据库（pgvector 仍是 v1 选择；Phase 4 或 later 在 real bottleneck 出现后再评估 Qdrant/Milvus）
- Elasticsearch / OpenSearch
- 把 embedding 塞进 `memory_items` 主表
- 修改 Phase 1 的 resolver / privacy gate 核心逻辑

### 依赖

- Phase 1 memory_items 稳定
- Phase 1.1 eval set 可重复运行（Phase 2 每次 retrieval 变更需要回归）

---

## 3. Phase 2.5 — Intent-Aware Context Orchestration

**目标：** 从单一 `chat` intent 升级到 intent-aware context injection。

### 做

- intent classifier（at least: `technical_debug`, `project_status`, `health_today`, `health_trend`, `relationship_context`, `daily_chat`, `public_writing`）
- context budget planner（按 intent 分配 token budget）
- actor + intent 双重 gating（不是 or，是 and）
- 不同 intent 的不同 memory policy：

  | intent | 注入的 core_blocks | 注入的 memory_type | 排除 |
  |--------|-------------------|-------------------|------|
  | `technical_debug` | active_projects, technical_environment | procedure, project_decision, device_inventory | health, relationship |
  | `health_today` | health_baseline | health_observation_summary, health_pattern | — |
  | `relationship_context` | relationship_context | relationship_context, episodic_event | health |
  | `public_writing` | — | public_like only | personal, sensitive, restricted, sealed |
  | `daily_chat` | response_policy | preference, episodic_event, project_state | health_pattern, relationship_context |

- Hermes health context 专用路径：`hermes_get_health_context` 或 `get_context(intent='health')`，**不**扩展 default Hermes whitelist

### 不做

- 复杂 NLU / 多轮意图追踪
- 自动意图推断（初期可显式传 intent）

### 强调

- 默认 Hermes context 不应自动读 `health_baseline` / `relationship_context`。
- 健康数据读取应通过明确 `health` intent 或专用 health agent。
- Phase 2.5 的 intent gating 叠加在 Phase 1.1 privacy gate 之上（两层过滤独立）。

### 依赖

- Phase 1.1 privacy gate 稳定（actor 参数传递统一）
- Phase 2 retrieval 路径统一（search / recent 都走同一隐私过滤）

---

## 4. Phase 3 — Historical Import and Third-Party Privacy

**目标：** 安全接入大规模历史数据，特别是微信历史。

### 做

- WeChat batch import pipeline
- raw events first（每条消息 → `memory_events`）
- `memory_events` time partitioning（年/月，为百万级 events 做准备）
- Cold archive manifest table（Parquet / JSONL export for aged events）
- contact / time / session / topic sessionization
- session-level summaries instead of per-message memory extraction
- low-confidence candidates by default（`source_trust='wechat_import'`, `confidence` 上限 0.6）
- third-party privacy marking（对方消息默认 `restricted`）
- de-identification / pseudonymization for future exports
- import-specific `memory_candidates` flag（`extractor_name='wechat_importer'`）
- Legacy `memories` retirement planning begins（when `memory_items` coverage confirmed > 95%）

### 不做

- 实时微信接入
- 第三方消息自由注入一般 context
- 从第三方消息推断用户身份/偏好
- fine-tuning from raw chat

### 强调

- 微信历史不是 Phase 1/2 工作。**不得在 resolver 尚未稳定、privacy gate 尚未生效、eval set 尚未建立前导入全量历史。**
- 第三方消息不应自由进入 public writing、training exports 或 general context。
- Historical import candidates should default to lower confidence and higher privacy than current direct user statements.

### 依赖

- Phase 1 resolver + privacy gate + review queue 全部稳定
- Phase 2 eval set 覆盖 import 场景

---

## 5. Phase 3.5 — Health Data Architecture

**目标：** 把健康数据从 Phase 0.5 的 `raw_health_data` + `memories` 兼容模式升级为正式健康分层。

### 做

- `health_observations` 表（raw high-frequency）
- `health_summaries` daily / weekly / monthly
- anomaly / pattern candidates
- reviewed `health_pattern`
- `health_baseline` core block only via review（不自动生成）
- diagnosis-like memory requires review
- health-specific privacy policy（默认 `sensitive`）
- health eval cases

### 不做

- 实时健康告警
- 医疗诊断自动化
- 健康数据外部分享

### 强调

- `health_observation_summary` 可以自动提交（Phase 1.0 R2），但 `health_pattern` / `health_baseline` 不能自动生成。
- Phase 1.0 已经建立了这个硬边界：auto_commit of health_observation_summary does not update health_baseline and does not create health_pattern。

### 依赖

- Phase 1 memory_items 稳定
- Phase 2.5 health intent 可用
- Phase 3 historical import 可与健康数据分层并行（无冲突）

---

## 6. Phase 4 — Long-Term Storage, Cold Archive, and Recovery

**目标：** 确保系统能长期迁移、恢复、低维护运行。

### 做

- Parquet / JSONL cold archive（按年/月打包）
- `memory_events` cold export for aged events（retention policy）
- `memory_access_log` TTL / archive policy（retention window, aggregation）
- manifest table（记录每次 export 的文件列表 + hash）
- monthly export package
- pg_dump + physical backup strategy
- **automated restore test**（至少半年一次，不只是 dump — 实际 restore 验证）
- checksum verification
- read-only degradation mode（数据库不可用时仍可读 cold archive 索引）
- emergency local archive browser（纯静态 HTML 或 CLI）
- NVMe / stronger host migration checkpoint（decision point before 3-year mark）

### 不做

- 云端自动备份（至少 v1 保持本地）
- 分布式存储
- 实时 WAL shipping

### Search Infrastructure Review（Phase 4）

- **Evaluate pgvector limits with real data** before reaching 1M vectors.
- Consider Qdrant / Milvus / dedicated vector store if vectors reach 1M-5M scale.
- Evaluate PostgreSQL FTS vs dedicated search engine **only after real bottlenecks appear**（do not pre-optimize for Elasticsearch in Phase 1-2）。
- The `memory_embeddings` table isolation ensures a vector store migration does **not** require rewriting `memory_items`.

### 强调

- **备份不是存在文件，而是 restore test 通过。**
- Cold archive 的部分能力（如 pg_dump）可以在 Phase 1 就开始积累，但自动化 restore test 是 Phase 4 的工作。

### 依赖

- Schema 在 Phase 1-3 中趋于稳定
- memory_items 主键 UUID（方便跨 export 引用）

---

## 7. Phase 5 — Conflict Graph and Advanced Memory Evolution

**目标：** 增强长期记忆冲突、演化、解释能力。

### 做

- `memory_edges` 表：`supports` / `contradicts` / `supersedes` / `derived_from`
- conflict review（多条 candidate 对同一 `(subject_key, predicate_key)` 冲突时可视化）
- historical decision chains（"为什么这个决策被取代了？"可追溯）
- supersession visualization
- optional AGM-inspired resolver ideas（asymmetric graph-based conflict detection）

### 不做

- 全自动冲突解决（Phase 5 仍保留人工 review）
- 复杂图数据库（PostgreSQL 递归 CTE 已足够 v1）
- 实时图查询

### 依赖

- Phase 1 `valid_from` / `valid_to` + `supersedes_memory_id` 数据基础
- Phase 2 retrieval 可做 edge-aware 扩展

---

## 8. Phase 6 — Personal Model Training and Export

**目标：** 在已有 provenance / privacy / de-identification 基础上，才考虑训练或偏好数据导出。

### 做

- training export policy（定义什么可以进入训练集）
- source_trust filtering（排除 `assistant_inferred`, `wechat_import`, `webpage`）
- third-party exclusion by default
- de-identification pipeline（姓名、地点、联系方式替换为占位符）
- SFT / DPO dataset preparation（仅 `user_direct` + `user_confirmed` 记忆）
- export manifest with content hash

### 不做

- 直接从 raw chat 训练
- 包含第三方消息的训练数据导出
- 自动化 fine-tuning pipeline

### 强调

- **训练导出必须晚于 privacy / provenance / review / de-identification 能力。**
- Phase 6 不是"做训练"，而是"为训练准备安全的导出管道"。
- No raw third-party chat export without explicit filtering.

### 依赖

- Phase 1 source_trust 体系
- Phase 2 retrieval 可筛选 memory_type
- Phase 3 微信导入的 de-identification 管道
- Phase 4 manifest / hash 体系

---

## 9. Long-Term Scale and Performance Roadmap

> 本节标记的是 1-10 年*可能*遇到的规模瓶颈和*预留*的迁移路线。
> Phase 1 不需要解决规模问题，但 Phase 2/3/4 必须明确保留迁移边界。

### 9.1 Expected Data Growth

| Time horizon | Events | Memory items | Embeddings | Storage estimate |
|---|---:|---:|---:|---:|
| 1 year | ~100K | ~10K | ~10K | ~5 GB |
| 3 years | ~1M | ~100K | ~50K | ~30 GB |
| 5 years | ~5M | ~500K | ~200K | ~80 GB |
| 10 years | ~20M | ~2M | ~1M | ~300 GB+ |

**注意：**

- 这些是 planning estimates，**不是保证值**。实际增长取决于：
  - 数据源频率（健康数据 polling 频率、对话频率）
  - retention policy（是否/何时引入 TTL、cold archive）
  - 是否启用 WeChat / historical import（单次即可推至 3-4 年数据规模）
  - extractor 产率（candidate 产生速率）
- **WeChat full import can immediately push the system to a 3-4 year data scale.** 微信历史单次导入即可产生百万级 events。
- 所有数字假设没有刻意清理、没有 aggressive TTL、extractor 保持当前产率。引入 retention policy 或 archive 策略后实际热存储量可显著降低。

### 9.2 Bottleneck Analysis

#### memory_events

- Append writes are not the bottleneck（单行插入，时间索引）。
- **Hot full-text search over millions of events will become expensive.** 百万级 events 之上的 FTS 需要时间分区或归档。
- Future solution: time-based partitioning（年/月），cold archive，manifest table。
- This belongs to Phase 3, not Phase 1.

#### memory_items and embeddings

- pgvector is appropriate for early scale.
- **100K vectors should be comfortable** on Raspberry Pi 5 with pgvector ivfflat/hnsw.
- **1M vectors may still be workable** with tuning/hardware but becomes a serious planning boundary.
- **5M+ vectors — must evaluate dedicated vector store**（Qdrant, Milvus, or offloaded to stronger host）. 迁移应 benchmark-driven，不是自动发生：先测量 pgvector 的实际延迟/吞吐/资源消耗，再与替代方案对比。
- **This is why embeddings must stay outside `memory_items`.** Embedding 的存储引擎可能需要独立迁移，不能和 committed memory schema 耦合。

#### memory_access_log

- It grows forever（每次 context injection 一行）。
- Not urgent in Phase 1（当前体量 < 100 条/天）。
- Future solution: TTL（retention policy），aggregation（按日/周汇总），cold archive。
- Add to Phase 3/4 operational hardening.

#### legacy memories dual-write

- Phase 1 dual-write is acceptable for compatibility（双写 ~2x 写入延迟，可接受）。
- Long-term, legacy `memories` should be retired to reduce duplication and storage overhead.
- Full retirement is Phase 3+（when `memory_items` coverage > 95% and all read paths use `memory_items`）。

### 9.3 Hardware Planning

| Horizon | Hardware | Expectation |
|---------|----------|------------|
| 1-2 years | Raspberry Pi 5, 8 GB RAM, SD/USB SSD | Likely fine at moderate personal scale |
| 3-5 years | Likely NVMe SSD required | Index management / cold archive becomes important |
| 5+ years | May require stronger host or split storage/search | Evaluate migration before pgvector hits 1M+ vectors |

**原则：**
- Do not optimize prematurely in Phase 1, but **preserve migration boundaries**（独立表、独立 service、明确接口）。
- Raspberry Pi 5 is a local-first personal platform; the architectural patterns should survive a migration to x86/cloud if needed.

### 9.4 Phase Additions from Scale Review

#### Phase 2 — Embedding Infrastructure (amended)

在现有 Phase 2 条目基础上补充：
- `memory_embeddings` independent table（`embedding_model`, `model_revision`, `embedding_dim`, `content_hash`, `is_active`）
- Shadow embedding migration framework（新模型后台 re-embedding batch jobs）
- Dual-read or shadow-read during migration（旧模型保持 active，新模型 backfill 后 eval 比较再切换）
- pgvector index selection（ivfflat → hnsw when cluster size grows beyond early thresholds）
- Embedding model upgrade does not require `memory_items` migration

#### Phase 3 — Historical Import and Storage Scaling (amended)

在现有 Phase 3 条目基础上补充：
- `memory_events` time partitioning（年/月）
- Cold archive manifest table
- Parquet / JSONL export for aged events
- WeChat import **only after** resolver + privacy gate + review queue + archive plan are stable
- Legacy `memories` retirement planning begins（when `memory_items` coverage confirmed）

#### Phase 3.5 or Phase 4 — Operational Hardening (amended)

在现有 Phase 4 条目基础上补充：
- `memory_access_log` TTL / archive policy（retention window, aggregation）
- Monthly restore test（not just pg_dump — actually restore to verify）
- pg_dump + physical backup + archive verification（checksum comparison）
- NVMe / stronger host migration checkpoint（decision point before 3-year mark）

#### Phase 4 or Later — Search Infrastructure Review

- **Evaluate pgvector limits with real data** before reaching 1M vectors. Migration decision must be benchmark-driven, not based on vector count alone.
- Consider Qdrant / Milvus / dedicated vector store only after pgvector performance measured against real query patterns at scale.
- Evaluate PostgreSQL FTS vs dedicated search engine **only after real bottlenecks appear**（do not pre-optimize for Elasticsearch in Phase 1-2）。
- The `memory_embeddings` table isolation ensures a vector store migration does **not** require rewriting `memory_items`.

### 9.5 Scale Guardrails

These are added to §11 Architectural Guardrails:

- Do not import full WeChat history before resolver, privacy gate, review queue, and cold archive plan are stable.
- Do not put embeddings inside `memory_items`.
- Do not keep all raw historical events hot forever.
- Do not treat pgvector as a permanent guarantee; treat it as the current local-first implementation.
- Do not optimize Phase 1 for hypothetical 10-year scale, but **preserve migration paths**（independent tables, documented interfaces, no tight coupling to one engine）.
- Backup strategy must eventually include restore tests, not just dump files.

---

## 10. Phase Dependency Summary

| Phase | Main Goal | Depends On | Must Not Start Before |
|-------|----------|-----------|----------------------|
| **1.0** | Memory lifecycle | Phase 0.5 | — |
| **1.1** | Privacy gate + eval | Phase 1.0 | resolver 稳定、review queue 可用 |
| **1.2** | Cleanup + policy rules | Phase 1.0, 1.1 | memory_type 分布可观测 |
| **2** | Retrieval quality + embedding architecture | Phase 1.1 eval set | privacy gate 稳定、eval runner 可重复运行 |
| **2.5** | Intent-aware context | Phase 1.1, 2 | actor 参数统一、retrieval 路径统一 |
| **3** | Historical import (WeChat) | Phase 1, 2 | resolver + privacy gate + review queue + eval 全部稳定 |
| **3.5** | Health data architecture | Phase 1, 2.5 | health intent 可用、memory_items 稳定 |
| **4** | Cold archive + recovery + operational hardening | Phase 1-3 schema stable | 大表结构不再频繁变更；access_log TTL, restore test, NVMe checkpoint |
| **4 (Search)** | Search infrastructure review | Real vector/FTS bottlenecks observed | pgvector at 1M+ vectors; dedicated store evaluation only after real data |
| **5** | Conflict graph | Phase 1-4 | valid_from/valid_to + supersedes 数据积累 |
| **6** | Training export | Phase 1-4 | privacy + provenance + de-identification 全部就绪 |

**Phase 4 的部分能力可提前：** pg_dump 从 Phase 0.5 已经开始；manifest / checksum 可在 Phase 2 schema 稳定后引入。但自动化 restore test 和 cold archive 完整方案需要 schema 成熟。

**Scale checkpoints（不与 Phase 绑定，由数据量触发）：**
- Events > 1M → time partitioning 必须启动
- Vectors > 100K → pgvector index tuning（hnsw）
- Vectors > 1M → dedicated vector store evaluation
- Access log rows > 10M → TTL / aggregation policy
- Total storage > 50 GB → NVMe + cold archive 必须启动

---

## 11. Architectural Guardrails Across All Phases

这些是跨越所有 Phase 的不变原则：

**数据层级：**
- Raw events are the source of truth.
- Candidates are proposals, not truth.
- Memory items are derived projections.
- Core blocks are curated and versioned.

**写入：**
- External agents propose, they do not approve.
- All writes go through provenance.
- `assistant_inferred` does not auto-commit to identity / health / relationship memory.
- Health observations do not silently become health patterns or baselines.

**读取：**
- Sensitive context requires explicit actor/intent policy.
- Privacy filtering must happen at SQL retrieval time, not in-LLM.
- `sealed` is never auto-injected for any actor.

**信任边界（Accepted Technical Debt）：**
- **api_client trust boundary:** `/v1/chat/completions` is currently assumed to be a trusted/private endpoint. `api_client` maps to `local_bot` policy.
- **Trigger for change:** 当出现第二个非本地、低信任、或外部 client 调用 `/v1/chat/completions` 时：
  1. require explicit `X-Actor` header，或
  2. downgrade default `api_client` to lower-privilege `personal`-only policy。
- This is accepted technical debt — Phase 1.0 does not need to change the code, but the boundary must be documented and reviewed before Phase 1.1 privacy gate goes live.

**基础设施：**
- Embedding must remain decoupled from `memory_items`.
- No full-history imports before privacy / review / eval are stable.
- No training exports before de-identification.
- Backup must include restore verification.
- PostgreSQL is the sovereign store; cold archives are projections.

**规模与迁移：**
- Do not put embeddings inside `memory_items`（embedding 引擎可能需要独立迁移）。
- Do not keep all raw historical events hot forever（time partitioning + cold archive in Phase 3/4）。
- Do not treat pgvector as a permanent guarantee; treat it as the current local-first implementation.
- Do not optimize Phase 1 for hypothetical 10-year scale, but **preserve migration paths**（independent tables, documented interfaces, no tight coupling to one engine）。
- WeChat full import must not happen before resolver, privacy gate, review queue, and cold archive plan are stable.
- Backup strategy must eventually include restore tests, not just dump files.

**节奏：**
- Each phase ships one coherent capability.
- Cross-phase scope creep is the primary risk to this project.
- Phase N+1 must not break Phase N's verified behavior.
