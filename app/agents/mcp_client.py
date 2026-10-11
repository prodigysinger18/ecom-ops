"""Bounded stdio client; only server-selected read-only tools are callable."""
import asyncio
import json
import os
import sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from app.core.auth import Role
from app.core.config import settings

TOOLS = frozenset({"query_order", "query_logistics", "query_product", "check_after_sale_policy"})


class McpFailure(RuntimeError):
    pass


async def _call(name, arguments, role):
    env = dict(os.environ)
    env["MCP_STAFF_TOKEN"] = settings.admin_api_key if role == Role.ADMIN else settings.agent_api_key
    # Pass the active database URL, including absolute SQLite location, to the child.
    env["DATABASE_URL"] = settings.database_url
    params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server"],
                                  env=env, cwd=str(Path(__file__).resolve().parents[2]))
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            available = await session.list_tools()
            if name not in {tool.name for tool in available.tools if tool.annotations and tool.annotations.readOnlyHint}:
                raise McpFailure("tool_not_advertised_readonly")
            result = await session.call_tool(name, arguments)
            if result.isError:
                raise McpFailure("remote_tool_error")
            data = result.structuredContent
            if data is None:
                data = json.loads(next(item.text for item in result.content if item.type == "text"))
            if not isinstance(data, dict) or data.get("error") or not isinstance(data.get("request_id"), str):
                raise McpFailure("invalid_remote_result")
            return data


def call_tool(name, arguments, role, timeout):
    if name not in TOOLS:
        raise McpFailure("tool_not_allowed")
    if timeout <= 0:
        raise McpFailure("deadline_exceeded")
    async def bounded():
        return await asyncio.wait_for(_call(name, arguments, role), timeout=min(timeout, settings.mcp_call_timeout_seconds))
    try:
        return asyncio.run(bounded())
    except Exception:
        raise McpFailure("mcp_call_failed") from None
