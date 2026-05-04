"""Single-MCP-server client wrapper around the official mcp SDK's stdio transport."""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from code_minions.mcp.config import MCPServerConfig


class MCPClientError(Exception):
    pass


class MCPClient:
    """Wraps one mcp-stdio subprocess + session. Synchronous facade over async SDK."""

    def __init__(self, cfg: MCPServerConfig):
        self._cfg = cfg
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None
        self._tools_cache: list[dict[str, Any]] | None = None

    def start(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._loop.run_until_complete(self._async_start())

    async def _async_start(self) -> None:
        self._stack = AsyncExitStack()
        params = StdioServerParameters(
            command=self._cfg.command,
            args=self._cfg.args,
            env=self._cfg.env or None,
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()

    def list_tools(self) -> list[dict[str, Any]]:
        if self._tools_cache is not None:
            return self._tools_cache
        assert self._loop and self._session
        result = self._loop.run_until_complete(self._session.list_tools())
        self._tools_cache = [
            {"name": t.name, "description": t.description or "", "input_schema": t.inputSchema or {"type": "object"}}
            for t in result.tools
        ]
        return self._tools_cache

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        assert self._loop and self._session
        result = self._loop.run_until_complete(
            self._session.call_tool(tool_name, arguments)
        )
        # mcp returns list[TextContent|...]
        parts: list[str] = []
        for item in result.content:
            if hasattr(item, "text"):
                parts.append(item.text)
        return "\n".join(parts)

    def stop(self) -> None:
        if self._loop and self._stack:
            self._loop.run_until_complete(self._stack.aclose())
            self._loop.close()
            self._loop = None
            self._session = None
            self._stack = None
