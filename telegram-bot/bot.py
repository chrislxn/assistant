#!/usr/bin/env python3
"""
Telegram Bot — Chris 的私人助手 v2

被动：消息队列串行 → 并行拉三路数据（语义搜索+最近记忆+健康数据）
      → 注入 system prompt → LLM 回复 → 立刻写记忆
主动：每小时 trigger，夜间静默 / 24h topic 去重 / 每日3条上限
"""
import asyncio
import json
import logging
import os
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Optional

import asyncpg
import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# ── 配置 ─────────────────────────────────────────────────────────────────────
BOT_TOKEN     = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID       = int(os.environ["TELEGRAM_CHAT_ID"])
KIWI_URL      = os.environ.get("KIWI_MEM_URL", "http://kiwi-mem:8080")
KIWI_TOKEN    = os.environ["KIWI_MEM_ACCESS_TOKEN"]
API_KEY       = os.environ["API_KEY"]
CHEAP_MODEL   = os.environ.get("CHEAP_MODEL", "gpt-5.4-mini")
TRIGGER_MODEL = os.environ.get("TRIGGER_MODEL", "gpt-5.5")
DB_URL        = os.environ["DATABASE_URL"]

INACTIVITY_SECS  = 900   # 15分钟不活跃自动清空历史
HISTORY_MAX_MSGS = 20    # 超过20条（10轮）立刻LLM压缩
PERSONA_TITLE    = "__BOT_PERSONA__"
PERSONA_TTL_SECS = 300   # 5分钟缓存，改完 persona 后最多5分钟生效
DEV_RUNNER_URL   = "http://172.18.0.1:7777"

DEV_TASK_SYSTEM = """你是一个精准的任务规格描述器。将用户的开发需求转换成清晰完整的 Claude Code 任务描述。

项目背景：
- 工作目录：/home/chris/assistant
- 架构：Docker Compose，服务有 kiwi-mem（FastAPI 记忆网关 :8080）、telegram-bot、db（PostgreSQL+pgvector）
- kiwi-mem 源码：kiwi-mem/main.py（主应用）、database.py、memory_extractor.py
- telegram-bot 源码：telegram-bot/bot.py
- LLM API：https://co.yes.vg/v1/responses（Responses API，可用模型 gpt-5.4-mini / gpt-5.5）
- 公网地址：https://agent.xeon.im

生成的任务描述必须包含：
1. 目标：要实现什么功能
2. 操作位置：具体修改哪些文件
3. 约束：不能修改 docker-compose.yml；不破坏现有 bot.py 主逻辑；新连接器放 connectors/ 子目录
4. 完成标准：创建了哪些文件，是否需要重启（docker compose build <service> && docker compose up -d <service>）

只输出任务描述正文，不要前置解释，不要问候语。"""

IMPORTANT_KEYWORDS = {
    "Ellie", "情绪", "伤心", "开心", "生气", "担心", "焦虑",
    "计划", "决定", "重要", "失去", "思念", "想念", "哭", "难过",
    "喜欢", "爱", "讨厌", "心情", "感受", "分手", "失恋", "难受",
    "压力", "崩溃", "高兴", "兴奋", "期待", "害怕", "后悔",
    # 偏好/习惯类——用户表达规则/偏好时自动升为 importance=8
    "以后", "记住", "偏好", "习惯", "别再", "不要再", "提醒",
}

# 技术规则：永远不变，不放进 kiwi-mem
SYSTEM_PROMPT_RULES = """格式规则（不可覆盖）：
- 不用 markdown，不列清单，短句为主
- 不说「根据我的记忆」「我看到记忆里」之类的话
- 不问「还有什么我能帮你的」

数据规则：
- 系统提示开头的「今日实时数据」永远最权威，与记忆冲突时以它为准"""

# ── Persona 缓存 ──────────────────────────────────────────────────────────────
_persona_cache: str = ""
_persona_cache_at: float = 0.0


