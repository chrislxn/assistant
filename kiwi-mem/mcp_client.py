"""
mcp_client.py — MCP 客户端模块（v2）
================================
让 gateway 作为 MCP 客户端，连接外部 MCP 服务器，获取工具并执行调用。

支持传输格式：
  - Streamable HTTP（远程部署，主要场景）
  - SSE（旧版远程兼容）
  - stdio（本地进程，预留接口）

核心功能：
  - 连接 MCP 服务器，列出可用工具
  - 将 MCP 工具 schema 转换为 OpenAI function calling 格式
  - 执行 tool_call，返回结果
  - 工具列表缓存（避免每次请求都重新连接）
"""

import os
import json
import time
import asyncio
from typing import Optional
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.sse import sse_client

# ============================================================
# 工具缓存
# ============================================================

# { url: { "tools": [...], "mcp_tools": [...], "timestamp": float } }
_tool_cache: dict = {}
_CACHE_TTL = 300  # 5 分钟缓存


def _cache_valid(url: str) -> bool:
    if url not in _tool_cache:
        return False
    return (time.time() - _tool_cache[url]["timestamp"]) < _CACHE_TTL


def clear_tool_cache(url: str = None):
    """清除工具缓存"""
    if url:
        _tool_cache.pop(url, None)
    else:
        _tool_cache.clear()


# ============================================================
# 连接 MCP 服务器 & 列出工具
# ============================================================

async def _connect_and_list(url: str, transport: str = "streamable_http") -> list:
    """
    连接 MCP 服务器并获取工具列表
    返回 MCP Tool 对象列表
    """
    try:
        if transport == "sse":
            async with sse_client(url) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    return list(result.tools)
        else:
            # streamable_http (default)
            async with streamablehttp_client(url) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    return list(result.tools)
    except Exception as e:
        print(f"❌ MCP 连接失败 [{url}]: {e}")
        return []


def _mcp_tool_to_openai(tool, server_url: str) -> dict:
    """
    将 MCP Tool schema 转换为 OpenAI function calling 格式
    在 function name 中不包含 server 信息（MCP tool name 本身已有前缀）
    """
    # 处理 inputSchema
    input_schema = tool.inputSchema or {}
    if isinstance(input_schema, dict):
        # 确保有 type: object
        schema = {**input_schema}
        if "type" not in schema:
            schema["type"] = "object"
    else:
        schema = {"type": "object", "properties": {}}

    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": (tool.description or "")[:1024],
            "parameters": schema,
        },
    }


async def get_tools_for_servers(servers: list[dict]) -> tuple[list[dict], dict]:
    """
    获取多个 MCP 服务器的工具列表

    参数：
      servers: [{"url": "https://...", "transport": "streamable_http", "name": "..."}, ...]

    返回：
      (openai_tools, tool_map)
      - openai_tools: OpenAI function calling 格式的工具列表
      - tool_map: { tool_name: {"url": server_url, "transport": transport} }
    """
    openai_tools = []
    tool_map = {}  # tool_name → server info

    for server in servers:
        url = server.get("url", "")
        transport = server.get("transport", "streamable_http")
        name = server.get("name", url)

        if not url:
            continue

        # 检查缓存
        if _cache_valid(url):
            cached = _tool_cache[url]
            for oa_tool in cached["tools"]:
                tool_name = oa_tool["function"]["name"]
                openai_tools.append(oa_tool)
                tool_map[tool_name] = {"url": url, "transport": transport, "server_name": name}
            print(f"🔧 MCP [{name}]: {len(cached['tools'])} 工具 (缓存)")
            continue

        # 连接获取
        print(f"🔧 MCP [{name}]: 连接中...")
        mcp_tools = await _connect_and_list(url, transport)

        if mcp_tools:
            oa_tools = []
            for t in mcp_tools:
                oa_tool = _mcp_tool_to_openai(t, url)
                oa_tools.append(oa_tool)
                tool_map[t.name] = {"url": url, "transport": transport, "server_name": name}
            
            openai_tools.extend(oa_tools)

            # 缓存
            _tool_cache[url] = {
                "tools": oa_tools,
                "mcp_tools": mcp_tools,
                "timestamp": time.time(),
            }
            print(f"🔧 MCP [{name}]: {len(oa_tools)} 工具 ✓")
        else:
            print(f"⚠️ MCP [{name}]: 无工具或连接失败")

    return openai_tools, tool_map


