# Personal AI Assistant — 树莓派5 部署文档

## 项目概述

在树莓派5（用户 chris）上搭建一个私有、自托管的个人 AI 助手系统，具备长期记忆、多数据源接入（iPhone 健康、iPad 日历/提醒）、Telegram 交互界面和 Cloudflare 安全暴露能力。

---

## 关键地址 & 访问信息

| 项目 | 值 |
|------|----|
| 公网地址 | `https://agent.xeon.im` |
| 管理面板 | `https://agent.xeon.im/admin` |
| MCP 地址 | `https://agent.xeon.im/memory/mcp?token=o1ZfbY11cCrImb0qh0qaRbMigo7b` |
| MCP connector 名称 | `locapmemo` |
| Pi Tailscale IP | `100.93.144.87` |
| Telegram Bot | `@chrisliclaudebot`，只响应 `CHAT_ID=7559735793` |
| ACCESS_TOKEN / MCP token | `o1ZfbY11cCrImb0qh0qaRbMigo7b` |

---

## 目录结构

```
/home/chris/assistant/
├── CLAUDE.md               ← 本文件
├── docker-compose.yml      ← 根部整合编排（4个服务）
├── .env                    ← 实际 secrets（不入 git）
├── .env.example            ← secrets 模板
├── .gitignore
├── kiwi-mem/               ← 记忆网关（克隆自 github.com/LucieEveille/kiwi-mem）
│   ├── Dockerfile
│   ├── main.py             ← FastAPI 主应用
│   ├── database.py         ← PostgreSQL + pgvector 操作
│   ├── memory_extractor.py ← AI 提取记忆
│   ├── dream.py            ← 记忆整合（睡眠模拟）
│   ├── daily_digest.py     ← 日历摘要压缩
│   ├── admin-panel/        ← 管理面板（静态 HTML）
│   └── requirements.txt
├── telegram-bot/           ← Telegram Bot 服务（已完成）
│   ├── Dockerfile
│   ├── bot.py              ← 被动响应 + 主动触发（合并版）
│   └── requirements.txt
├── data/                   ← 持久数据卷挂载点（.gitignore 已排除）
└── cloudflared/            ← Cloudflare Tunnel（systemd 运行，非 Docker）
```

---

## 系统架构

```
iPhone (Health Auto Export)
    │  POST /data/health
    ▼
Cloudflare Tunnel (agent.xeon.im)
    │
    ▼
kiwi-mem :8080  ←──────────────────── claude.ai MCP (locapmemo)
(FastAPI 记忆网关)                         6 tools: get_recent / search /
    │  注入记忆 context                     save / lock / unlock / digest
    │  gpt-5.4-mini via co.yes.vg
    ▼
PostgreSQL :5432 + pgvector
(raw_health_data + memories)
    ↑
    │ asyncpg 读取健康/情绪数据
    ▼
Telegram Bot (bot.py)
    被动: 用户消息 → kiwi-mem /v1/chat/completions → 回复
    主动: 每小时检查健康数据 → gpt-5.4-mini 判断 → gpt-5.5 生成消息
```

**数据流：**
1. iPhone/iPad 数据 → `POST /data/health` → `raw_health_data`（原始） + `memories`（AI提炼）
2. Telegram 消息 → Bot → kiwi-mem（自动注入相关记忆） → gpt-5.4-mini 回复
3. 每小时 trigger → 读 raw_health_data → 判断是否需要主动推送

---

## 服务清单

| 服务 | 镜像/构建 | 端口 | 状态 |
|------|-----------|------|------|
| `db` | `pgvector/pgvector:pg16` | 5432（仅内网） | ✅ 运行中 |
| `kiwi-mem` | `./kiwi-mem/Dockerfile` | `127.0.0.1:8080` | ✅ 运行中 |
| `cloudflared` | systemd 服务（非 Docker） | 无（出站 tunnel） | ✅ 运行中（`agent.xeon.im`） |
| `telegram-bot` | `./telegram-bot/Dockerfile` | 无（出站） | ✅ 运行中（`assistant-telegram-bot-1`） |

