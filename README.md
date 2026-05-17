# kiwi-mem — Personal AI Memory Infrastructure

A self-hosted, privacy-governed long-term memory system for personal AI assistants.
Runs on a Raspberry Pi 5 behind Cloudflare Tunnel, with Telegram Bot and MCP integration.

**Current phase**: Phase 1.5 (Candidate Review Policy) — M2 + M3a completed.

## Quick start

```bash
cp .env.example .env   # fill in secrets
docker compose up -d    # db + kiwi-mem + telegram-bot
```

Services: `db` (pgvector), `kiwi-mem` (:8080), `telegram-bot`, `cloudflared` (systemd).

## What it does

- **Long-term memory** with provenance (raw event → candidate → committed memory)
- **Actor privacy gate** — what an agent sees depends on who it is (SQL-layer enforcement)
- **Candidate review** — AI proposes, human reviews; high-stakes facts require manual approval
- **Hermes Agent** — restricted external agent with separate health/memory dual-path access
- **Telegram Bot** — personal chat interface with memory-injected context

## Project structure

```
kiwi-mem/          FastAPI memory gateway + MCP server
telegram-bot/      Telegram Bot (passive + proactive triggers)
scripts/           Tests and eval harnesses
evals/             Eval case definitions (JSONL)
docs/              Architecture, vision, phase plans, risks
claude-runner/     Host-side CLI runner for /dev command
```

## Documentation

| File | Purpose |
|------|---------|
| `docs/STATUS.md` | Current phase, completed modules, verification results |
| `docs/ARCHITECTURE.md` | System architecture, data flow, agent rules |
| `docs/VISION.md` | Long-term design philosophy, memory model, agent boundaries |
| `docs/KNOWN_RISKS.md` | Inherent risks and mitigation status |
| `docs/PHASE_1_PLAN.md` | Phase 1 roadmap |
| `docs/PHASE_1_5_REQUIREMENTS.md` | Candidate review policy requirements |

## Verification

```bash
python3 scripts/test_privacy_policy.py              # 19/19 — privacy helper
python3 scripts/test_candidate_review_policy.py      # 31/31 — classifier rules
python3 scripts/test_observation_m3a.py              # 24/24 — admin queue (needs Docker)
python3 scripts/test_observation_m3b.py              # 17/17 — resolver routing (needs Docker)
# HTTP-based (needs ACCESS_TOKEN + running kiwi-mem):
ACCESS_TOKEN=xxx python3 scripts/test_privacy_gate_retrieval.py  # 124/124
ACCESS_TOKEN=xxx python3 scripts/eval_retrieval_minimal.py      # 10/10
```
