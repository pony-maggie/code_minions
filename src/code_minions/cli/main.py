"""Command-line interface for code_minions."""
from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.console import Console
from rich.table import Table

from code_minions.config import load_devflow_config
from code_minions.engine.engine import Engine, EngineError
from code_minions.engine.event_bus import Event, EventBus
from code_minions.engine.skill_runtime import SkillRuntime

app = typer.Typer(add_completion=False, no_args_is_help=True)
skill_app = typer.Typer(add_completion=False, no_args_is_help=True, help="Skill inspection commands.")
mcp_app = typer.Typer(add_completion=False, no_args_is_help=True, help="MCP server inspection commands.")
app.add_typer(skill_app, name="skill")
app.add_typer(mcp_app, name="mcp")
console = Console()


DEFAULT_AGENTS_MD = """# AGENTS.md

> 项目给 AI agent 的约定文件。越精确，AI 工作越稳。

## 技术栈
- 语言：
- 框架：

## 目录约定
- 源代码：`src/`
- 测试：`tests/`

## 测试命令
- 运行单元测试：`pytest`

## 编码规范
- Lint：
- 禁止项：

## 业务术语
（可选）
"""

DEFAULT_DEVFLOW_YAML = """version: 1

llm:
  default: anthropic
  providers:
    anthropic:
      model: claude-sonnet-4-6
      api_key_env: ANTHROPIC_API_KEY

workflow:
  default: prd-to-pr
  search_paths: [./workflows]

skills:
  search_paths: [./skills]
"""

DEFAULT_MCP_JSON = '{"mcpServers": {}}\n'


def _builtin_root() -> Path:
    return Path(__file__).resolve().parent.parent / "builtin"


def _workflow_search_paths(project_root: Path) -> list[Path]:
    cfg = load_devflow_config(project_root)
    return [*cfg.workflow_search_paths, _builtin_root() / "workflows"]


def _skill_search_paths(project_root: Path) -> list[Path]:
    cfg = load_devflow_config(project_root)
    return [*cfg.skill_search_paths, _builtin_root() / "skills"]


def _default_workflow(project_root: Path) -> str | None:
    return load_devflow_config(project_root).workflow_default


def _llm_display(project_root: Path) -> str:
    devflow = project_root / "devflow.yaml"
    if not devflow.exists():
        return "not configured"
    try:
        from code_minions.llm.config import load_llm_config
        cfg = load_llm_config(devflow)
    except Exception as e:
        return f"not configured ({e})"
    provider = cfg.providers[cfg.default]
    if provider.model:
        return f"{cfg.default}/{provider.model}"
    return cfg.default


def _make_engine(project_root: Path, event_bus: EventBus | None = None) -> Engine:
    from code_minions.llm.config import load_llm_config
    from code_minions.mcp.config import load_mcp_config
    from code_minions.mcp.pool import MCPClientPool

    llm = None
    mcp = None
    devflow = project_root / "devflow.yaml"
    if devflow.exists():
        try:
            cfg = load_llm_config(devflow)
            pc = cfg.providers[cfg.default]
            from code_minions.llm.litellm_backend import LiteLLMBackend
            llm = LiteLLMBackend(
                provider=cfg.default,
                default_model=pc.model,
                api_key=pc.api_key,
                api_base=pc.api_base,
            )
        except Exception as e:
            console.print(f"[yellow]warning:[/yellow] LLM not configured: {e}")

    mcp_json = project_root / ".mcp.json"
    if mcp_json.exists():
        try:
            mcp_cfg = load_mcp_config(mcp_json)
            mcp = MCPClientPool(mcp_cfg)
            mcp.start()
            import atexit
            atexit.register(mcp.stop)
        except Exception as e:
            console.print(f"[yellow]warning:[/yellow] MCP not configured: {e}")

    return Engine(
        project_root=project_root,
        skill_search_paths=_skill_search_paths(project_root),
        workflow_search_paths=_workflow_search_paths(project_root),
        runtime=SkillRuntime(),
        llm_backend=llm,
        mcp_pool=mcp,
        event_bus=event_bus,
    )


