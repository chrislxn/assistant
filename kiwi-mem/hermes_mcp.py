"""
Hermes MCP Server — 受限 Agent 接入层
===========================================================

Hermes 是一个受限的外部 agent，权限远低于主 MCP agent (claude_mcp)。

核心限制：
- 读取：不返回 sealed / restricted 记忆
- 写入观察：仅通过 append_event (POST /events)，不直接写 memories
- 提取记忆：只写 memory_candidates(status='pending')，不写 committed memory
- core_blocks：只读白名单（response_policy + active_projects），不可写
- get_context：actor_scope='hermes_agent'，每次调用写 memory_access_log

挂载路径：/hermes → URL：/hermes/mcp
工具：hermes_observe, hermes_propose_memory, hermes_search, hermes_get_recent, hermes_get_context
"""

import os
import json
import httpx
import uuid
from mcp.server.fastmcp import FastMCP

# ============================================================
# 配置
# ============================================================

GATEWAY_PORT = int(os.getenv("PORT", "8080"))
GATEWAY_BASE = f"http://127.0.0.1:{GATEWAY_PORT}"
_access_token = os.getenv("ACCESS_TOKEN", "")
GATEWAY_HEADERS = {"Authorization": f"Bearer {_access_token}"} if _access_token else {}

HERMES_ACTOR = "hermes_agent"
HERMES_TRUST = "assistant_inferred"
EXCLUDE_PRIVACY = "sealed,restricted"
HERMES_CORE_WHITELIST = {"response_policy", "active_projects"}

# ============================================================
# Hermes MCP Server
# ============================================================

mcp_hermes = FastMCP("Hermes Agent (restricted)", stateless_http=True)


# ---- 工具 1：写入观察事件 ----

@mcp_hermes.tool()
async def hermes_observe(content: str, observation_type: str = "general_observation") -> str:
    """
    写入一条观察事件到 memory_events（不直接写 memories 表）。

    参数：
    - content: 观察内容（必填），描述观察到的事实、状态或事件
    - observation_type: 观察类型，可选 general_observation / health_observation / behavior_observation / environment_observation（默认 general_observation）

    返回 event_id，后续可通过 hermes_propose_memory 引用此 event 来提案记忆。

    注意：此工具只写事件，不生成记忆。记忆需要通过 hermes_propose_memory 单独提案。
    """
    if not content.strip():
        return "内容不能为空。"

    try:
        async with httpx.AsyncClient(timeout=15, headers=GATEWAY_HEADERS) as client:
            resp = await client.post(
                f"{GATEWAY_BASE}/events",
                json={
                    "content_text": content.strip(),
                    "event_type": observation_type.strip(),
                    "source_type": "hermes_agent",
                    "source_trust": HERMES_TRUST,
                    "actor": HERMES_ACTOR,
                    "privacy_level": "personal",
                },
            )
            data = resp.json()

        if "error" in data:
            return f"观察写入失败：{data['error']}"

        event_id = data.get("event_id", "?")
        return f"✅ 观察已记录：event_id={event_id}\n类型: {observation_type}\n内容: {content[:100]}{'…' if len(content) > 100 else ''}"

    except Exception as e:
        return f"观察写入出错：{str(e)}"


# ---- 工具 2：提案记忆候选 ----

