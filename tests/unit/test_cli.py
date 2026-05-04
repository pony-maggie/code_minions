"""Smoke tests for the CLI (no real LLM / MCP calls)."""
from __future__ import annotations

import builtins
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from typer.testing import CliRunner

from code_minions.cli import main
from code_minions.cli.main import app
from code_minions.store.run_store import RunStore
from code_minions.types import RunStatus, StepStatus


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "init" in result.output
    assert "run" in result.output
    assert "status" in result.output


def test_cli_init_creates_files(tmp_git_repo):
    runner = CliRunner()
    result = runner.invoke(app, ["init", str(tmp_git_repo)])
    assert result.exit_code == 0
    assert (tmp_git_repo / "devflow.yaml").exists()
    assert (tmp_git_repo / "AGENTS.md").exists()
    assert (tmp_git_repo / ".mcp.json").exists()


def test_run_uses_devflow_default_workflow_from_configured_path(tmp_git_repo):
    flows = tmp_git_repo / "flows"
    flows.mkdir()
    (flows / "custom-hello.yaml").write_text(
        """name: custom-hello
description: Custom workflow path smoke test.
inputs:
  name: {type: string, required: true}
steps:
  - id: greet
    skill: hello-world
    inputs:
      name: $inputs.name
"""
    )
    (tmp_git_repo / "devflow.yaml").write_text(
        """version: 1
workflow:
  default: custom-hello
  search_paths: [./flows]
skills:
  search_paths: [./skills]
"""
    )

    result = CliRunner().invoke(app, ["run", "--project-root", str(tmp_git_repo), "--input", "name=world"])

    assert result.exit_code == 0
    assert "status: success" in result.output


def test_run_explicit_workflow_overrides_devflow_default(tmp_git_repo):
    (tmp_git_repo / "devflow.yaml").write_text(
        """version: 1
workflow:
  default: missing-workflow
  search_paths: [./workflows]
skills:
  search_paths: [./skills]
"""
    )

    result = CliRunner().invoke(
        app,
        ["run", "hello-world", "--project-root", str(tmp_git_repo), "--input", "name=world"],
    )

    assert result.exit_code == 0
    assert "status: success" in result.output


def test_run_prints_successful_step_outputs(tmp_git_repo):
    result = CliRunner().invoke(
        app,
        ["run", "hello-world", "--project-root", str(tmp_git_repo), "--input", "name=world"],
    )

    assert result.exit_code == 0
    assert "outputs:" in result.output
    assert "greet:" in result.output
    assert '"greeting": "hello, world!"' in result.output


def test_run_prints_live_progress_events(tmp_git_repo):
    result = CliRunner().invoke(
        app,
        ["run", "hello-world", "--project-root", str(tmp_git_repo), "--input", "name=world"],
    )

    assert result.exit_code == 0
    assert "starting workflow: hello-world" in result.output
    assert "code-minions status" in result.output
    assert "greet  running" in result.output
    assert "greet  success" in result.output


def test_live_progress_prints_step_detail(monkeypatch):
    event = main.Event(
        run_id="r_1234",
        kind="step.status",
        payload={
            "step_id": "implement[17]",
            "status": "running",
            "detail": "T18: Add calculation history search",
        },
        ts=datetime(2026, 4, 26, 8, 12, 32, tzinfo=UTC),
    )
    echoed: list[str] = []
    monkeypatch.setattr(main.typer, "echo", echoed.append)

    class EngineStub:
        def get_run_workspace_path(self, run_id):
            return "/tmp/workspace"

    printer = main._make_run_event_printer(EngineStub())

    printer(event)

    assert any(
        "implement[17]  running  T18: Add calculation history search" in line
        for line in echoed
    )

