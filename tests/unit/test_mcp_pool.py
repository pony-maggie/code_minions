"""Integration-ish test: spin up a real mcp python stdio server fixture and call it."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from code_minions.mcp.config import MCPConfig, MCPServerConfig
from code_minions.mcp.pool import MCPClientPool

FAKE_SERVER_SCRIPT = '''
import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as t

app = Server("fake")

@app.list_tools()
async def _tools():
    return [t.Tool(name="ping", description="ping", inputSchema={"type":"object"})]

@app.call_tool()
async def _call(name, arguments):
    return [t.TextContent(type="text", text="pong:" + str(arguments))]

async def main():
    async with stdio_server() as (r, w):
        await app.run(r, w, app.create_initialization_options())

asyncio.run(main())
'''


@pytest.fixture
def fake_mcp_script(tmp_path: Path) -> Path:
    p = tmp_path / "fake_mcp.py"
    p.write_text(FAKE_SERVER_SCRIPT)
    return p


def test_pool_start_stop_and_call(fake_mcp_script: Path) -> None:
    cfg = MCPConfig(servers={
        "fake": MCPServerConfig(
            name="fake",
            command=sys.executable,
            args=[str(fake_mcp_script)],
        ),
    })
    with MCPClientPool(cfg) as pool:
        tools = pool.list_tools()
        assert "ping" in [t["name"] for t in tools["fake"]]
        result = pool.call_tool("fake", "ping", {"x": 1})
        assert "pong" in result and "x" in result


def test_pool_respects_allowed_list(fake_mcp_script: Path) -> None:
    cfg = MCPConfig(servers={
        "fake": MCPServerConfig(name="fake", command=sys.executable, args=[str(fake_mcp_script)]),
    })
    pool = MCPClientPool(cfg, allowed_servers=[])   # empty allowlist → nothing started
    pool.start()
    try:
        assert pool.list_tools() == {}
    finally:
        pool.stop()
