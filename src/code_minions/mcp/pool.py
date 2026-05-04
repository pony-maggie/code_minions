"""MCPClientPool: start/stop multiple MCP servers and dispatch tool calls."""
from __future__ import annotations

import contextlib
from typing import Any

from code_minions.mcp.client import MCPClient, MCPClientError
from code_minions.mcp.config import MCPConfig


class MCPClientPool:
    def __init__(self, config: MCPConfig, allowed_servers: list[str] | None = None):
        self._config = config
        self._allowed = set(allowed_servers) if allowed_servers is not None else None
        self._clients: dict[str, MCPClient] = {}

    def start(self) -> None:
        for name, server_cfg in self._config.servers.items():
            if self._allowed is not None and name not in self._allowed:
                continue
            client = MCPClient(server_cfg)
            client.start()
            self._clients[name] = client

    def stop(self) -> None:
        for c in self._clients.values():
            with contextlib.suppress(Exception):
                c.stop()
        self._clients.clear()

    def __enter__(self) -> MCPClientPool:
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    def list_tools(self) -> dict[str, list[dict[str, Any]]]:
        return {name: c.list_tools() for name, c in self._clients.items()}

    def call_tool(self, server: str, tool: str, arguments: dict[str, Any]) -> str:
        c = self._clients.get(server)
        if c is None:
            raise MCPClientError(f"MCP server {server!r} is not running (not in allowed list?)")
        return c.call_tool(tool, arguments)
