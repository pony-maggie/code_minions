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


def _is_usable_base_branch(branch: str, current_branch: str) -> bool:
    return bool(branch) and branch != "(unknown)" and branch != current_branch


def _ref_exists(workdir: Path, ref: str) -> bool:
    try:
        _run_git(workdir, "show-ref", "--verify", "--quiet", ref)
    except RuntimeError:
        return False
    return True


def _base_branch(workdir: Path, current_branch: str) -> str:
    try:
        ref = _run_git(workdir, "symbolic-ref", "refs/remotes/origin/HEAD")
        branch = ref.rsplit("/", 1)[-1]
        if _is_usable_base_branch(branch, current_branch):
            return branch
    except RuntimeError:
        pass

    try:
        out = _run_git(workdir, "remote", "show", "origin")
    except RuntimeError:
        out = ""
    for line in out.splitlines():
        if "HEAD branch:" in line:
            branch = line.split("HEAD branch:", 1)[1].strip()
            if _is_usable_base_branch(branch, current_branch):
                return branch

    for candidate in ("main", "master"):
        if _ref_exists(workdir, f"refs/remotes/origin/{candidate}") or _ref_exists(workdir, f"refs/heads/{candidate}"):
            return candidate

    return "main"


def _extract_pull_url(text: str) -> tuple[int, str] | None:
    match = re.search(r"https://github\.com/[^/\s]+/[^/\s]+/pull/(?P<number>\d+)", text)
    if match:
        return int(match.group("number")), match.group(0)
    return None


def _parse_create_pr_response(raw: str) -> tuple[int, str]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        extracted = _extract_pull_url(raw)
        if extracted:
            return extracted
        preview = raw[:500] if raw else "<empty response>"
        raise RuntimeError(f"github MCP returned non-JSON PR response: {preview}") from exc
    url = data.get("html_url") or data.get("url")
    number = data.get("number")
    if number is None and isinstance(url, str):
        match = re.search(r"/pull/(?P<number>\d+)(?:$|[/?#])", url)
        if match:
            number = match.group("number")
    try:
        return int(number), url
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"github MCP PR response missing PR number/url: {data!r}") from exc


def _find_existing_pr(mcp_pool: Any, owner: str, repo: str, head: str) -> tuple[int, str] | None:
    for tool in ("list_pull_requests", "search_pull_requests"):
        try:
            raw = mcp_pool.call_tool(
                "github",
                tool,
                {
                    "owner": owner,
                    "repo": repo,
                    "head": f"{owner}:{head}",
                    "state": "open",
                },
            )
        except Exception:
            continue
        extracted = _extract_pull_url(raw)
        if extracted:
            return extracted
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else data.get("items") or data.get("pull_requests") or []
        for item in items:
            url = item.get("html_url") or item.get("url")
            number = item.get("number")
            if number is None and isinstance(url, str):
                match = re.search(r"/pull/(?P<number>\d+)(?:$|[/?#])", url)
                if match:
                    number = match.group("number")
            if number is not None and url:
                return int(number), url
    return None


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
    try:
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
        return _parse_create_pr_response(raw)
    except RuntimeError as exc:
        if "pull request already exists" in str(exc).lower():
            existing = _find_existing_pr(mcp_pool, owner, repo, head)
            if existing:
                return existing
        raise


def run(ctx):
    workdir = ctx.workdir
    branch = _current_branch(workdir)
    origin_url = _origin_url(workdir)
    owner, repo = _parse_github_repo(origin_url)
    base_branch = _base_branch(workdir, branch)
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