def test_run_prints_configured_llm_provider(tmp_git_repo, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    (tmp_git_repo / "devflow.yaml").write_text(
        """version: 1
llm:
  default: openai
  providers:
    openai:
      model: gpt-5.5
      api_key_env: OPENAI_API_KEY
"""
    )

    result = CliRunner().invoke(
        app,
        ["run", "hello-world", "--project-root", str(tmp_git_repo), "--input", "name=world"],
    )

    assert result.exit_code == 0
    assert "llm: openai/gpt-5.5" in result.output


def test_event_time_format_converts_utc_to_local_timezone():
    ts = datetime(2026, 4, 26, 1, 16, 43, tzinfo=UTC)

    assert main._format_event_time(ts, ZoneInfo("Asia/Hong_Kong")) == "09:16:43"


def test_hello_world_without_llm_does_not_import_litellm_backend(tmp_git_repo, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "code_minions.llm.litellm_backend":
            raise AssertionError("LiteLLM backend should not be imported for hello-world without LLM config")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    result = CliRunner().invoke(
        app,
        ["run", "hello-world", "--project-root", str(tmp_git_repo), "--input", "name=world"],
    )

    assert result.exit_code == 0
    assert "status: success" in result.output


def test_skill_search_paths_use_devflow_config(tmp_path):
    (tmp_path / "devflow.yaml").write_text(
        """version: 1
skills:
  search_paths: [./agent-skills]
"""
    )

    paths = main._skill_search_paths(tmp_path)

    assert paths[0] == tmp_path.resolve() / "agent-skills"
    assert paths[-1].name == "skills"


def test_status_prints_full_step_error(tmp_path):
    db_path = tmp_path / ".devflow" / "runs.db"
    db_path.parent.mkdir()
    store = RunStore(db_path)
    run_id = store.create_run("hello-world", {"name": "world"})
    long_error = (
        "worktree creation failed: git worktree add failed: fatal: invalid reference: HEAD "
        "because the repository has no commits yet"
    )
    store.upsert_step(run_id, "__setup__", StepStatus.FAILED, error=long_error)
    store.set_run_status(run_id, RunStatus.FAILED)

    result = CliRunner().invoke(app, ["status", run_id, "--project-root", str(tmp_path)])

    assert result.exit_code == 0
    assert "repository has no commits yet" in result.output


def test_status_prints_step_detail(tmp_path):
    db_path = tmp_path / ".devflow" / "runs.db"
    db_path.parent.mkdir()
    store = RunStore(db_path)
    run_id = store.create_run("prd-to-commit", {"prd": "./prd.md"})
    store.upsert_step(
        run_id,
        "implement[17]",
        StepStatus.RUNNING,
        detail="T18: Add calculation history search",
    )
    store.set_run_status(run_id, RunStatus.RUNNING)

    result = CliRunner().invoke(app, ["status", run_id, "--project-root", str(tmp_path)])

    assert result.exit_code == 0
    assert "detail" in result.output
    for word in ("T18:", "Add", "calculation", "history", "search"):
        assert word in result.output


def test_status_prints_step_start_and_end_times(tmp_path):
    db_path = tmp_path / ".devflow" / "runs.db"
    db_path.parent.mkdir()
    store = RunStore(db_path)
    run_id = store.create_run("prd-to-commit", {"prd": "./prd.md"})
    store.upsert_step(run_id, "parse", StepStatus.RUNNING)
    store.upsert_step(run_id, "parse", StepStatus.SUCCESS, output={"ok": True})
    store.upsert_step(run_id, "implement", StepStatus.RUNNING)
    store.set_run_status(run_id, RunStatus.RUNNING)

    result = CliRunner().invoke(app, ["status", run_id, "--project-root", str(tmp_path)])

    assert result.exit_code == 0
    assert "started" in result.output
    assert "ended" in result.output
    assert "parse" in result.output
    assert "implement" in result.output
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", result.output)


def test_status_prints_run_llm(tmp_path):
    db_path = tmp_path / ".devflow" / "runs.db"
    db_path.parent.mkdir()
    store = RunStore(db_path)
    run_id = store.create_run("prd-to-commit", {"prd": "./prd.md"}, llm="minimax/MiniMax-M2.7")
    store.set_run_status(run_id, RunStatus.RUNNING)

    result = CliRunner().invoke(app, ["status", run_id, "--project-root", str(tmp_path)])

    assert result.exit_code == 0
    assert "llm=" in result.output
    assert "minimax/MiniMax-M2.7" in result.output


def test_status_prints_successful_step_outputs(tmp_path):
    db_path = tmp_path / ".devflow" / "runs.db"
    db_path.parent.mkdir()
    store = RunStore(db_path)
    run_id = store.create_run("summarize-file", {"file": "./prd.md"})
    store.upsert_step(
        run_id,
        "summ",
        StepStatus.SUCCESS,
        output={"summary": "short summary", "byte_count": 123},
    )
    store.set_run_status(run_id, RunStatus.SUCCESS)

    result = CliRunner().invoke(app, ["status", run_id, "--project-root", str(tmp_path)])

    assert result.exit_code == 0
    assert "outputs:" in result.output
    assert "summ:" in result.output
    assert "short summary" in result.output
    assert '"byte_count": 123' in result.output


def test_skill_list_discovers_skill_md_frontmatter(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    d = skills / "demo"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        """---
name: demo
description: Demo skill
allowed-tools:
  - Read
required-mcps: []
entrypoint-script: scripts/run.py
---

# Demo
"""
    )
    monkeypatch.setattr(main, "_skill_search_paths", lambda project_root: [skills])

    result = CliRunner().invoke(app, ["skill", "list", "--project-root", str(tmp_path)])

    assert result.exit_code == 0
    assert "demo" in result.output
    assert "scripts/run.py" in result.output
    assert "handler" not in result.output
    assert "version" not in result.output


def test_skill_info_shows_frontmatter_metadata(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    d = skills / "demo"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        """---
name: demo
description: Demo skill
allowed-tools:
  - Read
required-mcps:
  - jira
entrypoint-script: scripts/run.py
invokes-skills:
  - review
policies:
  self_heal_max_rounds: 2
hooks:
  post_run:
    - lint
llm:
  max_iterations: 3
---

# Demo instructions
"""
    )
    monkeypatch.setattr(main, "_skill_search_paths", lambda project_root: [skills])

    result = CliRunner().invoke(app, ["skill", "info", "demo", "--project-root", str(tmp_path)])

    assert result.exit_code == 0
    assert "allowed_tools: ['Read']" in result.output
    assert "required_mcps: ['jira']" in result.output
    assert "entrypoint_script: scripts/run.py" in result.output
    assert "invokes_skills: ['review']" in result.output
    assert "self_heal_max_rounds" in result.output
    assert "max_iterations" in result.output
    assert "post_run" in result.output


def test_skill_test_reports_old_skill_yaml_migration_error(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    d = skills / "old"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("# Old format\n")
    (d / "skill.yaml").write_text("name: old\nversion: 1.0\n")
    monkeypatch.setattr(main, "_skill_search_paths", lambda project_root: [skills])

    result = CliRunner().invoke(app, ["skill", "test", "old", "--project-root", str(tmp_path)])

    assert result.exit_code == 1
    assert "migrate" in result.output.lower()
    assert "SKILL.md frontmatter" in result.output
    assert "skill.yaml" in result.output
