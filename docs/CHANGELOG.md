# Changelog

## 2026-05-08 — Phase 1.0 Memory Lifecycle Closure

### Phase 1.0 M1 — memory_items Schema

- `memory_items` 表（21 columns, UUID PK, 6 indexes, 2 FKs, 不含 embedding）
- `insert_memory_item()` / `get_memory_item()` helpers
- 不改任何现有写入/检索路径
- Commit: `85ed010`

### Phase 1.0 M2 — Candidate Resolver

- `resolve_candidate()` — auto_commit / keep_pending / requires_review 三条路径
- Low-risk whitelist + high-stakes requires_review
- assistant_inferred 永远 keep_pending
- Dual-write: memory_items + memories (compat)
- Transaction 包裹：conflict → supersede → dual-write → candidate update
- `scripts/test_resolver_scenarios.py`（28 assertions, 7 scenarios）
- force_commit 参数预留给 M3

### Phase 1.0 M3 — Review Queue API

- 4 端点：list / detail / commit / reject
- force_commit 人工批准路径
- 状态保护：committed/rejected 重复操作拒绝（409）
- age_days 修复（EPOCH/86400）
- Commit: `40b0c64`, `ea717e4`

### Docs

- `docs/PHASE_1_PLAN.md` — Phase 1.0/1.1/1.2 三段计划（524 行）
- `docs/PHASES.md` — Phase 0-6 长期路线图（490 行）
- `docs/STATUS.md` — Phase 1.0 completed 标记
- tag: `phase-1-planning-frozen`

---

## 2026-05-08 — Phase 0.5 Foundation (earlier)

### M1-M6: Provenance, Core Blocks, Access Logging

- `memory_events` append-only provenance layer
- `memory_candidates` shadow/proposal layer
- `core_blocks` versioned curated core memory
- `memory_access_log` read/context audit
- `__BOT_PERSONA__` → `core_blocks.response_policy` 迁移
- MCP external entry provenance (`claude_mcp`)
- Final validation: 10/10 PASS
- tag: `phase-0.5-foundation`

### Hermes Conservative Integration

- `hermes_mcp.py` — 5 restricted tools
- Security review: 7/7 PASS
- Core block whitelist: response_policy + active_projects only
- tag: `phase-0.5-hermes-conservative`