async def get_persona() -> str:
    """从 kiwi-mem 读取 persona 记忆，带 TTL 缓存。"""
    import time
    global _persona_cache, _persona_cache_at
    now = time.monotonic()
    if _persona_cache and now - _persona_cache_at < PERSONA_TTL_SECS:
        return _persona_cache
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(
                f"{KIWI_URL}/debug/memories",
                headers={"Authorization": f"Bearer {KIWI_TOKEN}"},
                params={"title": PERSONA_TITLE, "limit": 1},
            )
            r.raise_for_status()
            results = r.json().get("results", [])
            if results:
                _persona_cache = results[0]["content"]
                _persona_cache_at = now
                return _persona_cache
    except Exception as e:
        log.warning("get_persona 失败: %s", e)
    return _persona_cache  # 失败时返回旧缓存

# ── 状态 ─────────────────────────────────────────────────────────────────────
_history:       list[dict]         = []
_last_msg_time: Optional[datetime] = None
_db_pool:       Optional[asyncpg.Pool] = None
_msg_queue:     asyncio.Queue      = asyncio.Queue()


# ── DB ───────────────────────────────────────────────────────────────────────
async def get_db_pool() -> asyncpg.Pool:
    global _db_pool
    if _db_pool is None:
        _db_pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=3)
    return _db_pool


