# STATUS — 最后更新：2026-05-08

## 当前阶段
Phase 0.5 完成 → 进入 Phase 1 规划

## 已完成
- ✅ M1：4 张新表 + memories 8 列扩展 + source_trust 回填（117 条）
- ✅ M2：6 个新函数；save_memory() 扩展 source_trust / source_event_ids / privacy_level / memory_type
- ✅ M3.1：POST /debug/memories 手动保存路径
- ✅ M3.2：POST /data/health 健康数据路径
- ✅ M3.3：AI 自动提取路径（process_memories_background）
- ✅ M4.1：core-blocks CRUD API（GET /core-blocks、GET /core-blocks/{key}、POST /core-blocks/{key}）
- ✅ M4.2：__BOT_PERSONA__ → core_blocks.response_policy 启动迁移（幂等）
- ✅ M4.3：get_persona() 改为优先读 core_blocks.response_policy，fallback memories
- ✅ M5.1：context injection 加入 core_blocks（白名单：response_policy + active_projects）
- ✅ M5.2：memory_access_log 写入（api_client + telegram_bot 双入口）
- ✅ M6.1：mcp_server.py save_memory 写入 source_trust=assistant_inferred / source_type=mcp_agent / actor=claude_mcp
- ✅ Phase 0.5 回归验证：10/10 全通过

## 下一步（Phase 1）
- memory_items 表 + memory_id UUID → 现有 memories 迁移
- resolver / conflict detector
- Privacy-gated retrieval
- Intent 分类
- MCP 工具重命名为 propose_memory

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
CONTEXT.md → logs/2026-05-07.md → logs/2026-05-08.md → 本文件
