"""Run a real MCP stdio handshake and a read-only product call."""
import asyncio
import os
import sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from app.core.config import settings


async def check():
    env = dict(os.environ)
    env["MCP_STAFF_TOKEN"] = settings.agent_api_key
    params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server"],
                                   env=env, cwd=str(Path(__file__).resolve().parents[1]))
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool("query_product", {"product_id":1001})
            if result.isError:
                raise RuntimeError("MCP tool failed")
            print(f"mcp_handshake=ok tools={len(tools.tools)} product_call=ok")


if __name__ == "__main__":
    asyncio.run(check())