async def init_db() -> None:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS trigger_log (
                id       SERIAL PRIMARY KEY,
                topic    TEXT NOT NULL,
                content  TEXT NOT NULL,
                sent_at  TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_trigger_log_topic_sent
            ON trigger_log (topic, sent_at)
        """)
    log.info("DB 初始化完成（trigger_log 表就绪）")


# ── 记忆读写 ──────────────────────────────────────────────────────────────────
def _format_memories(data: dict) -> str:
    results = data.get("results", [])
    if not results:
        return ""
    lines = []
    for m in results:
        ts      = m.get("created_at", "")[:10]
        title   = m.get("title", "")
        content = m.get("content", "")
        prefix  = f"[{ts}] {title}：" if title else f"[{ts}] "
        lines.append(prefix + content)
    return "\n".join(lines)


async def search_memory(query: str, limit: int = 10) -> str:
    """语义搜索与用户当前消息相关的记忆。"""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{KIWI_URL}/debug/memories",
                headers={"Authorization": f"Bearer {KIWI_TOKEN}"},
                params={"q": query, "limit": limit},
            )
            r.raise_for_status()
            return _format_memories(r.json())
    except Exception as e:
        log.warning("search_memory 失败: %s", e)
        return ""


async def get_recent(limit: int = 10) -> str:
    """获取最近 N 条记忆（按时间倒序）。"""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{KIWI_URL}/debug/memories",
                headers={"Authorization": f"Bearer {KIWI_TOKEN}"},
                params={"limit": limit},
            )
            r.raise_for_status()
            return _format_memories(r.json())
    except Exception as e:
        log.warning("get_recent 失败: %s", e)
        return ""


def _is_important(text: str) -> bool:
    return any(kw in text for kw in IMPORTANT_KEYWORDS)


async def save_conversation_memory(user_text: str, reply: str, important: bool = False) -> None:
    """每轮对话结束后立刻写入记忆。重要内容 importance=8，普通 importance=5。"""
    now        = datetime.now().strftime("%Y-%m-%d %H:%M")
    u_short    = user_text[:200]
    r_short    = reply[:200]
    content    = f'Telegram {now}：用户说"{u_short}"，回复"{r_short}"'
    title      = f"Telegram对话-{now[:10]}"
    importance = 8 if important else 5
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"{KIWI_URL}/debug/memories",
                headers={"Authorization": f"Bearer {KIWI_TOKEN}"},
                json={"title": title, "content": content, "importance": importance},
            )
            r.raise_for_status()
        log.info("对话记忆已写入 (importance=%d): %s…", importance, content[:80])
    except Exception as e:
        log.warning("save_conversation_memory 失败: %s", e)


# ── 今日健康数据 ───────────────────────────────────────────────────────────────
async def build_today_health_block() -> str:
    """从 health_summary + raw_health_data 读取今日健康数据，格式化为紧凑字符串。"""
    today     = date.today()
    yesterday = today - timedelta(days=1)

    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            today_rows = await conn.fetch(
                "SELECT metric_type, value_json FROM health_summary WHERE date = $1",
                today,
            )
            sleep_row = await conn.fetchrow(
                """SELECT value_json FROM health_summary
                   WHERE date = $1 AND metric_type = 'sleep'""",
                yesterday,
            )
            # 从 raw_health_data 读额外生理指标（今天或昨天取最新）
            raw_vitals_rows = await conn.fetch(
                """SELECT metric_name, raw_json FROM raw_health_data
                   WHERE data_type = 'health_metrics'
                     AND metric_name = ANY($1::text[])
                     AND source_date >= $2
                   ORDER BY received_at DESC""",
                ["heart_rate_variability", "respiratory_rate",
                 "apple_sleeping_wrist_temperature"],
                yesterday,
            )
    except Exception as e:
        log.error("build_today_health_block DB 失败: %s", e)
        return ""

    by_type: dict = {}
    for row in today_rows:
        val = row["value_json"]
        by_type[row["metric_type"]] = val if isinstance(val, (dict, list)) else json.loads(val)

    if "sleep" not in by_type and sleep_row:
        val = sleep_row["value_json"]
        by_type["sleep"] = val if isinstance(val, (dict, list)) else json.loads(val)

    # raw vitals：每个 metric_name 取最新一条（rows 已按 received_at DESC）
    raw_vitals: dict = {}
    for row in raw_vitals_rows:
        name = row["metric_name"]
        if name not in raw_vitals:
            val = row["raw_json"]
            raw_vitals[name] = val if isinstance(val, dict) else json.loads(val)

    parts: list[str] = []

    if "steps" in by_type:
        total = by_type["steps"].get("total", 0)
        if total:
            parts.append(f"步数 {total:,}")

    if "heart_rate" in by_type:
        hr = by_type["heart_rate"]
        if hr.get("avg"):
            s = f"心率 avg{hr['avg']}"
            if hr.get("min"): s += f"/min{hr['min']}"
            if hr.get("max"): s += f"/max{hr['max']}"
            parts.append(s)

    if "resting_heart_rate" in by_type:
        rhr = by_type["resting_heart_rate"].get("avg")
        if rhr:
            parts.append(f"静息心率 {rhr}")

    # HRV（单值或取 data 数组均值）
    if "heart_rate_variability" in raw_vitals:
        data = raw_vitals["heart_rate_variability"].get("data", [])
        if data:
            avg_hrv = sum(d["qty"] for d in data) / len(data)
            parts.append(f"HRV {avg_hrv:.1f}ms")

    # 呼吸率（均值）
    if "respiratory_rate" in raw_vitals:
        data = raw_vitals["respiratory_rate"].get("data", [])
        if data:
            avg_rr = sum(d["qty"] for d in data) / len(data)
            parts.append(f"呼吸率 {avg_rr:.1f}/min")

    # 腕温
    if "apple_sleeping_wrist_temperature" in raw_vitals:
        data = raw_vitals["apple_sleeping_wrist_temperature"].get("data", [])
        if data:
            parts.append(f"腕温 {data[-1]['qty']:.1f}°C")

    if "sleep" in by_type:
        sl = by_type["sleep"]
        total = sl.get("total", 0)
        if total:
            # 入睡/起床时间（取时分部分）
            time_range = ""
            if sl.get("sleepStart") and sl.get("sleepEnd"):
                t_start = sl["sleepStart"][11:16] if len(sl["sleepStart"]) >= 16 else sl["sleepStart"]
                t_end   = sl["sleepEnd"][11:16]   if len(sl["sleepEnd"]) >= 16   else sl["sleepEnd"]
                time_range = f" {t_start}→{t_end}"
            s = f"睡眠 {total:.1f}h{time_range}"
            details = []
            if sl.get("deep"):  details.append(f"深睡{sl['deep']:.1f}h")
            if sl.get("core"):  details.append(f"浅睡{sl['core']:.1f}h")
            if sl.get("rem"):   details.append(f"REM {sl['rem']:.1f}h")
            if sl.get("awake"): details.append(f"清醒{sl['awake']:.2f}h")
            if details:
                s += f"（{'  '.join(details)}）"
            parts.append(s)

    if "workouts" in by_type:
        for w in (by_type["workouts"] if isinstance(by_type["workouts"], list) else []):
            s = f"{w.get('type', '运动')} {w.get('duration_min', 0)}分钟"
            if w.get("energy"): s += f" {w['energy']}kcal"
            if w.get("hr_avg"): s += f" HR{w['hr_avg']}"
            parts.append(s)

    if "mood" in by_type:
        moods = by_type["mood"] if isinstance(by_type["mood"], list) else [by_type["mood"]]
        mood_strs = [m.get("valence_cn", "") for m in moods if m.get("valence_cn")]
        if mood_strs:
            parts.append(f"情绪 {' '.join(mood_strs)}")

    if not parts:
        return ""
    return " / ".join(parts)


# ── LLM 直调（yes.vg Responses API）─────────────────────────────────────────
async def call_llm(user_msg: str, system: str = "", model: str = "") -> str:
    payload: dict = {
        "model": model or CHEAP_MODEL,
        "input": [{"role": "user", "content": user_msg}],
    }
    if system:
        payload["instructions"] = system

    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            "https://co.yes.vg/v1/responses",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json=payload,
        )
        r.raise_for_status()
        data = r.json()

    for item in data.get("output", []):
        if item.get("type") == "message":
            for chunk in item.get("content", []):
                if chunk.get("type") == "output_text":
                    return chunk.get("text", "").strip()
    return ""


# ── 历史压缩 ──────────────────────────────────────────────────────────────────
async def maybe_compress_history() -> None:
    """_history 达到 HISTORY_MAX_MSGS 时，用 LLM 压缩成摘要，清空后保留摘要。"""
    global _history
    if len(_history) < HISTORY_MAX_MSGS:
        return

    log.info("历史压缩触发（当前 %d 条，阈值 %d）", len(_history), HISTORY_MAX_MSGS)
    history_text = "\n".join(
        f"{'用户' if m['role'] == 'user' else '助手'}：{m['content']}"
        for m in _history
    )
    try:
        summary = await call_llm(
            f"以下是对话记录：\n\n{history_text}\n\n"
            "请用3-5句话概括这段对话的主要内容和关键信息，保留重要细节。",
            "你是对话摘要助手，简洁总结对话内容。",
        )
    except Exception as e:
        log.warning("历史压缩 LLM 调用失败: %s，改为截断保留后10条", e)
        _history[:] = _history[-10:]
        return

    _history.clear()
    if summary:
        _history.append({"role": "assistant", "content": f"（之前对话摘要：{summary}）"})
        log.info("历史已压缩: %s…", summary[:80])
    else:
        log.warning("历史压缩：LLM 返回空，历史已清空")


# ── 被动响应（核心逻辑） ───────────────────────────────────────────────────────
async def _process_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _history, _last_msg_time

    user_text = update.message.text or ""
    now       = datetime.now()

    # 超过15分钟不活跃：清空历史，下次从记忆库重建上下文
    if _last_msg_time and (now - _last_msg_time).total_seconds() > INACTIVITY_SECS:
        idle_secs = (now - _last_msg_time).total_seconds()
        log.info("不活跃 %.0fs（>%ds），清空对话历史", idle_secs, INACTIVITY_SECS)
        _history.clear()
    _last_msg_time = now

    # 历史超过阈值则先压缩（在加入新消息之前）
    await maybe_compress_history()

    _history.append({"role": "user", "content": user_text})

    # 并行拉三路数据
    search_result, recent_result, health_block = await asyncio.gather(
        search_memory(user_text, limit=10),
        get_recent(limit=10),
        build_today_health_block(),
    )
    log.info(
        "三路数据拉取完成 | search=%d chars  recent=%d chars  health=%d chars",
        len(search_result), len(recent_result), len(health_block),
    )

    # 构建注入块，今日实时数据放最前面
    context_lines: list[str] = []
    if health_block:
        context_lines.append(f"今日实时数据：{health_block}")
    if search_result:
        context_lines.append(f"相关记忆：\n{search_result}")
    if recent_result and recent_result != search_result:
        context_lines.append(f"最近动态：\n{recent_result}")

    persona = await get_persona()
    base = (persona + "\n\n" + SYSTEM_PROMPT_RULES) if persona else SYSTEM_PROMPT_RULES
    system = ("\n".join(context_lines) + "\n\n" + base) if context_lines else base

    messages = [{"role": "system", "content": system}] + _history

    # 调用 LLM（经 kiwi-mem chat completions 接口）
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{KIWI_URL}/v1/chat/completions",
                headers={"Authorization": f"Bearer {KIWI_TOKEN}"},
                json={"model": CHEAP_MODEL, "messages": messages},
            )
            r.raise_for_status()
            reply = r.json()["choices"][0]["message"]["content"]
        _history.append({"role": "assistant", "content": reply})
        important = _is_important(user_text) or _is_important(reply)
        asyncio.create_task(save_conversation_memory(user_text, reply, important))
        log.info("回复生成完成 (important=%s, history=%d)", important, len(_history))
    except Exception as e:
        log.error("LLM 调用失败: %s", e)
        reply = "抱歉，出了点问题，稍后再试。"
        _history.pop()  # 回滚，下次重发不带脏记录

    await update.message.reply_text(reply)


# ── 消息队列（防并发限流） ────────────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != CHAT_ID:
        return
    await _msg_queue.put((update, context))


async def _queue_worker() -> None:
    """串行消费消息队列，防止并发请求触发 yes.vg 限流。"""
    log.info("消息队列工作器启动")
    while True:
        try:
            update, ctx = await _msg_queue.get()
            try:
                await _process_message(update, ctx)
            except Exception as e:
                log.error("_queue_worker 处理消息异常: %s", e)
            finally:
                _msg_queue.task_done()
        except asyncio.CancelledError:
            log.info("消息队列工作器收到取消信号，退出")
            break
        except Exception as e:
            log.error("_queue_worker 外层异常: %s", e)


# ── trigger 冷却 & 日志 ───────────────────────────────────────────────────────
async def _get_new_topics(topics: list[str]) -> list[str]:
    """返回过去 24h 内未曾推送过的 topics。"""
    if not topics:
        return []
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT DISTINCT topic FROM trigger_log
               WHERE topic = ANY($1::text[])
                 AND sent_at >= NOW() - INTERVAL '24 hours'""",
            topics,
        )
    sent = {r["topic"] for r in rows}
    return [t for t in topics if t not in sent]