---

## LLM 配置

### kiwi-mem（主记忆网关）

| 参数 | 值 |
|------|----|
| `API_BASE_URL` | `https://co.yes.vg/v1/responses` |
| `API_KEY` | `cr_0f0831...`（见 `.env`） |
| `DEFAULT_MODEL` | `gpt-5.4-mini` |

**重要：** co.yes.vg 只支持 `/v1/responses`（OpenAI Responses API 格式），**不支持** `/v1/chat/completions` 或 `/v1/messages`（后者需要 Claude Code user-agent）。`main.py` 内有 Chat Completions ↔ Responses API 双向适配器，外部仍用标准 OpenAI 格式调用 kiwi-mem。

**允许的模型：** gpt-5.2、gpt-5.4、gpt-5.4-mini、gpt-5.3-codex、gpt-5.5。Claude 模型（claude-sonnet-4-6 等）在 Responses API 上返回 403，不可用。

### Telegram Bot

| 用途 | 模型 | 路由 |
|------|------|------|
| 被动回复 | `gpt-5.4-mini`（`CHEAP_MODEL`） | 经 kiwi-mem `/v1/chat/completions` |
| trigger 判断（YES/NO） | `gpt-5.4-mini`（`CHEAP_MODEL`） | 直调 `co.yes.vg/v1/responses` |
| trigger 消息生成 | `gpt-5.5`（`TRIGGER_MODEL`） | 直调 `co.yes.vg/v1/responses` |

### Embedding（独立服务）

| 参数 | 值 |
|------|----|
| `EMBEDDING_API_KEY` | SiliconFlow key（见 `.env`） |
| `EMBEDDING_BASE_URL` | `https://api.siliconflow.com/v1` |
| `EMBEDDING_MODEL` | `Qwen/Qwen3-Embedding-0.6B`（1024 维） |

### Claude Code（本地开发环境）

```bash
# ~/.bashrc 中配置
ANTHROPIC_BASE_URL=https://co.yes.vg
ANTHROPIC_API_KEY=<yes.vg key>
```

---

## 重要配置决策

### 1. 端口绑定策略
kiwi-mem 绑定 `127.0.0.1:8080`，确保只有本机和 cloudflared tunnel 能访问。

### 2. PostgreSQL 不对外暴露
`db` 服务没有 `ports` 映射，只在 Docker 内网 `db:5432` 可访问。

### 3. kiwi-mem 内有独立 docker-compose.yml
原仓库文件，保留但不使用。所有服务统一由根目录 `docker-compose.yml` 编排。

### 4. Secrets 管理
所有敏感值在 `.env`，`.gitignore` 已排除。Docker Compose 通过环境变量传入容器。

### 5. ARM64 兼容性
所有镜像均验证支持 `linux/arm64`（树莓派5）。

### 6. Telegram Bot 架构选择
被动回复走 kiwi-mem 网关（自动注入记忆，无需手动查询）；trigger 直调 co.yes.vg（轻量判断，无需记忆注入）。

### 7. cloudflared 不用 Docker
树莓派5 上 cloudflared 以 systemd 服务运行，`.env` 里 `CLOUDFLARE_TUNNEL_TOKEN` 留空，Docker 版 cloudflared 容器不启动。

### 8. asyncpg JSONB 返回类型
asyncpg 在此环境返回 JSONB 字段为字符串（不是 dict），需用 `rj = raw if isinstance(raw, dict) else json.loads(raw)` 处理。

---

## 环境变量说明

