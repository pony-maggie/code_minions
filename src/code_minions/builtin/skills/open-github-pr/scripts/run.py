"""open-github-pr entrypoint."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from code_minions.engine.skill_runtime import SkillExecutionError


def _run_git(workdir: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise RuntimeError(f"git {' '.join(args)} failed: {msg}")
    return result.stdout.strip()


def _current_branch(workdir: Path) -> str:
    return _run_git(workdir, "rev-parse", "--abbrev-ref", "HEAD")


def _origin_url(workdir: Path) -> str:
    return _run_git(workdir, "remote", "get-url", "origin")


def _parse_github_repo(origin_url: str) -> tuple[str, str]:
    ssh = re.match(r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?$", origin_url)
    if ssh:
        return ssh.group("owner"), ssh.group("repo")
    https = re.match(r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?$", origin_url)
    if https:
        return https.group("owner"), https.group("repo")
    raise RuntimeError(f"origin is not a GitHub remote: {origin_url}")


def _base_branch(workdir: Path) -> str:
    try:
        ref = _run_git(workdir, "symbolic-ref", "refs/remotes/origin/HEAD")
        return ref.rsplit("/", 1)[-1]
    except RuntimeError:
        try:
            out = _run_git(workdir, "remote", "show", "origin")
        except RuntimeError:
            return "main"
        for line in out.splitlines():
            if "HEAD branch:" in line:
                return line.split("HEAD branch:", 1)[1].strip()
        return "main"


def _push_branch(workdir: Path, branch: str) -> None:
    _run_git(workdir, "push", "-u", "origin", branch)


def _read_report(workdir: Path, report_path: str) -> str:
    return (workdir / report_path).read_text()[:12000]


def _build_pr_title(epic_title: str) -> str:
    return f"[code-minions] {epic_title}"


def _build_pr_body(
    prd: str,
    branch: str,
    base_branch: str,
    tickets_output: dict[str, Any],
    implement_results: list[dict[str, Any]],
    report_text: str,
) -> str:
    epic = tickets_output.get("epic") or {}
    tickets = tickets_output.get("tickets") or []
    lines = [
        "## Summary",
        f"- PRD: `{prd}`",
        f"- Branch: `{branch}`",
        f"- Base branch: `{base_branch}`",
        "",
        "## Jira",
    ]
    if epic.get("key") and epic.get("url"):
        lines.append(f"- Epic: [{epic['key']}]({epic['url']})")
    for ticket in tickets:
        if ticket.get("ticket_key") and ticket.get("url"):
            lines.append(f"- [{ticket['ticket_key']}]({ticket['url']})")
    lines.extend(["", "## Commits"])
    for item in implement_results:
        sha = item.get("commit_sha", "")
        if sha:
            lines.append(f"- `{sha}`")
    lines.extend(["", "## Report", "", report_text])
    return "\n".join(lines).strip()


def _resolve_create_pr_tool(mcp_pool: Any) -> str:
    tools = (mcp_pool.list_tools().get("github") or [])
    names = {tool["name"] for tool in tools}
    for candidate in ("create_pull_request", "create_pr"):
        if candidate in names:
            return candidate
    raise RuntimeError("github MCP server does not expose a PR creation tool")


def _create_pr(
    mcp_pool: Any,
    owner: str,
    repo: str,
    title: str,
    head: str,
    base: str,
    body: str,
) -> tuple[int, str]:
    tool = _resolve_create_pr_tool(mcp_pool)
    raw = mcp_pool.call_tool(
        "github",
        tool,
        {
            "owner": owner,
            "repo": repo,
            "title": title,
            "head": head,
            "base": base,
            "body": body,
        },
    )
    data = json.loads(raw)
    return int(data["number"]), data["html_url"]


def run(ctx):
    workdir = ctx.workdir
    branch = _current_branch(workdir)
    origin_url = _origin_url(workdir)
    owner, repo = _parse_github_repo(origin_url)
    base_branch = _base_branch(workdir)
    title = _build_pr_title(ctx.inputs["epic_title"])
    report_text = _read_report(workdir, ctx.inputs["report_path"])
    body = _build_pr_body(
        prd=ctx.inputs["prd"],
        branch=branch,
        base_branch=base_branch,
        tickets_output=ctx.inputs["tickets_output"],
        implement_results=ctx.inputs["implement_results"],
        report_text=report_text,
    )

    pushed = False
    try:
        _push_branch(workdir, branch)
        pushed = True
        pr_number, pr_url = _create_pr(ctx.mcp_pool, owner, repo, title, branch, base_branch, body)
    except Exception as exc:
        raise SkillExecutionError(str(exc), {
            "branch": branch,
            "base_branch": base_branch,
            "origin_url": origin_url,
            "repo_owner": owner,
            "repo_name": repo,
            "pushed": pushed,
            "pr_url": "",
            "pr_number": 0,
            "title": title,
            "body_preview": body[:500],
            "error": str(exc),
        }) from exc

    return {
        "branch": branch,
        "base_branch": base_branch,
        "origin_url": origin_url,
        "repo_owner": owner,
        "repo_name": repo,
        "pushed": True,
        "pr_url": pr_url,
        "pr_number": pr_number,
        "title": title,
        "body_preview": body[:500],
        "error": "",
    }