# ============================================================
# 执行工具调用
# ============================================================

async def call_tool(tool_name: str, arguments: dict, tool_map: dict) -> str:
    """
    执行一次 MCP 工具调用

    参数：
      tool_name: 工具名称（如 "notion_search"）
      arguments: 工具参数
      tool_map: get_tools_for_servers 返回的映射

    返回：
      工具执行结果的文本
    """
    if tool_name not in tool_map:
        return f"错误：未知工具 {tool_name}"

    server_info = tool_map[tool_name]
    url = server_info["url"]
    transport = server_info["transport"]
    server_name = server_info.get("server_name", url)

    print(f"🔨 调用工具 [{server_name}]: {tool_name}({json.dumps(arguments, ensure_ascii=False)[:200]})")

    try:
        if transport == "sse":
            async with sse_client(url) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)
                    return _format_tool_result(result)
        else:
            # streamable_http
            async with streamablehttp_client(url) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)
                    return _format_tool_result(result)

    except Exception as e:
        error_msg = f"工具调用失败 [{tool_name}]: {str(e)}"
        print(f"❌ {error_msg}")
        return error_msg


def _format_tool_result(result) -> str:
    """将 MCP CallToolResult 格式化为文本"""
    if not result or not result.content:
        return "(空结果)"

    parts = []
    for block in result.content:
        if hasattr(block, "text"):
            parts.append(block.text)
        elif hasattr(block, "data"):
            parts.append(f"[二进制数据: {getattr(block, 'mimeType', 'unknown')}]")
        else:
            parts.append(str(block))

    return "\n".join(parts)


# ============================================================
# 批量工具调用（同服务器复用连接，跨服务器并发）
# ============================================================

async def call_tools_batch(calls: list, tool_map: dict) -> dict:
    """
    批量执行 MCP 工具调用。
    - 同一服务器的多个调用复用同一个连接（省去反复握手）
    - 不同服务器的调用通过 asyncio.gather 并发执行

    参数:
      calls: [{"id": "...", "name": "...", "args": {...}}, ...]
      tool_map: { tool_name: {"url": ..., "transport": ..., "server_name": ...} }

    返回:
      { call_id: result_text }
    """
    if not calls:
        return {}

    # 按服务器 URL 分组
    server_groups = {}
    for c in calls:
        info = tool_map.get(c["name"], {})
        url = info.get("url", "")
        if not url:
            continue
        if url not in server_groups:
            server_groups[url] = {
                "transport": info.get("transport", "streamable_http"),
                "name": info.get("server_name", url),
                "calls": [],
            }
        server_groups[url]["calls"].append(c)

    results = {}

    async def _run_batch(url, transport, server_name, batch_calls):
        """同一服务器的工具调用：只建一次连接，顺序执行"""
        try:
            if transport == "sse":
                async with sse_client(url) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        for c in batch_calls:
                            try:
                                result = await session.call_tool(c["name"], c["args"])
                                results[c["id"]] = _format_tool_result(result)
                            except Exception as e:
                                results[c["id"]] = f"工具调用失败 [{c['name']}]: {e}"
            else:
                async with streamablehttp_client(url) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        for c in batch_calls:
                            try:
                                result = await session.call_tool(c["name"], c["args"])
                                results[c["id"]] = _format_tool_result(result)
                            except Exception as e:
                                results[c["id"]] = f"工具调用失败 [{c['name']}]: {e}"

            print(f"🔧 MCP [{server_name}]: {len(batch_calls)} 个工具调用完成 ✓")

        except Exception as e:
            print(f"❌ MCP [{server_name}] 连接失败: {e}")
            for c in batch_calls:
                if c["id"] not in results:
                    results[c["id"]] = f"服务器连接失败: {e}"

    # 不同服务器并发执行
    tasks = [
        _run_batch(url, g["transport"], g["name"], g["calls"])
        for url, g in server_groups.items()
    ]
    if tasks:
        await asyncio.gather(*tasks)

    # 填充未知工具的结果
    for c in calls:
        if c["id"] not in results:
            results[c["id"]] = f"错误：未知工具 {c['name']}"

    return results