```bash
# LLM（kiwi-mem 用，Responses API 格式）
API_KEY                     # co.yes.vg 的 API key（cr_0f0831...）
API_BASE_URL                # https://co.yes.vg/v1/responses
DEFAULT_MODEL               # gpt-5.4-mini

# Embedding（SiliconFlow）
EMBEDDING_API_KEY           # SiliconFlow API key
EMBEDDING_BASE_URL          # https://api.siliconflow.com/v1
EMBEDDING_MODEL             # Qwen/Qwen3-Embedding-0.6B

# kiwi-mem 服务
MEMORY_ENABLED=true
PORT=8080
ACCESS_TOKEN                # o1ZfbY11cCrImb0qh0qaRbMigo7b
MAX_MEMORIES_INJECT=15
MEMORY_EXTRACT_INTERVAL=3

# PostgreSQL
POSTGRES_USER=kiwi
POSTGRES_PASSWORD           # kiwi_rpi5_2026
POSTGRES_DB=kiwi_mem

# Cloudflare Tunnel（留空，systemd 管理）
CLOUDFLARE_TUNNEL_TOKEN=

# Telegram Bot
TELEGRAM_BOT_TOKEN          # 8789388436:AAFQ9...
TELEGRAM_CHAT_ID            # 7559735793

# GPT 模型（Telegram Bot 用）
OPENAI_API_KEY              # 同 API_KEY
OPENAI_BASE_URL             # https://co.yes.vg/v1
CHEAP_MODEL                 # gpt-5.4-mini
TRIGGER_MODEL               # gpt-5.5
```

---

## 已完成工作

### 基础设施
- [x] 创建项目目录结构，克隆 kiwi-mem
- [x] 创建根目录 `docker-compose.yml`（整合 4 个服务）
- [x] 配置 `.env`、`.env.example`、`.gitignore`
- [x] 成功构建所有 Docker 镜像（ARM64）
- [x] PostgreSQL + pgvector 就绪，kiwi-mem API 正常

### Embedding & 向量搜索
- [x] 接入 SiliconFlow embedding（`Qwen/Qwen3-Embedding-0.6B`，1024 维）
- [x] `database.py` 新增 `EMBEDDING_API_KEY` / `EMBEDDING_BASE_URL` 独立变量
- [x] embedding 覆盖率 100%，向量语义搜索正常

### LLM API 适配
- [x] 修复 LLM API 路径：改为 `https://co.yes.vg/v1/responses`（Responses API）
- [x] `main.py` 新增 Chat Completions ↔ Responses API 双向适配器（非流式、流式、tool loop 三路径）
- [x] `DEFAULT_MODEL` 改为 `gpt-5.4-mini`

### Cloudflare & 公网
- [x] cloudflared 以 systemd 服务运行，公网域名 `agent.xeon.im`
- [x] 更换 `ACCESS_TOKEN` 为安全随机 token

### claude.ai MCP 集成
- [x] 修复 MCP 连接 406 错误（FastMCP 需要 `Accept: text/event-stream`，加 ASGI 包装器自动注入）
- [x] `/memory/mcp` 和 `/calendar/mcp` 加入鉴权（`?token=` 和 `Authorization: Bearer` 双支持）
- [x] 新增 `GET /.well-known/oauth-protected-resource/{path}` OAuth 发现端点
- [x] claude.ai MCP 连接验证通过：connector 名称 `locapmemo`，6 个工具可见
- [x] Project System Prompt 已配置，强制使用 locapmemo，不用内置记忆

### 健康数据管道
- [x] 实现 `POST /data/health`，支持 step_count / heart_rate / sleep_analysis / active_energy / resting_heart_rate / walking_running_distance / workouts / stateOfMind
- [x] 自然语言转换 + 按日期去重更新（title 格式：`健康-{date}-{指标}`）
- [x] 修复 workout 解析（`start` 字段、秒→分钟、`activeEnergyBurned`/`heartRate` 嵌套结构、`name` 中文映射）
- [x] 修复 stateOfMind 解析（`valenceClassification`、`labels`、`associations` 三套中文映射，importance=7）
- [x] 新增 `raw_health_data` 原始存档表（`database.py` v6.0）：JSONB + `(data_type, source_date)` 索引
- [x] 双写架构：每次 POST 先存 `raw_health_data`，再 AI 提炼写 `memories`
- [x] Health Auto Export 已配置两个 automation（Health Metrics + Workouts）