async def _day_trigger_count() -> int:
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM trigger_log WHERE sent_at >= $1",
            today_start,
        )


async def _log_trigger(topic: str, content: str) -> None:
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO trigger_log (topic, content) VALUES ($1, $2)",
            topic, content,
        )


# ── 健康摘要（trigger 专用，含 topic 标注）────────────────────────────────────
async def _build_health_summary() -> tuple[str, list[str]]:
    today     = date.today()
    yesterday = today - timedelta(days=1)
    parts:  list[str] = []
    topics: list[str] = []

    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            today_rows = await conn.fetch(
                "SELECT metric_type, value_json FROM health_summary WHERE date = $1",
                today,
            )
            sleep_row = await conn.fetchrow(
                """SELECT value_json FROM health_summary
                   WHERE date = $1 AND metric_type = 'sleep'""",
                yesterday,
            )
    except Exception as e:
        log.error("_build_health_summary DB 查询失败: %s", e)
        return "", []

    by_type: dict = {}
    for row in today_rows:
        val = row["value_json"]
        by_type[row["metric_type"]] = val if isinstance(val, (dict, list)) else json.loads(val)

    if "sleep" not in by_type and sleep_row:
        val = sleep_row["value_json"]
        by_type["sleep"] = val if isinstance(val, (dict, list)) else json.loads(val)

    if "steps" in by_type:
        total = by_type["steps"].get("total", 0)
        if total:
            parts.append(f"今日步数 {total:,} 步")
            topics.append("步数")

    if "sleep" in by_type:
        sl = by_type["sleep"]
        total = sl.get("total", 0)
        if total:
            s = f"昨夜睡眠 {total:.1f} 小时"
            if sl.get("deep"): s += f"（深睡 {sl['deep']:.1f}h）"
            parts.append(s)
            topics.append("睡眠")

    if "mood" in by_type:
        moods = by_type["mood"] if isinstance(by_type["mood"], list) else [by_type["mood"]]
        mood_strs = [m.get("valence_cn", "") for m in moods if m.get("valence_cn")]
        if mood_strs:
            parts.append(f"近期情绪：{', '.join(mood_strs)}")
            topics.append("情绪")

    if "workouts" in by_type:
        wlist = by_type["workouts"] if isinstance(by_type["workouts"], list) else []
        if wlist:
            workout_strs = [f"{w.get('type','运动')} {w.get('duration_min',0)}分钟" for w in wlist]
            parts.append("运动：" + "、".join(workout_strs))
            topics.append("运动")

    return "；".join(parts), topics