# ============================================================
# Tool Call 循环（核心）
# ============================================================

async def run_tool_call_loop(
    messages: list[dict],
    tools: list[dict],
    tool_map: dict,
    api_url: str,
    api_key: str,
    model: str,
    temperature: float = 0.7,
    max_rounds: int = 10,
    on_tool_call: callable = None,  # 回调：每次 tool_call 时通知
) -> tuple[list[dict], Optional[dict]]:
    """
    执行 tool_call 循环直到模型返回普通文本。

    工作方式：
      1. 非流式请求 LLM（带 tools）
      2. 如果返回 tool_calls → 执行 → 加入历史 → 重复
      3. 如果返回普通文本 → 结束

    参数：
      messages: 当前消息历史
      tools: OpenAI function calling 格式的工具列表
      tool_map: 工具名 → 服务器信息的映射
      api_url: LLM API 地址
      api_key: API Key
      model: 模型名
      temperature: 温度
      max_rounds: 最大循环次数（防止无限循环）
      on_tool_call: 可选回调 async fn(tool_name, arguments, result)

    返回：
      (updated_messages, final_response)
      - updated_messages: 包含所有 tool_call 历史的完整消息列表
      - final_response: 如果循环内产生了最终回复则返回，否则 None（需要再流式请求一次）
    """
    import httpx

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if "openrouter" in api_url:
        headers["HTTP-Referer"] = os.getenv("EXTRA_REFERER", "https://ai-memory-gateway.local")
        headers["X-Title"] = "Eveille-Chat"

    current_messages = list(messages)

    for round_num in range(max_rounds):
        body = {
            "model": model,
            "messages": current_messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": temperature,
            "stream": False,
        }

        print(f"🔄 Tool loop round {round_num + 1}: sending {len(tools)} tools, {len(current_messages)} messages to {model}")

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(api_url, headers=headers, json=body)

            if resp.status_code != 200:
                error_text = resp.text[:500]
                print(f"❌ LLM 请求失败 (round {round_num + 1}): {resp.status_code} {error_text}")
                return current_messages, None

            data = resp.json()

        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "")

        # 检查是否有 tool_calls
        tool_calls = message.get("tool_calls", [])
        print(f"🔄 Round {round_num + 1} result: finish_reason={finish_reason}, tool_calls={len(tool_calls)}, has_content={bool(message.get('content'))}")

        if not tool_calls:
            # 没有 tool_call → 模型给出了最终回复
            if message.get("content"):
                current_messages.append({
                    "role": "assistant",
                    "content": message["content"],
                })
                return current_messages, message
            else:
                return current_messages, None

        # 有 tool_calls → 执行工具
        print(f"🔄 Tool call round {round_num + 1}: {len(tool_calls)} 个工具调用")

        # 先把 assistant 的 tool_calls 消息加入历史
        current_messages.append({
            "role": "assistant",
            "content": message.get("content") or None,
            "tool_calls": tool_calls,
        })

        # 执行每个 tool_call
        for tc in tool_calls:
            tc_id = tc.get("id", "")
            func = tc.get("function", {})
            tc_name = func.get("name", "")
            tc_args_str = func.get("arguments", "{}")

            try:
                tc_args = json.loads(tc_args_str)
            except json.JSONDecodeError:
                tc_args = {}

            # 执行工具
            result_text = await call_tool(tc_name, tc_args, tool_map)

            # 回调通知
            if on_tool_call:
                try:
                    await on_tool_call(tc_name, tc_args, result_text)
                except Exception:
                    pass

            # 加入历史
            current_messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": result_text[:8000],  # 截断过长结果
            })

    print(f"⚠️ Tool call 循环达到上限 ({max_rounds} 轮)")
    return current_messages, None