def _print_step_outputs(steps: list[dict[str, Any]]) -> None:
    rows = [s for s in steps if s.get("output_json")]
    if not rows:
        return
    typer.echo("outputs:")
    for s in rows:
        raw = s["output_json"]
        try:
            data = json.loads(raw)
            formatted = json.dumps(data, ensure_ascii=False, indent=2)
        except Exception:
            formatted = str(raw)
        typer.echo(f"{s['step_id']}:")
        typer.echo(formatted)


def _step_output(step: dict[str, Any]) -> dict[str, Any]:
    raw = step.get("output_json")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _print_step_findings(steps: list[dict[str, Any]]) -> None:
    rows: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for step in steps:
        output = _step_output(step)
        for finding in output.get("gate_findings") or []:
            if isinstance(finding, dict):
                rows.append((step["step_id"], output.get("agent_profile") or {}, finding))
    if not rows:
        return

    typer.echo("findings:")
    for step_id, agent_profile, finding in rows:
        profile_id = agent_profile.get("profile_id") or "unknown-profile"
        typer.echo(
            f"{step_id} [{profile_id}] "
            f"{finding.get('severity', 'error')} "
            f"{finding.get('stage', 'runtime')}/{finding.get('code', 'unknown')}: "
            f"{finding.get('message', '')}"
        )
        repair = finding.get("repair_hint")
        if repair:
            typer.echo(f"  repair: {repair}")


def _format_event_time(ts: datetime, tz: tzinfo | None = None) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    local = ts.astimezone(tz) if tz is not None else ts.astimezone()
    return local.strftime("%H:%M:%S")


def _format_status_time(ts: datetime | None, tz: tzinfo | None = None) -> str:
    if ts is None:
        return ""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    local = ts.astimezone(tz) if tz is not None else ts.astimezone()
    return local.strftime("%Y-%m-%d %H:%M:%S")


def _make_run_event_printer(engine: Engine) -> Any:
    seen_run_header = False

    def _on_event(event: Event) -> None:
        nonlocal seen_run_header
        if not seen_run_header:
            seen_run_header = True
            typer.echo(f"run: {event.run_id}")
            with contextlib.suppress(Exception):
                typer.echo(f"workspace: {engine.get_run_workspace_path(event.run_id)}")
            typer.echo("tip: inspect from another terminal with:")
            typer.echo(f"  code-minions status {event.run_id}")
            typer.echo("  code-minions list-runs")
        if event.kind == "step.status":
            ts = _format_event_time(event.ts)
            detail = event.payload.get("detail")
            suffix = f"  {detail}" if detail else ""
            typer.echo(f"[{ts}] {event.payload['step_id']}  {event.payload['status']}{suffix}")

    return _on_event


@app.command()
def init(
    path: Path = typer.Argument(Path("."), help="Project directory to initialize"),  # noqa: B008
) -> None:
    """Generate devflow.yaml, AGENTS.md, .mcp.json templates in the given dir."""
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    for filename, content in [
        ("devflow.yaml", DEFAULT_DEVFLOW_YAML),
        ("AGENTS.md", DEFAULT_AGENTS_MD),
        (".mcp.json", DEFAULT_MCP_JSON),
    ]:
        f = path / filename
        if f.exists():
            console.print(f"[yellow]skip existing[/yellow] {filename}")
            continue
        f.write_text(content)
        created.append(filename)

    (path / ".devflow").mkdir(exist_ok=True)
    gi = path / ".gitignore"
    existing = gi.read_text() if gi.exists() else ""
    if ".devflow/" not in existing:
        gi.write_text(existing + ("\n" if existing else "") + ".devflow/\n")
        created.append(".gitignore (+.devflow/)")

    for c in created:
        console.print(f"[green]created[/green] {c}")
    console.print("[bold]done.[/bold] next: fill AGENTS.md and configure .mcp.json, then run a workflow.")