@mcp_hermes.tool()
async def hermes_propose_memory(
    rendered_text: str,
    memory_type: str = "unknown",
    subject_key: str = "",
    predicate_key: str = "",
    importance: int = 5,
    confidence: float = 0.7,
    source_event_ids: str = "",
) -> str:
    """
    提取并提案一条长期记忆候选（写入 memory_candidates，status='pending'）。

    不会直接写 committed memory。提案需要通过人工审核后才能提交。

    参数：
    - rendered_text: 记忆的自然语言表述（必填），如"用户每周三晚上有瑜伽课"
    - memory_type: 记忆类型，可选 identity_fact / preference / project_state / episodic_event / health_pattern / relationship_context 等（默认 unknown）
    - subject_key: 主题键（可选），如 "person:self"、"project:kiwi-mem"
    - predicate_key: 谓词键（可选），如 "weekly_schedule"、"architecture_choice"
    - importance: 重要度 1-10（默认 5）
    - confidence: 置信度 0.0-1.0（默认 0.7）
    - source_event_ids: 逗号分隔的 event_id 列表（可选），用于溯源

    返回 candidate_id。
    """
    if not rendered_text.strip():
        return "rendered_text 不能为空。"

    if importance < 1:
        importance = 1
    elif importance > 10:
        importance = 10

    if confidence < 0.0:
        confidence = 0.0
    elif confidence > 1.0:
        confidence = 1.0

    event_ids = [e.strip() for e in source_event_ids.split(",") if e.strip()]

    try:
        async with httpx.AsyncClient(timeout=15, headers=GATEWAY_HEADERS) as client:
            resp = await client.post(
                f"{GATEWAY_BASE}/candidates",
                json={
                    "rendered_text": rendered_text.strip(),
                    "memory_type": memory_type.strip(),
                    "subject_key": subject_key.strip(),
                    "predicate_key": predicate_key.strip(),
                    "importance": importance,
                    "confidence": confidence,
                    "source_event_ids": event_ids,
                    "source_trust": HERMES_TRUST,
                    "extractor_name": HERMES_ACTOR,
                    "extractor_version": "1.0",
                    "privacy_level": "personal",
                },
            )
            data = resp.json()

        if "error" in data:
            return f"记忆提案失败：{data['error']}"

        cid = data.get("candidate_id", "?")
        return (
            f"✅ 记忆候选已提案：candidate_id={cid}\n"
            f"类型: {memory_type} | 重要度: {importance} | 置信度: {confidence}\n"
            f"内容: {rendered_text[:100]}{'…' if len(rendered_text) > 100 else ''}\n"
            f"状态: pending（等待审核）"
        )

    except Exception as e:
        return f"记忆提案出错：{str(e)}"


# ---- 工具 3：搜索记忆（受限） ----

@mcp_hermes.tool()
async def hermes_search(query: str, limit: int = 10) -> str:
    """
    搜索记忆（受限：不返回 sealed/restricted 记忆）。

    参数：
    - query: 搜索关键词或自然语言描述
    - limit: 返回条数上限（默认10，最大50）

    返回匹配的公开/个人记忆，不含敏感或密封记忆。
    """
    if limit > 50:
        limit = 50

    try:
        async with httpx.AsyncClient(timeout=15, headers=GATEWAY_HEADERS) as client:
            resp = await client.get(
                f"{GATEWAY_BASE}/debug/memories",
                params={
                    "q": query,
                    "limit": limit,
                    "exclude_privacy": EXCLUDE_PRIVACY,
                },
            )
            data = resp.json()

        if "error" in data:
            return f"搜索失败：{data['error']}"

        results = data.get("results", [])
        if not results:
            return f"没有找到与「{query}」相关的记忆。"

        lines = [f"找到 {len(results)} 条相关记忆（共 {data.get('total_memories', '?')} 条）：\n"]
        for i, mem in enumerate(results, 1):
            title = mem.get("title", "")
            title_tag = f"【{title}】" if title else ""
            date = mem.get("created_at", "")[:10]
            importance = mem.get("importance", "?")
            memory_type = mem.get("memory_type", "fragment")
            content = mem.get("content", "")

            lines.append(
                f"{i}. [{date}] {title_tag}{content}\n"
                f"   重要度: {importance} | 类型: {memory_type}"
            )

        return "\n".join(lines)

    except Exception as e:
        return f"搜索出错：{str(e)}"


# ---- 工具 4：获取最近记忆（受限） ----

@mcp_hermes.tool()
async def hermes_get_recent(limit: int = 20) -> str:
    """
    获取最近的记忆（受限：不返回 sealed/restricted 记忆）。

    参数：
    - limit: 返回条数（默认20，最大50）

    用于快速了解最近的公开/个人记忆动态。
    """
    if limit > 50:
        limit = 50

    try:
        async with httpx.AsyncClient(timeout=15, headers=GATEWAY_HEADERS) as client:
            resp = await client.get(
                f"{GATEWAY_BASE}/debug/memories",
                params={
                    "limit": limit,
                    "exclude_privacy": EXCLUDE_PRIVACY,
                },
            )
            data = resp.json()

        if "error" in data:
            return f"获取失败：{data['error']}"

        results = data.get("results", [])
        if not results:
            return "记忆库为空（或无可访问的记忆）。"

        lines = [f"最近 {len(results)} 条记忆（共 {data.get('total_memories', '?')} 条，已排除 sealed/restricted）：\n"]
        for i, mem in enumerate(results, 1):
            title = mem.get("title", "")
            title_tag = f"【{title}】" if title else ""
            date = mem.get("created_at", "")[:10]
            content = mem.get("content", "")

            lines.append(f"{i}. [{date}] {title_tag}{content[:80]}")

        return "\n".join(lines)

    except Exception as e:
        return f"获取出错：{str(e)}"


