from __future__ import annotations

from pathlib import Path

import pytest

from code_minions.mcp.config import MCPConfigError, load_mcp_config


def test_load_minimal(tmp_path: Path):
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {"fs": {"command": "echo", "args": ["ok"]}}}')
    cfg = load_mcp_config(tmp_path / ".mcp.json")
    assert "fs" in cfg.servers
    assert cfg.servers["fs"].command == "echo"
    assert cfg.servers["fs"].args == ["ok"]


def test_load_allowed_arguments(tmp_path: Path):
    (tmp_path / ".mcp.json").write_text(
        """
{
  "mcpServers": {
    "jira": {
      "command": "jira-mcp",
      "allowed_arguments": {
        "create_issue": {
          "project_key": ["ABC"],
          "issue_type": ["Epic", "Story"]
        }
      }
    }
  }
}
""".strip()
    )
    cfg = load_mcp_config(tmp_path / ".mcp.json")

    assert cfg.servers["jira"].allowed_arguments == {
        "create_issue": {
            "project_key": ["ABC"],
            "issue_type": ["Epic", "Story"],
        }
    }


def test_empty_servers(tmp_path: Path):
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {}}')
    cfg = load_mcp_config(tmp_path / ".mcp.json")
    assert cfg.servers == {}


def test_invalid_json(tmp_path: Path):
    (tmp_path / ".mcp.json").write_text("{not json")
    with pytest.raises(MCPConfigError):
        load_mcp_config(tmp_path / ".mcp.json")


def test_missing_file(tmp_path: Path):
    with pytest.raises(MCPConfigError, match="not found"):
        load_mcp_config(tmp_path / ".mcp.json")
