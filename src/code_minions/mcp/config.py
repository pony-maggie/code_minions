"""Load .mcp.json (Claude Code / Cursor / Cline standard format)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


class MCPConfigError(Exception):
    pass


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MCPConfig:
    servers: dict[str, MCPServerConfig]


def load_mcp_config(path: Path) -> MCPConfig:
    p = Path(path)
    if not p.exists():
        raise MCPConfigError(f".mcp.json not found: {p}")
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise MCPConfigError(f"invalid JSON: {e}") from e

    servers_raw = data.get("mcpServers") or {}
    servers: dict[str, MCPServerConfig] = {}
    for name, s in servers_raw.items():
        cmd = s.get("command")
        if not cmd:
            raise MCPConfigError(f"server {name!r} missing 'command'")
        servers[name] = MCPServerConfig(
            name=name,
            command=cmd,
            args=list(s.get("args") or []),
            env=dict(s.get("env") or {}),
        )
    return MCPConfig(servers=servers)