# ── 主动触发 ──────────────────────────────────────────────────────────────────
async def trigger_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    log.info("trigger_job: 开始检查健康数据...")
    try:
        # 夜间静默：00:00–08:00 不推送
        now = datetime.now()
        if 0 <= now.hour < 8:
            log.info("trigger_job: 夜间静默（%02d:%02d），跳过", now.hour, now.minute)
            return

        summary, topics = await _build_health_summary()
        if not summary:
            log.info("trigger_job: 无健康数据，跳过")
            return

        # 找出 24h 内未推送过的 topics
        new_topics = await _get_new_topics(topics if topics else ["健康"])
        if not new_topics:
            log.info("trigger_job: 所有 topic(%s) 24h 内已推送，跳过", topics)
            return

        # 今日总推送上限 3 条
        day_count = await _day_trigger_count()
        if day_count >= 3:
            log.info("trigger_job: 今日已推送 %d 条（上限3），跳过", day_count)
            return

        # gpt-5.4-mini 做 YES/NO 判断
        judgment = await call_llm(
            f"Chris 的近期健康数据：{summary}\n\n"
            "判断是否需要主动联系 Chris（久坐 / 睡眠不足 / 情绪低落等）。"
            "只回答 YES 或 NO，后面加一句简短理由（中文）。",
            "你是健康助手判断器，根据数据做简短判断，不闲聊。",
        )
        log.info("trigger 判断: %s", judgment)

        if not judgment.upper().startswith("YES"):
            return

        health_block = await build_today_health_block()
        trigger_system = "你是 Chris 的私人助手。"
        if health_block:
            trigger_system = f"今日实时数据：{health_block}\n\n" + trigger_system

        message = await call_llm(
            f"健康数据：{summary}\n判断：{judgment}\n"
            "生成一条自然友善的中文消息主动发给 Chris，像朋友一样，50字以内，不要多余客套话。",
            trigger_system,
            model=TRIGGER_MODEL,
        )
        if message:
            await context.bot.send_message(chat_id=CHAT_ID, text=message)
            primary_topic = new_topics[0]
            await _log_trigger(primary_topic, message)
            log.info("trigger 已推送 [topic=%s]: %s", primary_topic, message)

    except Exception as e:
        log.error("trigger_job 异常: %s", e)