### Telegram Bot
- [x] `telegram-bot/bot.py` 实现被动响应（经 kiwi-mem，gpt-5.4-mini）
- [x] 每小时 trigger：读 `raw_health_data` → `gpt-5.4-mini` 判断 YES/NO → `gpt-5.5` 生成推送消息
- [x] 修复被动回复 403 错误（硬编码 `claude-sonnet-4-6` 改为 `CHEAP_MODEL`）
- [x] `assistant-telegram-bot-1` 容器运行中，`restart: unless-stopped`
- [x] Bot `@chrisliclaudebot`，只响应 `CHAT_ID=7559735793`
- [x] **v2 全面重构**（bot.py）：
  - 每次回复前并行拉三路数据（`search_memory` + `get_recent(10)` + `build_today_health_block`），用 `asyncio.gather`
  - system prompt 注入格式：`今日实时数据 → 相关记忆 → 最近动态 → SYSTEM_PROMPT`
  - 对话历史超过 20 条（10轮）自动用 LLM 压缩为摘要，清空后保留为首条 assistant 消息
  - 用户超过 15 分钟不活跃自动清空历史
  - 写记忆时检测重要内容关键词（情绪/计划/重要事件）→ importance=8，普通对话 importance=5
  - `asyncio.Queue` 串行消费消息，防止并发请求触发 yes.vg 限流

### 健康数据 — Sleep 聚合修复
- [x] **修复 `main.py` sleep 聚合根本性 bug**（旧逻辑：`ORDER BY received_at DESC LIMIT 1` 取最后收到的单条，结果取到 0.96h 子区间）
- [x] 新逻辑：加载当日所有 sleep session → 过滤异常记录（时间窗口 >16h 或 <15min）→ 按 sleepStart 排序 → gap >2h 分组为独立 session → 去重重叠子区间（保留最大时间跨度，重叠 >15min 的丢弃）→ 各阶段分别求和 → 取最近结束的 session 写入 health_summary
- [x] 兼容 `start`/`end` 字段名（Health Auto Export 不同版本格式）
- [x] 验证：2026-05-06 睡眠摘要从 0.96h 修正为 3.54h（rem=0.71h, core=2.83h）

### 健康数据 — Bot 注入细化
- [x] `build_today_health_block()` 同时读 `health_summary`（聚合）和 `raw_health_data`（原始）
- [x] 新增字段：静息心率、HRV 均值（ms）、呼吸率均值（/min）、腕温（°C）
- [x] 睡眠显示补全：入睡/起床时间（`sleepStart→sleepEnd`）、浅睡(core)、清醒时长
- [x] 运动记录补全：HR avg
- [x] 示例输出：`步数 2,283 / 心率 avg87/min71/max103 / 静息心率 55 / HRV 38.9ms / 呼吸率 18.4/min / 腕温 36.3°C / 睡眠 7.2h 00:35→07:47（深睡1.1h 浅睡4.2h REM 1.9h 清醒0.02h）`

