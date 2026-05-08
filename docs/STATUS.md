# STATUS — 最后更新：2026-05-08

## 当前阶段
Phase 0.5（M1–M4 完成，进入 M5）

## 已完成
- ✅ M1：4 张新表 + memories 8 列扩展 + source_trust 回填（117 条）
- ✅ M2：6 个新函数；save_memory() 扩展 source_trust / source_event_ids / privacy_level / memory_type
- ✅ M3.1：POST /debug/memories 手动保存路径，已验证
- ✅ M3.2：POST /data/health 健康数据路径，已验证
- ✅ M3.3：AI 自动提取路径（process_memories_background），已验证
- ✅ M4.1：core-blocks CRUD API（GET /core-blocks、GET /core-blocks/{key}、POST /core-blocks/{key}）
- ✅ M4.2：__BOT_PERSONA__ → core_blocks.response_policy 启动迁移（幂等）
- ✅ M4.3：get_persona() 改为优先读 core_blocks.response_policy，fallback memories

## 下一步（按顺序）
1. **M5** — main.py：context injection 加入 core_blocks + log_memory_access
2. **M6** — mcp_server.py：source_trust='assistant_inferred' + create_candidate
3. 回归验证 + 验收标准检测

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

## 读这里开始下一个 session
CONTEXT.md → logs/2026-05-07.md → logs/2026-05-08.md → 本文件