# ── 人设更新（每周一 UTC 00:05） ──────────────────────────────────────────────
async def persona_update(context: ContextTypes.DEFAULT_TYPE = None) -> str:
    log.info("persona_update: 开始生成人设更新...")

    mem_text = await get_recent(limit=50)
    if not mem_text:
        log.info("persona_update: 无记忆，跳过")
        return ""

    today = date.today().isoformat()

    persona = await call_llm(
        f"以下是关于 Chris 最近的 50 条记忆：\n\n{mem_text}\n\n"
        "基于以上记忆，生成一段「关于 Chris 的最新认知」，包含：\n"
        "- 他最近在忙什么\n"
        "- 情绪状态如何\n"
        "- 有什么新的习惯或变化\n"
        "- 需要特别关心的事\n\n"
        "用自然的中文写，不超过 200 字，像朋友之间的了解，不要用清单格式。",
        "你是一个深度了解 Chris 的朋友，根据记忆总结对他的最新认知。",
        model=TRIGGER_MODEL,
    )

    if not persona:
        log.warning("persona_update: 模型未返回内容")
        return ""

    title = f"Chris 人设更新-{today}"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{KIWI_URL}/debug/memories",
                headers={"Authorization": f"Bearer {KIWI_TOKEN}"},
                json={"title": title, "content": persona, "importance": 9},
            )
            r.raise_for_status()
            result = r.json()
        log.info("persona_update 已保存: %s（总记忆 %d 条）", title, result.get("total", 0))
    except Exception as e:
        log.error("persona_update 保存失败: %s", e)

    return persona