### /dev 命令（Claude Code 自主开发）
- [x] `telegram-bot/bot.py` 新增 `/dev <需求>` 命令处理器（`CommandHandler`）
- [x] 新建 `claude-runner/runner.py`：HOST 侧纯 stdlib HTTP 服务，监听 `0.0.0.0:7777`，鉴权复用 `ACCESS_TOKEN`
- [x] 新建 `claude-runner/claude-runner.service`：systemd 服务文件，需手动 `sudo systemctl enable/start claude-runner`
- [x] 流程：`/dev <需求>` → LLM 扩展成完整任务描述 → POST `http://172.18.0.1:7777/run` → `claude -p <task> --dangerously-skip-permissions`（cwd=assistant 目录）→ 输出发回 Telegram（超 3000 字符截断）
- [x] `DEV_TASK_SYSTEM`：内嵌项目背景（服务架构、文件路径、LLM API）和约束（不改 docker-compose.yml、新文件放 connectors/）
- [x] Docker 容器通过 `172.18.0.1:7777` 访问 HOST runner（已验证通路）；runner 从 `.env` 读 `API_KEY` 作为 `ANTHROPIC_API_KEY`

### Persona 迁移到 kiwi-mem
- [x] `SYSTEM_PROMPT` 硬编码人设内容迁移为 kiwi-mem 记忆（title=`__BOT_PERSONA__`，importance=10，is_permanent=true）
- [x] `bot.py` 保留 `SYSTEM_PROMPT_RULES`（格式/数据优先级规则，不可被覆盖，仍写死在代码）
- [x] `bot.py` 新增 `get_persona()` 函数：按 title 精确查询 kiwi-mem，带 5 分钟 TTL 缓存，失败时 fallback 旧缓存
- [x] `kiwi-mem/main.py` `GET /debug/memories` 新增 `title=` 精确匹配参数（SQL `WHERE title = $1`）
- [x] system prompt 组装顺序：`今日实时数据 → 相关记忆 → 最近动态 → __BOT_PERSONA__ → SYSTEM_PROMPT_RULES`
- [x] 改 persona 方式：管理面板直接编辑 `__BOT_PERSONA__` 记忆，或 bot 自行调用 kiwi-mem API 写入

---

## 待完成事项

### 数据接入
- [ ] `POST /data/calendar` — iPad 日历事件接入（格式待定，配合 iOS 快捷指令）
- [ ] `POST /data/reminders` — iPad Reminders 接入
- [ ] State of Mind automation 配置（Health Auto Export 第三个 automation）

### Telegram Bot 优化
- [ ] **claude-runner systemd 安装**：`sudo cp claude-runner/claude-runner.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now claude-runner`
- [ ] 考虑将 trigger 改为系统 cron（现在是 job_queue 内部定时，重启后 5 分钟首次运行，但 v2 已有队列和压缩，优先级降低）

### 未来规划
- [ ] Anthropic 官方 API key（统一官端和 Bot 的模型风格，启用 Claude 模型）
- [ ] 个人数据看板（raw_health_data 已就绪，可直接出图表）
- [ ] Windows VM + Dispatch（Unraid 到货后）
- [ ] X1004 HAT 到货后换上

---

## 常用运维命令

```bash
cd /home/chris/assistant

# 查看所有服务状态
docker compose ps

# 查看日志（实时）
docker compose logs -f kiwi-mem
docker compose logs -f telegram-bot

# 重建特定服务镜像
docker compose build kiwi-mem && docker compose up -d kiwi-mem
docker compose build telegram-bot && docker compose up -d telegram-bot

# 启动所有服务
docker compose up -d

# 停止所有服务
docker compose down

# 停止并清除数据库数据（危险）
docker compose down -v
```

---

## kiwi-mem 核心机制（快速参考）

- **热度衰减**：记忆有热度值，时间衰减，反复提及升温；热度高→全文注入，中→摘要，冷→不注入
- **Dream 整合**：模拟睡眠，清理重复碎片，融合相关记忆，推断隐含信息
- **矛盾检测**：新记忆和旧记忆冲突时自动使旧记忆失效
- **锁定记忆**：重要记忆可锁定，不衰减不自动清除
- **管理面板**：`https://agent.xeon.im/admin`，用 `ACCESS_TOKEN` 登录
- **MCP 端点**：`https://agent.xeon.im/memory/mcp`，鉴权 `?token=<ACCESS_TOKEN>`
