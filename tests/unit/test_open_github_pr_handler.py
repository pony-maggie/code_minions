"""Unit tests for the open-github-pr entrypoint."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _load_entrypoint():
    import code_minions

    root = Path(code_minions.__file__).resolve().parent / "builtin" / "skills" / "open-github-pr"
    spec = importlib.util.spec_from_file_location("ogp_entrypoint", root / "scripts" / "run.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _ctx(tmp_git_repo: Path) -> SimpleNamespace:
    mcp = MagicMock()
    ctx = SimpleNamespace()
    ctx.inputs = {
        "prd": "./my-prd.md",
        "epic_title": "Q2 feature pack",
        "tickets_output": {
            "epic": {"key": "ABC-1", "url": "https://jira.example.com/browse/ABC-1"},
            "tickets": [
                {"task_id": "T1", "ticket_key": "ABC-2", "url": "https://jira.example.com/browse/ABC-2"}
            ],
            "errors": [],
        },
        "implement_results": [
            {"commit_sha": "abc123", "files_changed": ["x.py"]},
            {"commit_sha": "def456", "files_changed": ["y.py"]},
        ],
        "report_path": "report.md",
    }
    ctx.workdir = tmp_git_repo
    ctx.mcp_pool = mcp
    return ctx


def test_open_github_pr_happy_path(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "report.md").write_text("# Report\n\nAll good\n")

    def fake_run(cmd, cwd=None, capture_output=None, text=None, check=False, timeout=None):
        joined = " ".join(cmd)
        if joined == "git rev-parse --abbrev-ref HEAD":
            return MagicMock(returncode=0, stdout="code-minions/r_123\n", stderr="")
        if joined == "git remote get-url origin":
            return MagicMock(returncode=0, stdout="git@github.com:acme/demo.git\n", stderr="")
        if joined == "git symbolic-ref refs/remotes/origin/HEAD":
            return MagicMock(returncode=0, stdout="refs/remotes/origin/main\n", stderr="")
        if joined == "git push -u origin code-minions/r_123":
            return MagicMock(returncode=0, stdout="pushed\n", stderr="")
        raise AssertionError(joined)

    monkeypatch.setattr("subprocess.run", fake_run)

    ctx = _ctx(tmp_git_repo)
    ctx.mcp_pool.list_tools.return_value = {
        "github": [
            {
                "name": "create_pull_request",
                "description": "Create a pull request",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string"},
                        "repo": {"type": "string"},
                        "title": {"type": "string"},
                        "head": {"type": "string"},
                        "base": {"type": "string"},
                        "body": {"type": "string"},
                    },
                },
            }
        ]
    }
    ctx.mcp_pool.call_tool.return_value = (
        '{"number": 42, "html_url": "https://github.com/acme/demo/pull/42"}'
    )

    out = entrypoint.run(ctx)
    assert out["branch"] == "code-minions/r_123"
    assert out["base_branch"] == "main"
    assert out["repo_owner"] == "acme"
    assert out["repo_name"] == "demo"
    assert out["pushed"] is True
    assert out["pr_url"] == "https://github.com/acme/demo/pull/42"
    assert out["pr_number"] == 42
    assert out["error"] == ""


def test_open_github_pr_fails_when_origin_is_not_github(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "report.md").write_text("# Report\n")

    def fake_run(cmd, cwd=None, capture_output=None, text=None, check=False, timeout=None):
        joined = " ".join(cmd)
        if joined == "git rev-parse --abbrev-ref HEAD":
            return MagicMock(returncode=0, stdout="code-minions/r_123\n", stderr="")
        if joined == "git remote get-url origin":
            return MagicMock(returncode=0, stdout="git@gitlab.example.com:acme/demo.git\n", stderr="")
        raise AssertionError(joined)

    monkeypatch.setattr("subprocess.run", fake_run)
    ctx = _ctx(tmp_git_repo)

    with pytest.raises(RuntimeError, match="origin is not a GitHub remote"):
        entrypoint.run(ctx)


def test_open_github_pr_marks_pushed_true_when_push_succeeds_but_pr_creation_fails(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "report.md").write_text("# Report\n")

    def fake_run(cmd, cwd=None, capture_output=None, text=None, check=False, timeout=None):
        joined = " ".join(cmd)
        if joined == "git rev-parse --abbrev-ref HEAD":
            return MagicMock(returncode=0, stdout="code-minions/r_123\n", stderr="")
        if joined == "git remote get-url origin":
            return MagicMock(returncode=0, stdout="https://github.com/acme/demo.git\n", stderr="")
        if joined == "git symbolic-ref refs/remotes/origin/HEAD":
            return MagicMock(returncode=0, stdout="refs/remotes/origin/main\n", stderr="")
        if joined == "git push -u origin code-minions/r_123":
            return MagicMock(returncode=0, stdout="pushed\n", stderr="")
        raise AssertionError(joined)

    monkeypatch.setattr("subprocess.run", fake_run)

    ctx = _ctx(tmp_git_repo)
    ctx.mcp_pool.list_tools.return_value = {
        "github": [
            {
                "name": "create_pull_request",
                "description": "Create a pull request",
                "input_schema": {"type": "object"},
            }
        ]
    }
    ctx.mcp_pool.call_tool.side_effect = RuntimeError("github mcp failed")

    with pytest.raises(Exception) as exc_info:
        entrypoint.run(ctx)
    err = exc_info.value
    assert err.output["pushed"] is True
    assert err.output["pr_url"] == ""
    assert "github mcp failed" in str(err)


def test_open_github_pr_falls_back_to_create_pr_tool_name(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "report.md").write_text("# Report\n\nFallback tool\n")

    def fake_run(cmd, cwd=None, capture_output=None, text=None, check=False, timeout=None):
        joined = " ".join(cmd)
        if joined == "git rev-parse --abbrev-ref HEAD":
            return MagicMock(returncode=0, stdout="code-minions/r_456\n", stderr="")
        if joined == "git remote get-url origin":
            return MagicMock(returncode=0, stdout="https://github.com/acme/demo.git\n", stderr="")
        if joined == "git symbolic-ref refs/remotes/origin/HEAD":
            return MagicMock(returncode=0, stdout="refs/remotes/origin/main\n", stderr="")
        if joined == "git push -u origin code-minions/r_456":
            return MagicMock(returncode=0, stdout="pushed\n", stderr="")
        raise AssertionError(joined)

    monkeypatch.setattr("subprocess.run", fake_run)

    ctx = _ctx(tmp_git_repo)
    ctx.mcp_pool.list_tools.return_value = {
        "github": [
            {
                "name": "create_pr",
                "description": "Create a PR",
                "input_schema": {"type": "object"},
            }
        ]
    }
    ctx.mcp_pool.call_tool.return_value = (
        '{"number": 77, "html_url": "https://github.com/acme/demo/pull/77"}'
    )

    out = entrypoint.run(ctx)

    ctx.mcp_pool.call_tool.assert_called_once()
    assert ctx.mcp_pool.call_tool.call_args.args[1] == "create_pr"
    assert out["pr_number"] == 77
    assert out["pr_url"] == "https://github.com/acme/demo/pull/77"


def test_open_github_pr_ignores_unknown_or_current_remote_head_for_base_branch(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "report.md").write_text("# Report\n\nFallback base\n")

    def fake_run(cmd, cwd=None, capture_output=None, text=None, check=False, timeout=None):
        joined = " ".join(cmd)
        if joined == "git rev-parse --abbrev-ref HEAD":
            return MagicMock(returncode=0, stdout="code-minions/r_789\n", stderr="")
        if joined == "git remote get-url origin":
            return MagicMock(returncode=0, stdout="https://github.com/acme/demo.git\n", stderr="")
        if joined == "git symbolic-ref refs/remotes/origin/HEAD":
            return MagicMock(returncode=1, stdout="", stderr="not a symbolic ref")
        if joined == "git remote show origin":
            return MagicMock(returncode=0, stdout="  HEAD branch: code-minions/r_789\n", stderr="")
        if joined == "git show-ref --verify --quiet refs/remotes/origin/main":
            return MagicMock(returncode=0, stdout="", stderr="")
        if joined == "git push -u origin code-minions/r_789":
            return MagicMock(returncode=0, stdout="pushed\n", stderr="")
        raise AssertionError(joined)

    monkeypatch.setattr("subprocess.run", fake_run)

    ctx = _ctx(tmp_git_repo)
    ctx.mcp_pool.list_tools.return_value = {
        "github": [
            {
                "name": "create_pull_request",
                "description": "Create a PR",
                "input_schema": {"type": "object"},
            }
        ]
    }
    ctx.mcp_pool.call_tool.return_value = (
        '{"number": 88, "html_url": "https://github.com/acme/demo/pull/88"}'
    )

    out = entrypoint.run(ctx)

    assert out["base_branch"] == "main"
    assert ctx.mcp_pool.call_tool.call_args.args[2]["base"] == "main"


def test_parse_create_pr_response_accepts_github_pull_url_text() -> None:
    entrypoint = _load_entrypoint()

    number, url = entrypoint._parse_create_pr_response(  # noqa: SLF001
        "Pull request created: https://github.com/acme/demo/pull/123"
    )

    assert number == 123
    assert url == "https://github.com/acme/demo/pull/123"


def test_parse_create_pr_response_reports_non_json_raw_preview() -> None:
    entrypoint = _load_entrypoint()

    with pytest.raises(RuntimeError, match="github MCP returned non-JSON PR response: denied"):
        entrypoint._parse_create_pr_response("denied")  # noqa: SLF001


def test_parse_create_pr_response_accepts_github_mcp_url_only_shape() -> None:
    entrypoint = _load_entrypoint()

    number, url = entrypoint._parse_create_pr_response(  # noqa: SLF001
        '{"id": "3621496493", "url": "https://github.com/acme/demo/pull/1"}'
    )

    assert number == 1
    assert url == "https://github.com/acme/demo/pull/1"


def test_create_pr_returns_existing_pr_when_github_reports_duplicate() -> None:
    entrypoint = _load_entrypoint()
    mcp = MagicMock()
    mcp.list_tools.return_value = {
        "github": [{"name": "create_pull_request", "description": "Create a PR", "input_schema": {}}]
    }
    mcp.call_tool.side_effect = [
        (
            "failed to create pull request: POST https://api.github.com/repos/acme/demo/pulls: "
            "422 Validation Failed [{Resource:PullRequest Field: Code:custom "
            "Message:A pull request already exists for acme:code-minions/r_123.}]"
        ),
        '[{"number": 9, "html_url": "https://github.com/acme/demo/pull/9"}]',
    ]

    number, url = entrypoint._create_pr(  # noqa: SLF001
        mcp,
        "acme",
        "demo",
        "Title",
        "code-minions/r_123",
        "main",
        "Body",
    )

    assert number == 9
    assert url == "https://github.com/acme/demo/pull/9"
    assert mcp.call_tool.call_args_list[1].args[1] == "list_pull_requests"