# ── /dev 命令（调用 HOST 侧 claude-runner） ───────────────────────────────────
async def handle_dev(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != CHAT_ID:
        return

    args = context.args
    if not args:
        await update.message.reply_text("用法：/dev <需求描述>")
        return

    requirement = " ".join(args)
    await update.message.reply_text("⚙️ 正在分析需求...")

    try:
        expanded = await call_llm(requirement, DEV_TASK_SYSTEM)
    except Exception as e:
        log.error("handle_dev 需求扩展失败: %s", e)
        await update.message.reply_text(f"❌ 需求分析失败：{e}")
        return

    if not expanded:
        await update.message.reply_text("❌ 需求扩展返回空，终止。")
        return

    preview = expanded[:300] + "…" if len(expanded) > 300 else expanded
    await update.message.reply_text(f"🚀 Claude Code 启动中...\n\n{preview}")

    try:
        async with httpx.AsyncClient(timeout=320) as c:
            r = await c.post(
                f"{DEV_RUNNER_URL}/run",
                headers={"Authorization": f"Bearer {KIWI_TOKEN}"},
                json={"task": expanded},
            )
            r.raise_for_status()
            data = r.json()
    except httpx.ConnectError:
        await update.message.reply_text(
            "❌ claude-runner 未启动，请在 Pi 上执行：\n"
            "sudo systemctl start claude-runner"
        )
        return
    except Exception as e:
        log.error("handle_dev runner 调用失败: %s", e)
        await update.message.reply_text(f"❌ runner 调用失败：{e}")
        return

    output = data.get("output") or data.get("error") or "（无输出）"
    if len(output) > 3000:
        output = output[:3000] + "\n\n…（输出过长，已截断）"

    await update.message.reply_text(f"✅ 完成：\n\n{output}")
    log.info("handle_dev 完成，输出 %d 字符", len(output))


# ── 入口 ─────────────────────────────────────────────────────────────────────
async def post_init(application: Application) -> None:
    await init_db()
    asyncio.create_task(_queue_worker())
    log.info("Bot 初始化完成，消息队列工作器已启动")


def main() -> None:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("dev", handle_dev))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # 启动 5 分钟后首次运行，之后每小时一次
    app.job_queue.run_repeating(trigger_job, interval=3600, first=300)

    # 每周一 UTC 00:05 生成人设更新
    app.job_queue.run_daily(
        persona_update,
        time=dtime(0, 5, 0, tzinfo=timezone.utc),
        days=(1,),
    )

    log.info("Bot 启动，监听 CHAT_ID=%d", CHAT_ID)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