@app.command()
def run(
    workflow: str | None = typer.Argument(  # noqa: B008
        None,
        help="Workflow name (defaults to devflow.yaml workflow.default)",
    ),
    inputs: list[str] = typer.Option(  # noqa: B008
        [], "--input", "-i", help="Input in key=value form; repeatable"
    ),
    project_root: Path = typer.Option(  # noqa: B008
        Path("."), "--project-root", help="Project root (defaults to CWD)"
    ),
) -> None:
    """Start a workflow run."""
    project_root = project_root.resolve()
    parsed = dict(item.split("=", 1) for item in inputs) if inputs else {}
    selected_workflow = workflow or _default_workflow(project_root)
    if not selected_workflow:
        console.print("[red]error:[/red] workflow argument is required when devflow.yaml has no workflow.default")
        raise typer.Exit(code=1)

    console.print(f"[bold]starting workflow:[/bold] {selected_workflow}")
    console.print(f"[bold]llm:[/bold] {_llm_display(project_root)}")
    console.print("This may take a while for LLM/code workflows.")
    bus = EventBus()
    engine = _make_engine(project_root, event_bus=bus)
    bus.subscribe(_make_run_event_printer(engine))
    try:
        run_id = engine.start_run(workflow=selected_workflow, inputs=parsed)
    except EngineError as e:
        console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(code=1) from e

    state = engine.get_run_state(run_id)
    console.print(f"status: [cyan]{state['status']}[/cyan]")
    _print_step_outputs(state["steps"])


@app.command()
def status(
    run_id: str = typer.Argument(..., help="Run id (e.g. r_abcd1234)"),  # noqa: B008
    project_root: Path = typer.Option(Path("."), "--project-root"),  # noqa: B008
) -> None:
    """Show a run's status and per-step progress."""
    engine = _make_engine(project_root.resolve())
    try:
        state = engine.get_run_state(run_id)
    except EngineError as e:
        console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(code=1) from e

    llm = state.get("llm") or "not recorded"
    console.print(
        f"[bold]{state['id']}[/bold]  workflow=[cyan]{state['workflow']}[/cyan]  "
        f"status=[cyan]{state['status']}[/cyan]  llm=[cyan]{llm}[/cyan]"
    )
    t = Table(show_header=True, header_style="bold")
    t.add_column("step", no_wrap=True)
    t.add_column("status", no_wrap=True)
    t.add_column("started", no_wrap=True)
    t.add_column("ended", no_wrap=True)
    t.add_column("detail", overflow="fold")
    t.add_column("error", overflow="fold")
    for s in state["steps"]:
        t.add_row(
            s["step_id"],
            s["status"],
            _format_status_time(s.get("started_at")),
            _format_status_time(s.get("ended_at")),
            s.get("detail") or "",
            s["error"] or "",
        )
    console.print(t)
    errors = [s for s in state["steps"] if s["error"]]
    if errors:
        typer.echo("errors:")
        for s in errors:
            typer.echo(f"{s['step_id']}: {s['error']}")
    _print_step_findings(state["steps"])
    _print_step_outputs(state["steps"])


@app.command("list-runs")
def list_runs(
    project_root: Path = typer.Option(Path("."), "--project-root"),  # noqa: B008
    limit: int = typer.Option(10, "--limit", "-n"),  # noqa: B008
) -> None:
    """List recent runs."""
    project_root = project_root.resolve()
    engine = _make_engine(project_root)
    runs = engine._store.list_runs(limit=limit)    # noqa: SLF001 - intentional
    t = Table(show_header=True, header_style="bold")
    t.add_column("id")
    t.add_column("workflow")
    t.add_column("status")
    t.add_column("llm")
    t.add_column("started")
    for r in runs:
        t.add_row(r["id"], r["workflow"], r["status"], r.get("llm") or "not recorded", str(r["started_at"]))
    console.print(t)


@app.command()
def resume(
    run_id: str = typer.Argument(...),  # noqa: B008
    project_root: Path = typer.Option(Path("."), "--project-root"),  # noqa: B008
) -> None:
    """Resume a failed or interrupted run."""
    engine = _make_engine(project_root.resolve())
    try:
        engine.resume_run(run_id)
    except EngineError as e:
        console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(code=1) from e
    state = engine.get_run_state(run_id)
    console.print(f"status: [cyan]{state['status']}[/cyan]")


@app.command()
def cancel(run_id: str, project_root: Path = typer.Option(Path("."))) -> None:  # noqa: B008
    """Mark a pending/running run as cancelled (advisory; doesn't interrupt running skills in v1)."""
    engine = _make_engine(project_root.resolve())
    engine.cancel_run(run_id)
    console.print(f"[yellow]cancelled[/yellow] {run_id}")


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host. Keep on 127.0.0.1 for security (no auth)."),  # noqa: B008
    port: int = typer.Option(8080, "--port"),  # noqa: B008
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev)."),  # noqa: B008
) -> None:
    """Start the local dashboard (FastAPI + HTMX)."""
    if host != "127.0.0.1":
        console.print(
            f"[red]WARNING:[/red] binding to {host}. This dashboard has NO authentication; "
            "do not expose it to untrusted networks."
        )
    import uvicorn
    uvicorn.run(
        "code_minions.web.app:app",
        host=host,
        port=port,
        reload=reload,
    )


