# STATUS — 最后更新：2026-05-07

## 当前阶段
Phase 0.5（开始实施）

## 上次完成
- 完整架构设计文档（CONTEXT.md）
- Phase 0.5 实施计划完整规划并定稿（见 logs/2026-05-07.md）
- 9 条架构修订决策已确认（见日志 D1–D9）

## 已完成
- ✅ M1：4 张新表 + memories 8 列扩展 + source_trust 回填
- ✅ M2：6 个新函数（append_event / create_candidate / auto_commit_candidate / create_core_block_version / get_active_core_block / log_memory_access）；save_memory() 扩展 4 个参数
- ✅ M3.1：POST /debug/memories 手动保存路径，已验证
- ✅ M3.2：POST /data/health 健康数据路径，已验证

## 正在进行
- M3.3：AI 自动提取路径（extract_and_save_memories）

## 下一步（按顺序）
1. **M3.3** — main.py：AI 自动提取路径
2. **M4** — main.py：core-blocks API + __BOT_PERSONA__ 迁移
3. **M5** — main.py：context injection 加入 core_blocks + access log
4. **M6** — mcp_server.py：source_trust + candidate 路径
5. 回归验证 + 验收标准检测

## 关键决策记录
- memory_candidates 纳入 Phase 0.5（不推迟）
- AI/MCP 写入：source_trust='assistant_inferred' + 落 candidates 表，不自动提交
- core_blocks 独立版本化，create_core_block_version()，不原地覆盖
- idempotency_key：partial unique index(source_type, key) WHERE NOT NULL
- Low-risk additive migration，执行前必须 pg_dump

## 读这里开始下一个 session
CONTEXT.md → logs/2026-05-07.md → 本文件