# ---- 工具 5：获取上下文（核心工具） ----

@mcp_hermes.tool()
async def hermes_get_context(query: str = "", search_limit: int = 10, recent_limit: int = 10) -> str:
    """
    获取 Hermes agent 的完整上下文包 — 包含 core_blocks（仅白名单）、搜索记忆、最近记忆。

    每次调用自动写入 memory_access_log（actor='hermes_agent'）。
    不返回 sealed/restricted 记忆。

    参数：
    - query: 可选的搜索关键词（留空则只获取最近记忆和 core_blocks）
    - search_limit: 搜索结果条数（默认10）
    - recent_limit: 最近记忆条数（默认10）

    返回结构化上下文，可直接注入 Hermes 的 system prompt。
    """
    session_id = str(uuid.uuid4())
    core_blocks_text = ""
    search_text = ""
    recent_text = ""

    # 1. 获取白名单 core_blocks
    try:
        async with httpx.AsyncClient(timeout=15, headers=GATEWAY_HEADERS) as client:
            resp = await client.get(f"{GATEWAY_BASE}/core-blocks")
            data = resp.json()

        blocks = data.get("core_blocks", [])
        if blocks:
            parts = []
            for b in blocks:
                bk = b.get("block_key", "")
                if bk not in HERMES_CORE_WHITELIST:
                    continue
                ct = b.get("content_text", "")
                if ct and ct.strip():
                    parts.append(f"[Core memory: {bk}]\n{ct.strip()}\n[/Core memory]")
            if parts:
                core_blocks_text = "\n\n".join(parts)
    except Exception:
        pass

    # 2. 搜索记忆（受限）
    if query.strip():
        try:
            async with httpx.AsyncClient(timeout=15, headers=GATEWAY_HEADERS) as client:
                resp = await client.get(
                    f"{GATEWAY_BASE}/debug/memories",
                    params={
                        "q": query.strip(),
                        "limit": search_limit,
                        "exclude_privacy": EXCLUDE_PRIVACY,
                    },
                )
                data = resp.json()

            results = data.get("results", [])
            if results:
                lines = ["相关记忆："]
                for i, mem in enumerate(results, 1):
                    title = mem.get("title", "")
                    title_tag = f"【{title}】" if title else ""
                    content = mem.get("content", "")
                    lines.append(f"{i}. {title_tag}{content}")
                search_text = "\n".join(lines)
        except Exception:
            pass

    # 3. 最近记忆（受限）
    try:
        async with httpx.AsyncClient(timeout=15, headers=GATEWAY_HEADERS) as client:
            resp = await client.get(
                f"{GATEWAY_BASE}/debug/memories",
                params={
                    "limit": recent_limit,
                    "exclude_privacy": EXCLUDE_PRIVACY,
                },
            )
            data = resp.json()

        results = data.get("results", [])
        if results:
            lines = ["最近动态："]
            for i, mem in enumerate(results, 1):
                title = mem.get("title", "")
                title_tag = f"【{title}】" if title else ""
                content = mem.get("content", "")
                lines.append(f"{i}. {title_tag}{content[:120]}")
            recent_text = "\n".join(lines)
    except Exception:
        pass

    # 4. 写 memory_access_log（fire-and-forget）
    try:
        async with httpx.AsyncClient(timeout=10, headers=GATEWAY_HEADERS) as client:
            await client.post(
                f"{GATEWAY_BASE}/events/access-log",
                json={
                    "actor": HERMES_ACTOR,
                    "retrieval_mode": "hermes_agent",
                    "intent": "hermes_context",
                    "query_text": query.strip() or "(no query)",
                    "session_id": session_id,
                    "core_block_keys": ["response_policy", "active_projects"],
                },
            )
    except Exception:
        pass

    # 5. 组装上下文
    sections = []
    if core_blocks_text:
        sections.append(core_blocks_text)
    if search_text:
        sections.append(search_text)
    if recent_text:
        sections.append(recent_text)

    if not sections:
        return "（无可用的上下文：无 core_blocks，无记忆，无最近动态）"

    header = f"[Hermes Context — session={session_id[:8]}…]\n"
    return header + "\n\n".join(sections)


# ============================================================
# 获取 ASGI app
# ============================================================

def get_hermes_mcp_app():
    """
    Hermes 受限 Agent MCP。
    挂载路径：/hermes → URL：/hermes/mcp
    5 个工具：hermes_observe, hermes_propose_memory, hermes_search, hermes_get_recent, hermes_get_context
    """
    return mcp_hermes.streamable_http_app()