FRONTMATTER_KEY_ALIASES = {
    "allowed-tools": "allowed_tools",
    "required-mcps": "required_mcps",
    "entrypoint-script": "entrypoint_script",
    "invokes-skills": "invokes_skills",
}


class CLISkillLoadError(Exception):
    """Raised when CLI skill inspection cannot read SKILL.md metadata."""


def _normalize_frontmatter_keys(raw: dict[str, Any]) -> dict[str, Any]:
    return {FRONTMATTER_KEY_ALIASES.get(k, k): v for k, v in raw.items()}


def _read_skill_frontmatter(directory: Path) -> tuple[dict[str, Any], str]:
    md = directory / "SKILL.md"
    if not md.exists():
        if (directory / "skill.yaml").exists():
            raise CLISkillLoadError(
                "old skill.yaml format detected; migrate metadata into "
                "SKILL.md frontmatter"
            )
        raise CLISkillLoadError(f"missing SKILL.md in {directory}")

    text = md.read_text()
    if not text.startswith("---\n"):
        if (directory / "skill.yaml").exists():
            raise CLISkillLoadError(
                "old skill.yaml format detected; migrate to SKILL.md frontmatter"
            )
        raise CLISkillLoadError("SKILL.md must start with YAML frontmatter")
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        raise CLISkillLoadError("SKILL.md frontmatter is not closed")
    try:
        raw = yaml.safe_load(parts[0][4:]) or {}
    except yaml.YAMLError as e:
        raise CLISkillLoadError(f"invalid SKILL.md frontmatter: {e}") from e
    if not isinstance(raw, dict):
        raise CLISkillLoadError("SKILL.md frontmatter must be a mapping")
    meta = _normalize_frontmatter_keys(raw)
    if not meta.get("name"):
        raise CLISkillLoadError("SKILL.md frontmatter must include name")
    return meta, parts[1].strip()


def _skill_dirs(base: Path) -> list[Path]:
    if not base.exists():
        return []
    return [
        d for d in sorted(base.iterdir())
        if d.is_dir() and ((d / "SKILL.md").exists() or (d / "skill.yaml").exists())
    ]


@skill_app.command("list")
def skill_list(
    project_root: Path = typer.Option(Path("."), "--project-root"),  # noqa: B008
) -> None:
    """List every skill discoverable from project + builtin search paths."""
    project_root = project_root.resolve()
    builtin_root = _builtin_root() / "skills"
    t = Table(show_header=True, header_style="bold")
    t.add_column("name")
    t.add_column("source")
    t.add_column("entrypoint")
    t.add_column("description")
    seen: set[str] = set()
    for base in _skill_search_paths(project_root):
        for d in _skill_dirs(base):
            source = "builtin" if base == builtin_root else "project"
            try:
                meta, _instructions = _read_skill_frontmatter(d)
            except CLISkillLoadError as e:
                t.add_row(d.name, source, "-", f"[red]load error:[/red] {e}")
                continue
            skill_name = str(meta["name"])
            if skill_name in seen:
                continue
            seen.add(skill_name)
            t.add_row(
                skill_name,
                source,
                str(meta.get("entrypoint_script") or "-"),
                str(meta.get("description") or "")[:60],
            )
    console.print(t)


@skill_app.command("info")
def skill_info(
    name: str = typer.Argument(..., help="Skill name"),  # noqa: B008
    project_root: Path = typer.Option(Path("."), "--project-root"),  # noqa: B008
) -> None:
    """Show a skill's metadata and SKILL.md contents."""
    project_root = project_root.resolve()
    for base in _skill_search_paths(project_root):
        cand = base / name
        if cand.is_dir():
            try:
                meta, instructions = _read_skill_frontmatter(cand)
            except CLISkillLoadError as e:
                console.print(f"[red]error:[/red] {e}")
                raise typer.Exit(code=1) from e
            console.print(f"[bold]{meta['name']}[/bold]")
            console.print(f"directory: {cand}")
            console.print(f"allowed_tools: {meta.get('allowed_tools', [])}")
            console.print(f"required_mcps: {meta.get('required_mcps', [])}")
            console.print(f"entrypoint_script: {meta.get('entrypoint_script') or '-'}")
            console.print(
                f"invokes_skills: {meta.get('invokes_skills', [])}  "
                "[dim](advisory in v0.1)[/dim]"
            )
            console.print(f"policies: {meta.get('policies', {})}")
            console.print(f"llm: {meta.get('llm', {})}")
            console.print(f"hooks: {meta.get('hooks', {})}")
            console.print()
            console.print("[bold]SKILL.md:[/bold]")
            console.print(instructions)
            return
    console.print(f"[red]skill not found:[/red] {name}")
    raise typer.Exit(code=1)


@skill_app.command("test")
def skill_test(
    name: str | None = typer.Argument(None, help="Skill name (omit to validate all)"),  # noqa: B008
    project_root: Path = typer.Option(Path("."), "--project-root"),  # noqa: B008
) -> None:
    """Load-and-validate skill directories (schema + required files)."""
    project_root = project_root.resolve()
    targets: list[Path] = []
    if name is None:
        for base in _skill_search_paths(project_root):
            targets.extend(_skill_dirs(base))
    else:
        for base in _skill_search_paths(project_root):
            cand = base / name
            if cand.is_dir():
                targets.append(cand)
                break
        if not targets:
            console.print(f"[red]skill not found:[/red] {name}")
            raise typer.Exit(code=1)

    failed = 0
    for d in targets:
        try:
            _read_skill_frontmatter(d)
        except CLISkillLoadError as e:
            console.print(f"[red]FAIL[/red]  {d.name}: {e}")
            failed += 1
        else:
            console.print(f"[green]OK[/green]    {d.name}")
    if failed:
        raise typer.Exit(code=1)


@mcp_app.command("list")
def mcp_list(
    project_root: Path = typer.Option(Path("."), "--project-root"),  # noqa: B008
) -> None:
    """List MCP servers configured in .mcp.json."""
    from code_minions.mcp.config import MCPConfigError, load_mcp_config

    project_root = project_root.resolve()
    try:
        cfg = load_mcp_config(project_root / ".mcp.json")
    except MCPConfigError as e:
        console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(code=1) from e
    t = Table(show_header=True, header_style="bold")
    t.add_column("name")
    t.add_column("command")
    t.add_column("args")
    for srv_name, s in cfg.servers.items():
        t.add_row(srv_name, s.command, " ".join(s.args))
    console.print(t)


@mcp_app.command("test")
def mcp_test(
    name: str | None = typer.Argument(None, help="Server name (omit to test all)"),  # noqa: B008
    project_root: Path = typer.Option(Path("."), "--project-root"),  # noqa: B008
) -> None:
    """Start MCP server(s) and list their tools to verify connectivity."""
    from code_minions.mcp.config import MCPConfigError, load_mcp_config
    from code_minions.mcp.pool import MCPClientPool

    project_root = project_root.resolve()
    try:
        cfg = load_mcp_config(project_root / ".mcp.json")
    except MCPConfigError as e:
        console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(code=1) from e
    if name is not None and name not in cfg.servers:
        console.print(f"[red]no such server:[/red] {name}")
        raise typer.Exit(code=1)
    allowed = [name] if name else list(cfg.servers.keys())
    if not allowed:
        console.print("[yellow]no MCP servers configured.[/yellow]")
        return
    pool = MCPClientPool(cfg, allowed_servers=allowed)
    failed = False
    try:
        pool.start()
        tools_by_server = pool.list_tools()
    except Exception as e:
        console.print(f"[red]FAIL[/red] {e}")
        failed = True
        tools_by_server = {}
    try:
        for srv, tools in tools_by_server.items():
            console.print(f"[green]OK[/green]  {srv}  ({len(tools)} tools)")
            for tool in tools[:10]:
                desc = (tool.get("description") or "").replace("\n", " ")[:60]
                console.print(f"  - {tool['name']}: {desc}")
            if len(tools) > 10:
                console.print(f"  ... ({len(tools) - 10} more)")
    finally:
        pool.stop()
    if failed:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
