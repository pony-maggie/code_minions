"""Test implement-with-tdd entrypoint with a fake LLM + monkeypatched subprocess."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _load_entrypoint():
    import code_minions
    root = Path(code_minions.__file__).resolve().parent / "builtin" / "skills" / "implement-with-tdd"
    spec = importlib.util.spec_from_file_location("iwt_entrypoint", root / "scripts" / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_compact_test_output_keeps_start_and_end() -> None:
    entrypoint = _load_entrypoint()
    output = "FIRST FAILURE\n" + ("middle\n" * 100) + "LAST FAILURE\n"

    compacted = entrypoint._compact_test_output(output, limit=80)

    assert "FIRST FAILURE" in compacted
    assert "LAST FAILURE" in compacted
    assert "truncated" in compacted


def test_happy_path_one_round(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()

    from code_minions.llm.types import Message, Response, Usage
    llm = MagicMock()
    fake_content = '{"files_written": [{"path": "x.py", "content": "x = 1\\n"}], "reasoning": "ok"}'
    llm.chat.return_value = Response(
        message=Message(role="assistant", content=fake_content),
        usage=Usage(1, 1), model="fake", stop_reason="end_turn",
    )

    def invoke_skill(name, inputs):
        assert name == "ai-code-review"
        return {"issues": [], "summary": "lgtm", "approved": True}

    def fake_run(cmd, **kw):
        return MagicMock(returncode=0, stdout="abc123\n", stderr="")
    monkeypatch.setattr("subprocess.run", fake_run)

    ctx = MagicMock()
    ctx.inputs = {
        "ticket": {
            "id": "T1",
            "title": "hello",
            "trace_id": "cm_task_1",
            "expected_paths": ["x.py", "tests/**"],
            "acceptance_criteria": ["x.py is created"],
        }
    }
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = invoke_skill

    out = entrypoint.run(ctx)
    assert out["test_result"]["passed"] is True
    assert out["rounds_used"] == 1
    assert out["review_report"]["approved"] is True
    assert out["trace_id"] == "cm_task_1"
    assert out["task_id"] == "T1"
    assert out["plan_commitment"] == {
        "trace_id": "cm_task_1",
        "task_id": "T1",
        "will_change_paths": ["x.py", "tests/**"],
        "will_not_change_paths": ["paths outside expected_paths"],
        "acceptance_criteria": ["x.py is created"],
        "exit_criteria": ["tests pass", "review has no blocker or major findings"],
    }


def test_resume_adopts_existing_worktree_changes_before_coder_call(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()

    (tmp_git_repo / "x.py").write_text("x = 1\n")

    llm = MagicMock()

    monkeypatch.setattr(entrypoint, "_run_delivery_profile_gate", lambda *args, **kwargs: (True, "", []))
    monkeypatch.setattr(entrypoint, "_run_tests_with_optional_events", lambda *args, **kwargs: (True, "tests passed"))

    ctx = MagicMock()
    ctx.inputs = {
        "ticket": {
            "id": "T1",
            "title": "resume",
            "expected_paths": ["x.py", "tests/**"],
        }
    }
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.extras = {"is_resume": True}
    ctx.skill = SimpleNamespace(meta=SimpleNamespace(policies={"reviewer_max_rounds": 0}))

    out = entrypoint.run(ctx)

    assert out["test_result"]["passed"] is True
    assert out["files_changed"] == ["x.py"]
    llm.chat.assert_not_called()


def test_initial_run_does_not_adopt_user_project_files_as_resume_changes(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()

    from code_minions.llm.types import Message, Response, Usage

    (tmp_git_repo / "project.yml").write_text("name: demo\n")
    llm = MagicMock()
    llm.chat.return_value = Response(
        message=Message(
            role="assistant",
            content='{"files_written": [{"path": "x.py", "content": "x = 1\\n"}], "reasoning": "initial"}',
        ),
        usage=Usage(1, 1),
        model="gemini",
        stop_reason="end_turn",
    )

    monkeypatch.setattr(entrypoint, "_run_delivery_profile_gate", lambda *args, **kwargs: (True, "", []))
    monkeypatch.setattr(entrypoint, "_run_tests_with_optional_events", lambda *args, **kwargs: (True, "tests passed"))

    ctx = MagicMock()
    ctx.inputs = {
        "ticket": {
            "id": "T1",
            "title": "initial run",
            "expected_paths": ["x.py", "tests/**"],
        }
    }
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.extras = {"is_resume": False}
    ctx.skill = SimpleNamespace(meta=SimpleNamespace(policies={"reviewer_max_rounds": 0}))

    out = entrypoint.run(ctx)

    assert out["test_result"]["passed"] is True
    assert "x.py" in out["files_changed"]
    llm.chat.assert_called_once()


def test_retries_when_llm_returns_invalid_json(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()

    from code_minions.llm.types import Message, Response, Usage
    llm = MagicMock()
    llm.chat.side_effect = [
        Response(
            message=Message(role="assistant", content="{\n  files_written: []\n}"),
            usage=Usage(1, 1),
            model="gemini",
            stop_reason="end_turn",
        ),
        Response(
            message=Message(
                role="assistant",
                content='{"files_written": [{"path": "x.py", "content": "x = 1\\n"}], "reasoning": "ok"}',
            ),
            usage=Usage(1, 1),
            model="gemini",
            stop_reason="end_turn",
        ),
    ]

    def invoke_skill(name, inputs):
        assert name == "ai-code-review"
        return {"issues": [], "summary": "lgtm", "approved": True}

    def fake_run(cmd, **kw):
        return MagicMock(returncode=0, stdout="abc123\n", stderr="")
    monkeypatch.setattr("subprocess.run", fake_run)

    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello"}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = invoke_skill

    out = entrypoint.run(ctx)

    assert out["test_result"]["passed"] is True
    assert llm.chat.call_count == 2
    retry_messages = llm.chat.call_args_list[1].kwargs["messages"]
    assert any("valid JSON object only" in m.content for m in retry_messages)


def test_extract_json_object_allows_reasoning_prefix_and_trailing_text() -> None:
    entrypoint = _load_entrypoint()

    data = entrypoint._extract_json_object(
        '<think>fixed</think>\n\n'
        '{"files_written": [{"path": "src/App.tsx", "content": "export default function App() { return null }\\n"}], '
        '"reasoning": "ok"}\n'
        '{"reasoning": "duplicate trailing object"}',
        require_files=True,
    )

    assert data == {
        "files_written": [
            {"path": "src/App.tsx", "content": "export default function App() { return null }\n"}
        ],
        "reasoning": "ok",
    }


def test_extract_json_object_skips_preface_json_without_files_written() -> None:
    entrypoint = _load_entrypoint()

    data = entrypoint._extract_json_object(
        '<think>Use {"reasoning": "done"} only after tools have changed files.</think>\n'
        '{"files_written": [{"path": "src/winner.ts", "content": "export const ok = true\\n"}], '
        '"reasoning": "ok"}',
        require_files=True,
    )

    assert data["files_written"] == [
        {"path": "src/winner.ts", "content": "export const ok = true\n"}
    ]


def test_extract_minimax_inline_write_tool_call_as_files_written() -> None:
    entrypoint = _load_entrypoint()

    files = entrypoint._extract_inline_write_tool_files(
        "<think>writing</think>\n"
        "<minimax:tool_call>\n"
        '<invoke name="Write">\n'
        '<parameter name="path">tests/test_cli.py</parameter>\n'
        '<parameter name="content">print(&quot;ok&quot;)\\n</parameter>\n'
        "</invoke>\n"
        "</minimax:tool_call>"
    )

    assert files == [{"path": "tests/test_cli.py", "content": 'print("ok")\\n'}]


def test_retries_when_llm_returns_no_files_written(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()

    from code_minions.llm.types import Message, Response, Usage
    llm = MagicMock()
    llm.chat.side_effect = [
        Response(
            message=Message(role="assistant", content='{"files_written": [], "reasoning": "no changes"}'),
            usage=Usage(1, 1),
            model="fake",
            stop_reason="end_turn",
        ),
        Response(
            message=Message(
                role="assistant",
                content='{"files_written": [{"path": "x.py", "content": "x = 1\\n"}], "reasoning": "ok"}',
            ),
            usage=Usage(1, 1),
            model="fake",
            stop_reason="end_turn",
        ),
    ]

    def fake_run(cmd, **kw):
        return MagicMock(returncode=0, stdout="abc123\n", stderr="")
    monkeypatch.setattr("subprocess.run", fake_run)

    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello"}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = lambda name, inputs: {"issues": [], "summary": "lgtm", "approved": True}

    out = entrypoint.run(ctx)

    assert out["files_changed"] == ["x.py"]
    assert llm.chat.call_count == 2
    retry_messages = llm.chat.call_args_list[1].kwargs["messages"]
    assert "files_written" in retry_messages[-1].content


def test_retries_when_llm_returns_malformed_files_written_entries(
    tmp_git_repo: Path,
    monkeypatch,
):
    entrypoint = _load_entrypoint()

    from code_minions.llm.types import Message, Response, Usage
    llm = MagicMock()
    llm.chat.side_effect = [
        Response(
            message=Message(role="assistant", content='{"files_written": ["src/App.tsx"], "reasoning": "bad"}'),
            usage=Usage(1, 1),
            model="fake",
            stop_reason="end_turn",
        ),
        Response(
            message=Message(
                role="assistant",
                content='{"files_written": [{"path": "x.py", "content": "x = 1\\n"}], "reasoning": "ok"}',
            ),
            usage=Usage(1, 1),
            model="fake",
            stop_reason="end_turn",
        ),
    ]

    def fake_run(cmd, **kw):
        return MagicMock(returncode=0, stdout="abc123\n", stderr="")
    monkeypatch.setattr("subprocess.run", fake_run)

    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello"}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = lambda name, inputs: {"issues": [], "summary": "lgtm", "approved": True}

    out = entrypoint.run(ctx)

    assert out["files_changed"] == ["x.py"]
    assert llm.chat.call_count == 2
    retry_messages = llm.chat.call_args_list[1].kwargs["messages"]
    assert any("path/content entries" in message.content for message in retry_messages)


def test_bails_when_tests_never_green(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.engine.skill_runtime import SkillExecutionError
    from code_minions.llm.types import Message, Response, Usage
    llm = MagicMock()
    llm.chat.return_value = Response(
        message=Message(role="assistant",
                        content='{"files_written": [{"path":"x.py","content":"boom\\n"}]}'),
        usage=Usage(1, 1), model="fake", stop_reason="end_turn",
    )

    def fake_run(cmd, **kw):
        return MagicMock(returncode=1, stdout="fail", stderr="")
    monkeypatch.setattr("subprocess.run", fake_run)

    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1"}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = lambda name, inputs: {}
    ctx.skill = None

    try:
        entrypoint.run(ctx)
    except SkillExecutionError as e:
        assert e.output is not None
        assert e.output["test_result"]["passed"] is False
        assert "aborted" in e.output["review_report"]["summary"]
    else:
        raise AssertionError("expected SkillExecutionError")


def test_repair_cannot_add_skip_to_make_tests_pass(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.engine.skill_runtime import SkillExecutionError
    from code_minions.llm.types import Message, Response, Usage

    llm = MagicMock()
    llm.chat.side_effect = [
        Response(
            message=Message(
                role="assistant",
                content='{"files_written": [{"path":"tests/test_x.py","content":"def test_x():\\n    assert False\\n"}]}',
            ),
            usage=Usage(1, 1), model="fake", stop_reason="end_turn",
        ),
        Response(
            message=Message(
                role="assistant",
                content=(
                    '{"files_written": [{"path":"tests/test_x.py","content":'
                    '"import pytest\\n@pytest.mark.skip(reason=\\"later\\")\\ndef test_x():\\n    assert True\\n"}]}'
                ),
            ),
            usage=Usage(1, 1), model="fake", stop_reason="end_turn",
        ),
    ]
    monkeypatch.setattr(entrypoint, "_run_delivery_profile_gate", lambda *args: (True, "", []))
    monkeypatch.setattr(entrypoint, "_run_tests", MagicMock(return_value=(False, "failed")))

    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello"}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = MagicMock()
    ctx.skill = SimpleNamespace(meta=SimpleNamespace(policies={
        "self_heal_max_rounds": 1,
        "reviewer_max_rounds": 0,
    }))

    with pytest.raises(SkillExecutionError) as exc:
        entrypoint.run(ctx)

    assert "test quality gate failed" in str(exc.value)
    assert exc.value.run_status == "needs_human"
    codes = {finding["code"] for finding in exc.value.output["gate_findings"]}
    assert "skip-or-xfail-added" in codes


def test_require_tests_policy_rejects_green_run_without_tests(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.engine.skill_runtime import SkillExecutionError
    from code_minions.llm.types import Message, Response, Usage

    llm = MagicMock()
    llm.chat.return_value = Response(
        message=Message(
            role="assistant",
            content='{"files_written": [{"path":"src/app.py","content":"VALUE = 1\\n"}]}',
        ),
        usage=Usage(1, 1),
        model="fake",
        stop_reason="end_turn",
    )
    monkeypatch.setattr(entrypoint, "_run_delivery_profile_gate", lambda *args: (True, "", []))
    monkeypatch.setattr(entrypoint, "_run_tests", MagicMock(return_value=(True, "tests passed")))

    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello"}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = MagicMock()
    ctx.skill = SimpleNamespace(meta=SimpleNamespace(policies={
        "self_heal_max_rounds": 0,
        "reviewer_max_rounds": 0,
        "require_tests": True,
    }))

    with pytest.raises(SkillExecutionError) as exc:
        entrypoint.run(ctx)

    assert "no executable tests detected" in str(exc.value)
    assert exc.value.run_status == "needs_human"
    assert exc.value.output["test_result"]["passed"] is False
    codes = {finding["code"] for finding in exc.value.output["gate_findings"]}
    assert "tests-actually-exist" in codes
    assert not exc.value.output["commit_sha"]


def test_test_quality_snapshot_counts_tests_inside_devflow_worktree(tmp_path: Path) -> None:
    entrypoint = _load_entrypoint()
    workdir = tmp_path / ".devflow" / "runs" / "r1" / "worktree"
    test_path = workdir / "src" / "App.test.tsx"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(
        "import { it, expect } from 'vitest'\n"
        "it('renders', () => {\n"
        "  expect(true).toBe(true)\n"
        "})\n"
    )

    snapshot = entrypoint._test_quality_snapshot(workdir)

    assert snapshot["files"] == 1
    assert snapshot["tests"] == 1
    assert snapshot["assertions"] == 1


def test_reviewer_blockers_do_not_create_wip_commit(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.engine.skill_runtime import SkillExecutionError
    from code_minions.llm.types import Message, Response, Usage

    llm = MagicMock()
    llm.chat.return_value = Response(
        message=Message(
            role="assistant",
            content='{"files_written": [{"path": "x.py", "content": "x = 1\\n"}], "reasoning": "ok"}',
        ),
        usage=Usage(1, 1),
        model="fake",
        stop_reason="end_turn",
    )
    committed = {"value": False}

    def fake_run(cmd, **kw):
        if cmd[:2] == ["git", "commit"]:
            committed["value"] = True
        return MagicMock(returncode=0, stdout="abc123\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello"}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = lambda name, inputs: {
        "issues": [{"severity": "blocker", "file": "x.py", "line": 1, "description": "wrong"}],
        "summary": "not ready",
        "approved": False,
    }
    ctx.skill = SimpleNamespace(meta=SimpleNamespace(policies={
        "self_heal_max_rounds": 0,
        "reviewer_max_rounds": 1,
    }))

    with pytest.raises(SkillExecutionError) as exc:
        entrypoint.run(ctx)

    assert committed["value"] is False
    assert exc.value.run_status == "needs_human"
    assert exc.value.output["commit_sha"] == ""
    assert exc.value.output["review_report"]["approved"] is False


def test_reviewer_blockers_feed_one_repair_round(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, Usage

    llm = MagicMock()
    llm.chat.side_effect = [
        Response(
            message=Message(
                role="assistant",
                content='{"files_written": [{"path": "x.py", "content": "x = 1\\n"}], "reasoning": "first"}',
            ),
            usage=Usage(1, 1),
            model="fake",
            stop_reason="end_turn",
        ),
        Response(
            message=Message(
                role="assistant",
                content='{"files_written": [{"path": "x.py", "content": "x = 2\\n"}], "reasoning": "fixed"}',
            ),
            usage=Usage(1, 1),
            model="fake",
            stop_reason="end_turn",
        ),
    ]
    commits: list[list[str]] = []

    def fake_run(cmd, **kw):
        if cmd[:2] == ["git", "commit"]:
            commits.append(cmd)
        return MagicMock(returncode=0, stdout="abc123\n", stderr="")

    reviews = iter([
        {
            "issues": [{
                "severity": "major",
                "file": "x.py",
                "line": 1,
                "description": "wrong",
                "suggested_fix": "write x.py and tests/test_x.py",
            }],
            "summary": "not ready",
            "approved": False,
        },
        {"issues": [], "summary": "lgtm", "approved": True},
    ])

    monkeypatch.setattr("subprocess.run", fake_run)
    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello"}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = lambda name, inputs: next(reviews)
    ctx.skill = SimpleNamespace(meta=SimpleNamespace(policies={
        "self_heal_max_rounds": 0,
        "reviewer_max_rounds": 2,
    }))

    out = entrypoint.run(ctx)

    assert out["commit_sha"] == "abc123"
    assert out["rounds_used"] == 2
    assert out["review_report"]["approved"] is True
    assert commits
    second_messages = llm.chat.call_args_list[1].kwargs["messages"]
    second_prompt = "\n".join(
        message.content if hasattr(message, "content") else message["content"]
        for message in second_messages
    )
    assert "Previous reviewer feedback" in second_prompt
    assert "[major] x.py:1: wrong" in second_prompt
    assert "Suggested fix: write x.py and tests/test_x.py" in second_prompt
    assert "Review summary: not ready" in second_prompt


def test_expected_paths_reject_scope_drift(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    from code_minions.engine.skill_runtime import SkillExecutionError
    from code_minions.llm.types import Message, Response, Usage

    llm = MagicMock()
    llm.chat.return_value = Response(
        message=Message(
            role="assistant",
            content='{"files_written": [{"path": "README.md", "content": "outside\\n"}], "reasoning": "ok"}',
        ),
        usage=Usage(1, 1),
        model="fake",
        stop_reason="end_turn",
    )
    ctx = MagicMock()
    ctx.inputs = {
        "ticket": {
            "id": "T1",
            "title": "hello",
            "expected_paths": ["src/**", "tests/**"],
        }
    }
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = MagicMock()
    ctx.skill = SimpleNamespace(meta=SimpleNamespace(policies={
        "self_heal_max_rounds": 0,
        "reviewer_max_rounds": 0,
    }))

    with pytest.raises(SkillExecutionError) as exc:
        entrypoint.run(ctx)

    assert "scope drift" in str(exc.value)
    assert exc.value.run_status == "needs_human"
    assert exc.value.output["gate_findings"][0]["code"] == "scope-drift"
    assert (tmp_git_repo / "README.md").read_text() == "# tmp\n"


def test_expected_paths_include_delivery_bootstrap_scaffold_paths() -> None:
    entrypoint = _load_entrypoint()
    ticket = {
        "expected_paths": ["src/**", "tests/**/*.test.tsx"],
        "delivery_profile": {
            "kind": "web-app",
            "framework": "react",
            "build_system": "vite",
            "required_files": ["package.json", "index.html", "src/**/*.tsx"],
        },
    }

    expected_paths = entrypoint._normalized_expected_paths(ticket)

    assert entrypoint._path_allowed_by_expected_paths("index.html", expected_paths)
    assert entrypoint._path_allowed_by_expected_paths("package.json", expected_paths)
    assert entrypoint._path_allowed_by_expected_paths("vite.config.ts", expected_paths)
    assert entrypoint._path_allowed_by_expected_paths("tsconfig.node.json", expected_paths)
    assert entrypoint._path_allowed_by_expected_paths("src/App.tsx", expected_paths)
    assert entrypoint._path_allowed_by_expected_paths("tests/hooks/useGameControls.test.ts", expected_paths)
    assert not entrypoint._path_allowed_by_expected_paths("README.md", expected_paths)


def test_expected_paths_double_star_matches_root_and_nested_files() -> None:
    entrypoint = _load_entrypoint()

    assert entrypoint._path_allowed_by_expected_paths(
        "tests/game.test.tsx",
        ["tests/**/*.test.tsx"],
    )
    assert entrypoint._path_allowed_by_expected_paths(
        "tests/features/game.test.tsx",
        ["tests/**/*.test.tsx"],
    )
    assert not entrypoint._path_allowed_by_expected_paths(
        "src/game.test.tsx",
        ["tests/**/*.test.tsx"],
    )


def test_test_quality_allows_pruning_known_invalid_generated_react_tests() -> None:
    entrypoint = _load_entrypoint()

    findings = entrypoint._test_quality_regressions(
        {"files": 3, "tests": 97, "assertions": 120, "skip_xfail": 0, "weak_assertions": 0},
        {"files": 3, "tests": 95, "assertions": 118, "skip_xfail": 0, "weak_assertions": 0},
        allowed_test_count_drop=2,
    )

    assert [finding.code for finding in findings] == []


def test_generated_test_contract_findings_allow_test_pruning() -> None:
    entrypoint = _load_entrypoint()
    from code_minions.gates import GateFinding

    findings = [
        GateFinding(
            code="react-generated-test-brittle-long-timer-state",
            severity="error",
            stage="generated-test-contract",
            message="generated timer test is brittle",
            repair_hint="repair generated test",
            source="react-vite",
        )
    ]

    assert entrypoint._allowed_generated_test_prune_count(findings) == 10


def test_repairable_generated_test_contract_findings_do_not_allow_test_pruning() -> None:
    entrypoint = _load_entrypoint()
    from code_minions.gates import GateFinding

    findings = [
        GateFinding(
            code="react-generated-test-ambiguous-text-query",
            severity="error",
            stage="generated-test-contract",
            message="generated query is ambiguous",
            repair_hint="anchor the query",
            source="react-vite",
        )
    ]

    assert entrypoint._allowed_generated_test_prune_count(findings) == 0


def test_python_projects_run_pytest_with_current_interpreter_and_workdir_on_pythonpath(
    tmp_git_repo: Path,
    monkeypatch,
):
    entrypoint = _load_entrypoint()
    seen: dict = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        seen["cwd"] = kw["cwd"]
        seen["env"] = kw["env"]
        return MagicMock(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    passed, output = entrypoint._run_tests(tmp_git_repo)

    assert passed is True
    assert output == "ok\n"
    assert seen["cmd"] == [sys.executable, "-m", "pytest", "-q"]
    assert seen["cwd"] == tmp_git_repo
    assert str(tmp_git_repo) in seen["env"]["PYTHONPATH"].split(":")


def test_xcodegen_projects_run_xcodegen_and_xcodebuild(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "project.yml").write_text(
        """name: MacCalc
schemes:
  MacCalc:
    test:
      targets:
        - MacCalcTests
"""
    )
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return MagicMock(returncode=0, stdout=f"{' '.join(cmd)} ok", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    passed, output = entrypoint._run_tests(tmp_git_repo)

    assert passed is True
    assert calls == [
        ["xcodegen", "generate"],
        ["xcodebuild", "test", "-scheme", "MacCalc"],
    ]
    assert "xcodegen generate ok" in output
    assert "xcodebuild test -scheme MacCalc ok" in output


def test_node_projects_install_dependencies_before_npm_test(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "package.json").write_text(
        '{"scripts": {"test": "vitest"}, "devDependencies": {"vitest": "^1.6.0"}}\n'
    )
    calls: list[list[str]] = []
    envs: list[dict[str, str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        envs.append(kw.get("env") or {})
        return MagicMock(returncode=0, stdout=f"{' '.join(cmd)} ok", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    passed, output = entrypoint._run_tests(tmp_git_repo)

    assert passed is True
    assert calls == [
        ["npm", "install", "--no-audit", "--fund=false"],
        ["npm", "test"],
    ]
    assert "npm install --no-audit --fund=false ok" in output
    assert "npm test ok" in output
    assert envs[1]["CI"] == "true"


def test_delivery_profile_test_command_overrides_node_fallback(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "package.json").write_text(
        '{"scripts": {"test:unit": "vitest run"}, "devDependencies": {"vitest": "^1.6.0"}}\n'
    )
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return MagicMock(returncode=0, stdout=f"{' '.join(cmd)} ok", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    profile = {
        "kind": "web-app",
        "language": "typescript",
        "framework": "react",
        "build_system": "vite",
        "test_command": "npm run test:unit",
    }

    passed, output = entrypoint._run_tests(tmp_git_repo, profile)

    assert passed is True
    assert calls == [
        ["npm", "install", "--no-audit", "--fund=false"],
        ["npm", "run", "build"],
        ["npm", "run", "test:unit"],
    ]
    assert "npm run build ok" in output
    assert "npm run test:unit ok" in output


def test_delivery_profile_build_failure_stops_before_npm_test(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "package.json").write_text(
        '{"scripts": {"test": "vitest run"}, "devDependencies": {"typescript": "^5.0.0", "vitest": "^1.6.0"}}\n'
    )
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd == ["npm", "run", "build"]:
            return MagicMock(
                returncode=2,
                stdout="src/App.tsx(1,8): error TS6133: 'React' is declared but its value is never read.",
                stderr="",
            )
        return MagicMock(returncode=0, stdout=f"{' '.join(cmd)} ok", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    profile = {
        "kind": "web-app",
        "language": "typescript",
        "framework": "react",
        "build_system": "vite",
        "test_command": "npm test",
    }

    passed, output = entrypoint._run_tests(tmp_git_repo, profile)

    assert passed is False
    assert calls == [
        ["npm", "install", "--no-audit", "--fund=false"],
        ["npm", "run", "build"],
    ]
    assert "declared but its value is never read" in output


def test_execution_profile_test_timeout_returns_failure_evidence(
    tmp_git_repo: Path,
    monkeypatch,
):
    entrypoint = _load_entrypoint()
    import subprocess

    def fake_run(cmd, **kw):
        if cmd == ["npm", "test"]:
            raise subprocess.TimeoutExpired(
                cmd,
                timeout=300,
                output="partial stdout",
                stderr="partial stderr",
            )
        return MagicMock(returncode=0, stdout=f"{' '.join(cmd)} ok", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    passed, output = entrypoint._run_execution_profile_tests(
        tmp_git_repo,
        {
            "install_command": ["npm", "install", "--no-audit", "--fund=false"],
            "test_command": ["npm", "test"],
            "env": {"CI": "true"},
        },
    )

    assert passed is False
    assert "timed out after 300s" in output
    assert "partial stdout" in output
    assert "partial stderr" in output


def test_react_vite_scaffold_ensures_main_imports_index_css(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "main.tsx").write_text(
        "import React from 'react'\n"
        "import ReactDOM from 'react-dom/client'\n"
        "import App from './App'\n"
        "\n"
        "ReactDOM.createRoot(document.getElementById('root')!).render(<App />)\n"
    )

    ticket = {"delivery_profile": {"stack_id": "react-vite"}}
    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "main.tsx").read_text()
    assert "src/main.tsx" in changed
    assert "import './index.css'" in text


def test_react_vite_scaffold_repairs_llm_modified_harness_files(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "package.json").write_text(
        '{"scripts":{"test":"vitest run"},"devDependencies":{"@testing-library/user-event":"^16.0.1"}}\n'
    )
    (tmp_git_repo / "tsconfig.json").write_text(
        '{"include":["src"],"references":[{"path":"./tsconfig.node.json"}]}\n'
    )
    (tmp_git_repo / "src" / "setupTests.ts").write_text(
        "import '@testing-library/jest-dom/vitest'\n"
        "import { cleanup } from '@testing-library/react'\n"
        "import { afterEach } from 'vitest'\n"
        "afterEach(cleanup())\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}
    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    assert sorted(changed) == sorted([
        "index.html",
        "package.json",
        "src/App.test.tsx",
        "src/App.tsx",
        "src/index.css",
        "src/main.tsx",
        "src/setupTests.ts",
        "src/vite-env.d.ts",
        "tsconfig.json",
        "tsconfig.node.json",
        "vite.config.ts",
    ])
    package_json = (tmp_git_repo / "package.json").read_text()
    assert '"@testing-library/user-event": "14.6.1"' in package_json
    assert "^16.0.1" not in package_json
    assert "afterEach(cleanup)" in (tmp_git_repo / "src" / "setupTests.ts").read_text()
    assert "afterEach(cleanup())" not in (tmp_git_repo / "src" / "setupTests.ts").read_text()
    assert (tmp_git_repo / "tsconfig.node.json").is_file()


def test_react_vite_run_restabilizes_scaffold_after_llm_changes(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, Usage

    llm = MagicMock()
    llm.chat.return_value = Response(
        message=Message(
            role="assistant",
            content=(
                '{"files_written": ['
                '{"path": "package.json", "content": "{\\"scripts\\":{\\"test\\":\\"vitest run\\"},\\"devDependencies\\":{\\"@testing-library/user-event\\":\\"^16.0.1\\"}}\\n"},'
                '{"path": "src/setupTests.ts", "content": "import { cleanup } from \\"@testing-library/react\\"\\nimport { afterEach } from \\"vitest\\"\\nafterEach(cleanup())\\n"},'
                '{"path": "src/App.tsx", "content": "export default function App() { return <main>Ready</main> }\\n"}'
                '], "reasoning": "ok"}'
            ),
        ),
        usage=Usage(1, 1),
        model="fake",
        stop_reason="end_turn",
    )
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: MagicMock(returncode=0, stdout="ok", stderr=""))

    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "task-1", "title": "Board", "delivery_profile": {"stack_id": "react-vite"}}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = lambda name, inputs: {"issues": [], "summary": "lgtm", "approved": True}
    ctx.skill = SimpleNamespace(meta=SimpleNamespace(policies={
        "self_heal_max_rounds": 0,
        "reviewer_max_rounds": 0,
    }))

    output = entrypoint.run(ctx)

    assert output["test_result"]["passed"] is True
    package_json = (tmp_git_repo / "package.json").read_text()
    assert '"@testing-library/user-event": "14.6.1"' in package_json
    assert "^16.0.1" not in package_json
    setup_tests = (tmp_git_repo / "src" / "setupTests.ts").read_text()
    assert "afterEach(cleanup)" in setup_tests
    assert "afterEach(cleanup())" not in setup_tests
    assert (tmp_git_repo / "tsconfig.node.json").is_file()
    assert any(path in output["files_changed"] for path in ["package.json", "src/setupTests.ts"])


def test_react_vite_scaffold_imports_used_vitest_test_apis(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "App.test.tsx").write_text(
        "describe('App', () => {\n"
        "  it('renders', () => {\n"
        "    expect(true).toBe(true)\n"
        "  })\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}
    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "App.test.tsx").read_text()
    assert "src/App.test.tsx" in changed
    assert "import { describe, expect, it } from 'vitest'" in text
    passed, output, findings = entrypoint._run_delivery_profile_gate(tmp_git_repo, ticket)
    assert passed is True
    assert "vitest-global-api-mismatch" not in output
    assert not findings


def test_react_vite_scaffold_merges_duplicate_vitest_imports(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "movement.test.tsx").write_text(
        "import { describe, it, expect, beforeEach, vi } from 'vitest';\n"
        "import { vi } from 'vitest';\n"
        "describe('movement', () => {\n"
        "  beforeEach(() => vi.useFakeTimers())\n"
        "  it('moves', () => expect(vi).toBeDefined())\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "movement.test.tsx").read_text()
    assert "src/movement.test.tsx" in changed
    assert text.count("from 'vitest'") == 1
    assert "import { beforeEach, describe, expect, it, vi } from 'vitest'" in text


def test_react_vite_scaffold_anchors_board_coordinate_regex_queries(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "tests" / "StatusPanel.test.tsx").write_text(
        "import { describe, expect, it } from 'vitest'\n"
        "import { screen } from '@testing-library/react'\n"
        "describe('StatusPanel', () => {\n"
        "  it('queries a cell', () => {\n"
        "    expect(screen.getByRole('button', { name: /^第 7 行第 7 列，空位/ })).toBeDefined()\n"
        "  })\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}
    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "tests" / "StatusPanel.test.tsx").read_text()
    assert "tests/StatusPanel.test.tsx" in changed
    assert "name: /^第 7 行第 7 列，空位$/" in text
    passed, output, findings = entrypoint._run_delivery_profile_gate(tmp_git_repo, ticket)
    assert passed is True
    assert "ambiguous-testing-library-query" not in output
    assert not [finding for finding in findings if finding.code == "ambiguous-testing-library-query"]


def test_react_vite_scaffold_allows_coordinate_label_commas_in_regex_queries(
    tmp_git_repo: Path,
):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "tests" / "Place.test.tsx").write_text(
        "import { describe, expect, it } from 'vitest'\n"
        "import { screen } from '@testing-library/react'\n"
        "describe('Place', () => {\n"
        "  it('queries a cell label', () => {\n"
        "    expect(screen.getByRole('button', { name: /^行1列1, 空$/ })).toBeDefined()\n"
        "    expect(screen.getByRole('button', { name: /^行1列2, 白子$/ })).toBeDefined()\n"
        "  })\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}
    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "tests" / "Place.test.tsx").read_text()
    assert "tests/Place.test.tsx" in changed
    assert "name: /^行1\\s*,?\\s*列1, 空$/" in text
    assert "name: /^行1\\s*,?\\s*列2, 白子$/" in text


def test_react_vite_scaffold_allows_coordinate_label_commas_in_aria_assertions(
    tmp_git_repo: Path,
):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "tests" / "App.test.tsx").write_text(
        "import { describe, expect, it } from 'vitest'\n"
        "describe('App', () => {\n"
        "  it('checks a cell label', () => {\n"
        "    expect(cell).toHaveAttribute('aria-label', '行1列1, 黑子')\n"
        "    expect(cell2).toHaveAttribute('aria-label', \"行1列2, 白子\")\n"
        "  })\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "tests" / "App.test.tsx").read_text()
    assert "tests/App.test.tsx" in changed
    assert "expect(cell).toHaveAccessibleName(/^行1\\s*,?\\s*列1, 黑子$/)" in text
    assert "expect(cell2).toHaveAccessibleName(/^行1\\s*,?\\s*列2, 白子$/)" in text


def test_react_vite_scaffold_anchors_broad_chinese_score_text_queries(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "tests" / "App.test.tsx").write_text(
        "import { screen } from '@testing-library/react'\n"
        "import { expect, it } from 'vitest'\n"
        "it('shows score', () => {\n"
        "  expect(screen.getByText(/分数: 0/)).toBeInTheDocument()\n"
        "  const scoreText = screen.getByText(/分数: (\\d+)/)\n"
        "  const scoreMatch = scoreText.textContent?.match(/分数: (\\d+)/)\n"
        "  expect(scoreMatch).toBeTruthy()\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "tests" / "App.test.tsx").read_text()
    assert "tests/App.test.tsx" in changed
    assert "screen.getByText(/分数: 0/)" not in text
    assert "screen.getByText(/分数: (\\d+)/)" not in text
    assert "screen.getByText(/^分数:\\s*0$/)" in text
    assert "screen.getByText(/^分数:\\s*(\\d+)$/)" in text


def test_react_vite_scaffold_rewrites_pagewide_button_cell_counts(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "Game.test.tsx").write_text(
        "import { render, screen } from '@testing-library/react'\n"
        "import { expect, it } from 'vitest'\n"
        "import { DEFAULT_GRID_SIZE } from './types'\n"
        "it('renders a 20x20 grid board', () => {\n"
        "  render(<Game />)\n"
        "  const cells = screen.getAllByRole('button')\n"
        "  expect(cells).toHaveLength(DEFAULT_GRID_SIZE.rows * DEFAULT_GRID_SIZE.cols)\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "Game.test.tsx").read_text()
    assert "src/Game.test.tsx" in changed
    assert "screen.getAllByRole('button')" not in text
    assert "const cells = screen.getAllByTestId(/^cell-/)" in text
    assert "expect(cells).toHaveLength(DEFAULT_GRID_SIZE.rows * DEFAULT_GRID_SIZE.cols)" in text


def test_react_vite_scaffold_rewrites_single_regex_testid_queries(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "Game.test.tsx").write_text(
        "import { render, screen, within } from '@testing-library/react'\n"
        "import { expect, it } from 'vitest'\n"
        "it('uses a cell', () => {\n"
        "  render(<Game />)\n"
        "  const cell = screen.getByTestId(/^cell-\\d+-\\d+$/)\n"
        "  const scoped = within(screen.getByRole('grid')).getByTestId(/cell-\\d+-\\d+/)\n"
        "  expect(cell).toBeInTheDocument()\n"
        "  expect(scoped).toBeInTheDocument()\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "Game.test.tsx").read_text()
    assert "src/Game.test.tsx" in changed
    assert "getByTestId(/^cell-" not in text
    assert "screen.getAllByTestId(/^cell-\\d+-\\d+$/)[0]" in text
    assert "within(screen.getByRole('grid')).getAllByTestId(/cell-\\d+-\\d+/)[0]" in text


def test_react_vite_scaffold_rewrites_stateful_cell_testid_queries(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "Game.test.tsx").write_text(
        "import { render, screen } from '@testing-library/react'\n"
        "import { expect, it } from 'vitest'\n"
        "it('renders cell states', () => {\n"
        "  render(<Game />)\n"
        "  const snakeCells = screen.getAllByTestId(/^cell-.*state-snake/)\n"
        "  const foodCells = screen.getAllByTestId(/^cell-.*state-food/)\n"
        "  expect(snakeCells.length).toBeGreaterThan(0)\n"
        "  expect(foodCells).toHaveLength(1)\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "Game.test.tsx").read_text()
    assert "src/Game.test.tsx" in changed
    assert "screen.getAllByTestId(/^cell-.*state-snake/)" not in text
    assert "screen.getAllByTestId(/^cell-.*state-food/)" not in text
    assert "document.querySelectorAll<HTMLElement>('[data-testid^=\"cell-\"][data-state=\"snake\"]')" in text
    assert "document.querySelectorAll<HTMLElement>('[data-testid^=\"cell-\"][data-state=\"food\"]')" in text


def test_react_vite_scaffold_rewrites_coordinate_cell_attribute_queries(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "tests" / "Game.test.tsx").write_text(
        "import { render, screen } from '@testing-library/react'\n"
        "import { expect, it } from 'vitest'\n"
        "it('renders occupied cells', () => {\n"
        "  render(<Game />)\n"
        "  const snakeCells = screen.getAllByTestId(/^cell-10-[5-7]$/)\n"
        "  for (const cell of snakeCells) {\n"
        "    expect(cell).toHaveAttribute('data-occupied', 'true')\n"
        "  }\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "tests" / "Game.test.tsx").read_text()
    assert "tests/Game.test.tsx" in changed
    assert "screen.getAllByTestId(/^cell-10-[5-7]$/)" not in text
    assert "document.querySelectorAll<HTMLElement>('[data-testid^=\"cell-\"][data-occupied=\"true\"]')" in text


def test_react_vite_scaffold_rewrites_within_grid_label_status_queries(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "Game.test.tsx").write_text(
        "import { render, screen, within } from '@testing-library/react'\n"
        "import { expect, it } from 'vitest'\n"
        "it('checks status label', () => {\n"
        "  render(<Game />)\n"
        "  let grid = screen.getByRole('grid')\n"
        "  expect(within(grid).getByLabelText(/状态: 运行中/i)).toBeInTheDocument()\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "Game.test.tsx").read_text()
    assert "src/Game.test.tsx" in changed
    assert "within(grid).getByLabelText" not in text
    assert "expect(grid).toHaveAttribute('aria-label', expect.stringMatching(/状态: 运行中/i))" in text


def test_react_vite_scaffold_uses_status_aria_label_for_status_role(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "App.test.tsx").write_text(
        "import { describe, expect, it } from 'vitest'\n"
        "import { render, screen } from '@testing-library/react'\n"
        "describe('App', () => {\n"
        "  it('starts', () => {\n"
        "    render(<App />)\n"
        "    const status = screen.getByRole('status')\n"
        "    expect(status).toHaveTextContent('进行中')\n"
        "  })\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "App.test.tsx").read_text()
    assert "src/App.test.tsx" in changed
    assert "toHaveTextContent('进行中')" not in text
    assert "toHaveAttribute('aria-label', expect.stringContaining('进行中'))" in text


def test_react_vite_scaffold_rewrites_throwing_testid_fallback_queries(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "DirectionButtons.test.tsx").write_text(
        "import { fireEvent, render, screen } from '@testing-library/react'\n"
        "import { it } from 'vitest'\n"
        "it('clicks fallback direction buttons', () => {\n"
        "  render(<DirectionButtons />)\n"
        "  const upButton = screen.getByTestId('up-btn') ||\n"
        "    document.querySelector<HTMLElement>('[data-direction=\"UP\"]') ||\n"
        "    document.querySelector<HTMLElement>('.direction-btn.up')\n"
        "  fireEvent.click(upButton as HTMLElement)\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "DirectionButtons.test.tsx").read_text()
    assert "src/DirectionButtons.test.tsx" in changed
    assert "screen.getByTestId('up-btn') ||" not in text
    assert "screen.queryByTestId('up-btn') ||" in text


def test_react_vite_scaffold_removes_unused_user_event_setup_binding(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "App.test.tsx").write_text(
        "import { beforeEach, it, vi } from 'vitest'\n"
        "import userEvent from '@testing-library/user-event'\n"
        "let user: ReturnType<typeof userEvent.setup>\n"
        "beforeEach(() => {\n"
        "  vi.useFakeTimers()\n"
        "  user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })\n"
        "})\n"
        "it('uses fireEvent only', () => {\n"
        "  expect(true).toBe(true)\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "App.test.tsx").read_text()
    assert "src/App.test.tsx" in changed
    assert "userEvent" not in text
    assert "let user" not in text
    assert "user = userEvent.setup" not in text


def test_react_vite_scaffold_converts_public_next_direction_ref_to_state(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "useGameLoop.ts").write_text(
        "import { useEffect, useRef, useCallback } from 'react'\n"
        "import { Direction } from '../types'\n"
        "export function useGameLoop() {\n"
        "  const directionRef = useRef<Direction | null>('RIGHT')\n"
        "  const nextDirectionRef = useRef<Direction | null>(null)\n"
        "  useEffect(() => {\n"
        "    directionRef.current = 'RIGHT'\n"
        "  }, [])\n"
        "  const turn = useCallback((newDirection: Direction) => {\n"
        "    directionRef.current = newDirection\n"
        "    nextDirectionRef.current = newDirection\n"
        "  }, [])\n"
        "  return {\n"
        "    turn,\n"
        "    direction: directionRef.current,\n"
        "    nextDirection: nextDirectionRef.current,\n"
        "  }\n"
        "}\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "useGameLoop.ts").read_text()
    assert "src/useGameLoop.ts" in changed
    assert "useState" in text
    assert "const [nextDirection, setNextDirection] = useState<Direction | null>(null)" in text
    assert "setNextDirection(newDirection)" in text
    assert "nextDirection: nextDirectionRef.current" not in text
    assert "nextDirection," in text


def test_react_vite_scaffold_removes_unused_source_named_imports(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "useGameState.ts").write_text(
        "import { useState } from 'react'\n"
        "import { GameState, Position, createInitialGameState } from '../types'\n"
        "export function useGameState() {\n"
        "  const [gameState] = useState<GameState>(createInitialGameState())\n"
        "  return { gameState }\n"
        "}\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "useGameState.ts").read_text()
    assert "src/useGameState.ts" in changed
    assert "Position" not in text
    assert "GameState" in text
    assert "createInitialGameState" in text


def test_react_vite_scaffold_imports_used_sibling_named_exports(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "types.ts").write_text(
        "export type CellState = 'empty' | 'filled'\n"
        "export const GRID_SIZE = 20\n"
    )
    (tmp_git_repo / "src" / "Board.tsx").write_text(
        "import { CellState } from './types'\n"
        "export function Board({ board }: { board: CellState[][] }) {\n"
        "  return <div style={{ gridTemplateColumns: `repeat(${GRID_SIZE}, 1fr)` }}>{board.length}</div>\n"
        "}\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "Board.tsx").read_text()
    assert "src/Board.tsx" in changed
    assert "import { CellState, GRID_SIZE } from './types'" in text


def test_react_vite_scaffold_renames_unused_function_parameters(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "types.ts").write_text(
        "export type CellState = 'empty' | 'filled'\n"
        "export interface Position { row: number; col: number }\n"
        "export function findOpenCell(board: CellState[][], snake: Position[]): Position {\n"
        "  const occupied = new Set(snake.map((p) => `${p.row},${p.col}`))\n"
        "  return { row: occupied.size, col: 0 }\n"
        "}\n"
        "export function initializeBoard(): CellState[][] {\n"
        "  const board = [['empty']]\n"
        "  return board as CellState[][]\n"
        "}\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "types.ts").read_text()
    assert "src/types.ts" in changed
    assert "findOpenCell(_board: CellState[][], snake: Position[])" in text


def test_react_vite_scaffold_writes_returned_food_to_board(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "types.ts").write_text(
        "export const CELL_EMPTY = 0\n"
        "export const CELL_SNAKE = 1\n"
        "export const CELL_FOOD = 2\n"
        "export type CellState = typeof CELL_EMPTY | typeof CELL_SNAKE | typeof CELL_FOOD\n"
        "export interface Position { row: number; col: number }\n"
    )
    (tmp_git_repo / "src" / "gameLogic.ts").write_text(
        "import { CELL_EMPTY, CELL_SNAKE, CellState, Position } from './types'\n"
        "export function initializeGameBoard(): { board: CellState[][], food: Position } {\n"
        "  const board = [[CELL_EMPTY]]\n"
        "  const boardWithSnake = board.map(row => [...row])\n"
        "  boardWithSnake[0][0] = CELL_SNAKE\n"
        "  const food = { row: 0, col: 0 }\n"
        "  return { board: boardWithSnake, food }\n"
        "}\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "gameLogic.ts").read_text()
    assert "src/gameLogic.ts" in changed
    assert "import { CELL_EMPTY, CELL_SNAKE, CellState, Position, CELL_FOOD } from './types'" in text
    assert "const boardWithSnakeWithFood = boardWithSnake.map(row => [...row])" in text
    assert "boardWithSnakeWithFood[food.row][food.col] = CELL_FOOD" in text
    assert "return { board: boardWithSnakeWithFood, food }" in text


def test_react_vite_scaffold_replaces_commented_cell_state_literals(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "types.ts").write_text(
        "export const CELL_FOOD = 2\n"
        "export type CellState = 0 | typeof CELL_FOOD\n"
    )
    (tmp_git_repo / "src" / "useGameState.ts").write_text(
        "import { CellState } from './types'\n"
        "export function buildBoard(): CellState[][] {\n"
        "  const result: CellState[][] = [[0]]\n"
        "  result[0][0] = 2 // CELL_FOOD\n"
        "  return result\n"
        "}\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "useGameState.ts").read_text()
    assert "src/useGameState.ts" in changed
    assert "import { CellState, CELL_FOOD } from './types'" in text
    assert "result[0][0] = CELL_FOOD" in text
    assert "2 // CELL_FOOD" not in text


def test_react_vite_scaffold_moves_window_keydown_assignment_into_effect(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "App.tsx").write_text(
        "export default function App() {\n"
        "  const handleKeyDown = (event: KeyboardEvent) => {\n"
        "    if (event.key === 'Enter') startGame()\n"
        "  }\n"
        "  if (typeof window !== 'undefined') {\n"
        "    window.onkeydown = handleKeyDown\n"
        "  }\n"
        "  return <main />\n"
        "}\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "App.tsx").read_text()
    assert "src/App.tsx" in changed
    assert "import { useCallback, useEffect } from 'react'" in text
    assert "const handleKeyDown = useCallback((event: KeyboardEvent) => {" in text
    assert "}, [startGame])" in text
    assert "window.onkeydown" not in text
    assert "window.addEventListener('keydown', handleKeyDown)" in text
    assert "window.removeEventListener('keydown', handleKeyDown)" in text


def test_react_vite_scaffold_relaxes_drifted_component_prop_contracts(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "ScoreBoard.tsx").write_text(
        "interface ScoreBoardProps {\n"
        "  score: number\n"
        "  status: 'ready' | 'running'\n"
        "}\n"
        "export default function ScoreBoard({ score, status }: ScoreBoardProps) {\n"
        "  return <div>{score}{status}</div>\n"
        "}\n"
    )
    (tmp_git_repo / "src" / "Board.tsx").write_text(
        "interface BoardProps {\n"
        "  board: string[][]\n"
        "}\n"
        "export default function Board({ board }: BoardProps) {\n"
        "  return <div>{board.length}</div>\n"
        "}\n"
    )
    (tmp_git_repo / "src" / "App.tsx").write_text(
        "import Board from './Board'\n"
        "import ScoreBoard from './ScoreBoard'\n"
        "export default function App() {\n"
        "  return <><ScoreBoard score={0} /><Board board={[]} snake={[]} /></>\n"
        "}\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    score_text = (tmp_git_repo / "src" / "ScoreBoard.tsx").read_text()
    board_text = (tmp_git_repo / "src" / "Board.tsx").read_text()
    assert "src/ScoreBoard.tsx" in changed
    assert "src/Board.tsx" in changed
    assert "status?: 'ready' | 'running'" in score_text
    assert "snake?: unknown" in board_text


def test_react_vite_scaffold_rewrites_user_event_timer_method_calls(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "App.test.tsx").write_text(
        "import { describe, expect, it, beforeEach, vi } from 'vitest'\n"
        "import { render, screen, fireEvent, act } from '@testing-library/react'\n"
        "import userEvent from '@testing-library/user-event'\n"
        "async function startGameAndGetHead(user: ReturnType<typeof userEvent.setup>) {\n"
        "  await act(async () => {\n"
        "    user.advanceTimersByTime(150)\n"
        "  })\n"
        "}\n"
        "describe('movement', () => {\n"
        "  let user: ReturnType<typeof userEvent.setup>\n"
        "  beforeEach(() => {\n"
        "    vi.useFakeTimers()\n"
        "    user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })\n"
        "  })\n"
        "  it('moves', async () => {\n"
        "    render(<App />)\n"
        "    await startGameAndGetHead(user)\n"
        "    await act(async () => {\n"
        "      fireEvent.keyDown(window, { key: 'ArrowUp' })\n"
        "      user.advanceTimersByTime(150)\n"
        "    })\n"
        "    expect(screen.getByRole('grid')).toBeInTheDocument()\n"
        "  })\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "App.test.tsx").read_text()
    assert "src/App.test.tsx" in changed
    assert "user.advanceTimersByTime" not in text
    assert "vi.advanceTimersByTime(150)" in text
    assert "function startGameAndGetHead()" in text
    assert "startGameAndGetHead(user)" not in text
    assert "@testing-library/user-event" not in text


def test_react_vite_scaffold_adds_advance_timers_for_user_event_with_fake_timers(
    tmp_git_repo: Path,
):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "tests" / "movement.test.tsx").write_text(
        "import { beforeEach, describe, expect, it, vi } from 'vitest'\n"
        "import userEvent from '@testing-library/user-event'\n"
        "describe('movement', () => {\n"
        "  beforeEach(() => {\n"
        "    vi.useFakeTimers()\n"
        "  })\n"
        "  it('clicks while timers are mocked', async () => {\n"
        "    const user = userEvent.setup()\n"
        "    vi.advanceTimersByTime(150)\n"
        "    expect(user).toBeDefined()\n"
        "    expect(true).toBe(true)\n"
        "  })\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "tests" / "movement.test.tsx").read_text()
    assert "tests/movement.test.tsx" in changed
    assert "userEvent.setup({ advanceTimers: vi.advanceTimersByTime })" in text


def test_react_vite_scaffold_adds_missing_user_event_default_import(
    tmp_git_repo: Path,
):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "tests" / "movement.test.tsx").write_text(
        "import { beforeEach, describe, expect, it, vi } from 'vitest'\n"
        "describe('movement', () => {\n"
        "  let user: ReturnType<typeof userEvent.setup>\n"
        "  beforeEach(() => {\n"
        "    user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })\n"
        "    vi.useFakeTimers()\n"
        "  })\n"
        "  it('defines a user', () => expect(user).toBeDefined())\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "tests" / "movement.test.tsx").read_text()
    assert "tests/movement.test.tsx" in changed
    assert "import userEvent from '@testing-library/user-event'" in text


def test_react_vite_scaffold_removes_unused_user_event_default_import(
    tmp_git_repo: Path,
):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "tests" / "movement.test.tsx").write_text(
        "import { describe, expect, it } from 'vitest'\n"
        "import userEvent from '@testing-library/user-event'\n"
        "describe('movement', () => {\n"
        "  it('does not use userEvent', () => expect(true).toBe(true))\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "tests" / "movement.test.tsx").read_text()
    assert "tests/movement.test.tsx" in changed
    assert "@testing-library/user-event" not in text


def test_react_vite_scaffold_uses_fire_event_for_fake_timer_user_interactions(
    tmp_git_repo: Path,
):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "tests" / "movement.test.tsx").write_text(
        "import { beforeEach, describe, expect, it, vi } from 'vitest'\n"
        "import { render, screen, act } from '@testing-library/react'\n"
        "import userEvent from '@testing-library/user-event'\n"
        "describe('movement', () => {\n"
        "  beforeEach(() => {\n"
        "    vi.useFakeTimers()\n"
        "  })\n"
        "  it('clicks and presses keys while timers are mocked', async () => {\n"
        "    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })\n"
        "    await user.click(screen.getByTestId('start-button'))\n"
        "    await user.keyboard('{ArrowUp}')\n"
        "    act(() => {\n"
        "      vi.advanceTimersByTime(150)\n"
        "    })\n"
        "    expect(true).toBe(true)\n"
        "  })\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "tests" / "movement.test.tsx").read_text()
    assert "tests/movement.test.tsx" in changed
    assert "userEvent" not in text
    assert "import { act, fireEvent, screen } from '@testing-library/react'" in text
    assert "fireEvent.click(screen.getByTestId('start-button'))" in text
    assert "fireEvent.keyDown(window, { key: 'ArrowUp' })" in text


def test_react_vite_scaffold_uses_fire_event_for_user_event_aliases(
    tmp_git_repo: Path,
):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "tests" / "movement.test.tsx").write_text(
        "import { beforeEach, describe, expect, it, vi } from 'vitest'\n"
        "import { render, screen, act } from '@testing-library/react'\n"
        "import userEvent from '@testing-library/user-event'\n"
        "describe('movement', () => {\n"
        "  beforeEach(() => {\n"
        "    vi.useFakeTimers()\n"
        "  })\n"
        "  it('clicks and presses keys with an alias', async () => {\n"
        "    const u = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })\n"
        "    await act(async () => {\n"
        "      await u.click(screen.getByTestId('start-button'))\n"
        "      await u.keyboard('{ArrowUp}')\n"
        "    })\n"
        "    expect(true).toBe(true)\n"
        "  })\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "tests" / "movement.test.tsx").read_text()
    assert "tests/movement.test.tsx" in changed
    assert "userEvent" not in text
    assert "await u." not in text
    assert "fireEvent.click(screen.getByTestId('start-button'))" in text
    assert "fireEvent.keyDown(window, { key: 'ArrowUp' })" in text


def test_react_vite_scaffold_uses_fire_event_for_unawaited_fake_timer_user_interactions(
    tmp_git_repo: Path,
):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src" / "hooks").mkdir(parents=True)
    (tmp_git_repo / "src" / "hooks" / "useGame.test.tsx").write_text(
        "import { beforeEach, describe, it, vi } from 'vitest'\n"
        "import { act, render, screen } from '@testing-library/react'\n"
        "import userEvent from '@testing-library/user-event'\n"
        "describe('movement', () => {\n"
        "  beforeEach(() => {\n"
        "    vi.useFakeTimers()\n"
        "  })\n"
        "  it('uses user inside act', () => {\n"
        "    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })\n"
        "    act(() => {\n"
        "      user.click(screen.getByTestId('start'))\n"
        "      user.keyboard('{ArrowLeft}')\n"
        "    })\n"
        "  })\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "hooks" / "useGame.test.tsx").read_text()
    assert "src/hooks/useGame.test.tsx" in changed
    assert "userEvent" not in text
    assert "fireEvent.click(screen.getByTestId('start'))" in text
    assert "fireEvent.keyDown(window, { key: 'ArrowLeft' })" in text


def test_react_vite_scaffold_replaces_global_math_stubs_with_random_spy(
    tmp_git_repo: Path,
):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "App.test.tsx").write_text(
        "import { beforeEach, afterEach, describe, it, vi } from 'vitest'\n"
        "describe('movement', () => {\n"
        "  beforeEach(() => {\n"
        "    vi.useFakeTimers()\n"
        "    vi.stubGlobal('Math', {\n"
        "      ...Math,\n"
        "      random: () => 0.5,\n"
        "    })\n"
        "  })\n"
        "  afterEach(() => {\n"
        "    vi.useRealTimers()\n"
        "    vi.unstubAllGlobals()\n"
        "  })\n"
        "  it('runs', () => {})\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "App.test.tsx").read_text()
    assert "src/App.test.tsx" in changed
    assert "stubGlobal('Math'" not in text
    assert "vi.spyOn(Math, 'random').mockReturnValue(0.5)" in text
    assert "vi.restoreAllMocks()" in text


def test_react_vite_scaffold_resets_local_storage_mock_return_values(
    tmp_git_repo: Path,
):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "tests" / "storage.test.ts").write_text(
        "const localStorageMock = (() => {\n"
        "  let store: Record<string, string> = {}\n"
        "  return {\n"
        "    getItem: vi.fn((key: string) => store[key] ?? null),\n"
        "    clear: () => {\n"
        "      store = {}\n"
        "    },\n"
        "  }\n"
        "})()\n"
        "beforeEach(() => {\n"
        "  localStorageMock.clear()\n"
        "  vi.clearAllMocks()\n"
        "})\n"
        "it('uses mock return value', () => localStorageMock.getItem.mockReturnValue('50'))\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "tests" / "storage.test.ts").read_text()
    assert "tests/storage.test.ts" in changed
    assert "localStorageMock.getItem.mockImplementation((key: string) => store[key] ?? null)" in text


def test_react_vite_scaffold_advances_fake_timers_after_fire_event_interactions(
    tmp_git_repo: Path,
):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "tests" / "movement.test.tsx").write_text(
        "import { act, fireEvent, render, screen } from '@testing-library/react'\n"
        "import { beforeEach, describe, expect, it, vi } from 'vitest'\n"
        "describe('movement', () => {\n"
        "  beforeEach(() => {\n"
        "    vi.useFakeTimers()\n"
        "  })\n"
        "  it('ticks after timer-driven input', async () => {\n"
        "    await act(async () => {\n"
        "      fireEvent.keyDown(window, { key: 'ArrowUp' })\n"
        "    })\n"
        "    await act(async () => {\n"
        "      fireEvent.click(screen.getByTestId('btn-up'))\n"
        "    })\n"
        "    expect(true).toBe(true)\n"
        "  })\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "tests" / "movement.test.tsx").read_text()
    assert "tests/movement.test.tsx" in changed
    assert text.count("vi.advanceTimersByTime(150)") == 2
    assert "fireEvent.keyDown(window, { key: 'ArrowUp' })\n      vi.advanceTimersByTime(150)" in text
    assert "fireEvent.click(screen.getByTestId('btn-up'))\n      vi.advanceTimersByTime(150)" in text


def test_react_vite_scaffold_fills_empty_act_timer_ticks(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "tests" / "movement.test.tsx").write_text(
        "import { act } from '@testing-library/react'\n"
        "import { describe, it, vi } from 'vitest'\n"
        "describe('movement', () => {\n"
        "  beforeEach(() => {\n"
        "    vi.useFakeTimers()\n"
        "  })\n"
        "  it('moves after a tick', () => {\n"
        "    // Advance time by one tick interval\n"
        "    act(() => {\n"
        "    })\n"
        "  })\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "tests" / "movement.test.tsx").read_text()
    assert "tests/movement.test.tsx" in changed
    assert "act(() => {\n      vi.advanceTimersByTime(150)\n    })" in text


def test_react_vite_scaffold_fills_empty_async_act_timer_ticks(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "tests" / "movement.test.tsx").write_text(
        "import { act } from '@testing-library/react'\n"
        "import { describe, it, vi } from 'vitest'\n"
        "describe('movement', () => {\n"
        "  beforeEach(() => {\n"
        "    vi.useFakeTimers()\n"
        "  })\n"
        "  it('moves after an async tick', async () => {\n"
        "    await act(async () => {\n"
        "    })\n"
        "  })\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "tests" / "movement.test.tsx").read_text()
    assert "tests/movement.test.tsx" in changed
    assert "act(async () => {\n      vi.advanceTimersByTime(150)\n    })" in text


def test_react_vite_scaffold_captures_act_callback_return_values(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "tests" / "hook.test.tsx").write_text(
        "import { renderHook, act } from '@testing-library/react'\n"
        "import { describe, expect, it } from 'vitest'\n"
        "import { useGame } from '../src/useGame'\n"
        "describe('hook', () => {\n"
        "  it('asserts a callback return value', () => {\n"
        "    const { result } = renderHook(() => useGame())\n"
        "    const accepted = act(() => {\n"
        "      return result.current.changeDirection('LEFT')\n"
        "    })\n"
        "    expect(accepted).toBe(false)\n"
        "  })\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "tests" / "hook.test.tsx").read_text()
    assert "tests/hook.test.tsx" in changed
    assert "const accepted = act" not in text
    assert "let accepted" in text
    assert "accepted = result.current.changeDirection('LEFT')" in text
    assert "expect(accepted).toBe(false)" in text


def test_react_vite_scaffold_normalizes_inline_style_attribute_names(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "tests" / "layout.test.tsx").write_text(
        "import { screen } from '@testing-library/react'\n"
        "import { expect, it } from 'vitest'\n"
        "it('checks inline style text', () => {\n"
        "  const container = screen.getByTestId('board')\n"
        "  expect(container).toHaveAttribute('style', expect.stringContaining('maxWidth'))\n"
        "  expect(container).toHaveAttribute('style', expect.stringContaining('overflowX'))\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "tests" / "layout.test.tsx").read_text()
    assert "tests/layout.test.tsx" in changed
    assert "maxWidth" not in text
    assert "overflowX" not in text
    assert "max-width" in text
    assert "overflow-x" in text


def test_react_vite_scaffold_camel_cases_inline_style_object_keys(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src" / "components").mkdir(parents=True)
    (tmp_git_repo / "src" / "components" / "GameBoard.tsx").write_text(
        "export function GameBoard() {\n"
        "  return (\n"
        "    <div\n"
        "      style={{\n"
        "        'max-width': 'min(100vw, 560px)',\n"
        "        'grid-template-columns': 'repeat(20, 1fr)',\n"
        "        'overflow-x': 'hidden',\n"
        "      }}\n"
        "    />\n"
        "  )\n"
        "}\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "components" / "GameBoard.tsx").read_text()
    assert "src/components/GameBoard.tsx" in changed
    assert "'max-width'" not in text
    assert "'grid-template-columns'" not in text
    assert "'overflow-x'" not in text
    assert "maxWidth: 'min(100vw, 560px)'" in text
    assert "gridTemplateColumns: 'repeat(20, 1fr)'" in text
    assert "overflowX: 'hidden'" in text


def test_react_vite_scaffold_adds_default_export_for_default_imported_component(
    tmp_git_repo: Path,
):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "src" / "App.tsx").write_text(
        "export function App() {\n"
        "  return <main />\n"
        "}\n"
    )
    (tmp_git_repo / "tests" / "App.test.tsx").write_text(
        "import App from '../src/App'\n"
        "import { render } from '@testing-library/react'\n"
        "import { it } from 'vitest'\n"
        "it('renders', () => render(<App />))\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "App.tsx").read_text()
    assert "src/App.tsx" in changed
    assert "export default App" in text


def test_react_vite_scaffold_adds_named_export_for_named_imported_default_component(
    tmp_git_repo: Path,
):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "src" / "App.tsx").write_text(
        "function App() {\n"
        "  return <main />\n"
        "}\n"
        "\n"
        "export default App\n"
    )
    (tmp_git_repo / "tests" / "App.test.tsx").write_text(
        "import { App } from '../src/App'\n"
        "import { render } from '@testing-library/react'\n"
        "import { it } from 'vitest'\n"
        "it('renders', () => render(<App />))\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "App.tsx").read_text()
    assert "src/App.tsx" in changed
    assert "export { App }" in text


def test_react_vite_scaffold_adds_named_export_for_default_function_component(
    tmp_git_repo: Path,
):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "src" / "App.tsx").write_text(
        "export default function App() {\n"
        "  return <main />\n"
        "}\n"
    )
    (tmp_git_repo / "tests" / "App.test.tsx").write_text(
        "import { App } from '../src/App'\n"
        "import { render } from '@testing-library/react'\n"
        "import { it } from 'vitest'\n"
        "it('renders', () => render(<App />))\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "App.tsx").read_text()
    assert "src/App.tsx" in changed
    assert "export default function App()" in text
    assert "export { App }" in text


def test_react_vite_scaffold_does_not_duplicate_existing_named_component_export(
    tmp_git_repo: Path,
):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "src" / "GameBoard.tsx").write_text(
        "export const GameBoard = () => {\n"
        "  return <main />\n"
        "}\n"
        "\n"
        "export default GameBoard\n"
    )
    (tmp_git_repo / "tests" / "GameBoard.test.tsx").write_text(
        "import { GameBoard } from '../src/GameBoard'\n"
        "import { render } from '@testing-library/react'\n"
        "import { it } from 'vitest'\n"
        "it('renders', () => render(<GameBoard />))\n"
    )
    changed = entrypoint._stabilize_default_imported_component_exports(tmp_git_repo)

    text = (tmp_git_repo / "src" / "GameBoard.tsx").read_text()
    assert "src/GameBoard.tsx" not in changed
    assert text.count("export { GameBoard }") == 0
    assert "export const GameBoard" in text


def test_react_vite_scaffold_removes_redundant_component_re_export(
    tmp_git_repo: Path,
):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "src" / "GameBoard.tsx").write_text(
        "export const GameBoard = () => {\n"
        "  return <main />\n"
        "}\n"
        "\n"
        "export default GameBoard\n"
        "\n"
        "export { GameBoard }\n"
    )
    (tmp_git_repo / "tests" / "GameBoard.test.tsx").write_text(
        "import { GameBoard } from '../src/GameBoard'\n"
        "import { render } from '@testing-library/react'\n"
        "import { it } from 'vitest'\n"
        "it('renders', () => render(<GameBoard />))\n"
    )
    changed = entrypoint._stabilize_default_imported_component_exports(tmp_git_repo)

    text = (tmp_git_repo / "src" / "GameBoard.tsx").read_text()
    assert "src/GameBoard.tsx" in changed
    assert text.count("export { GameBoard }") == 0
    assert "export const GameBoard" in text


def test_react_vite_scaffold_adds_local_storage_mock_for_tests(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "tests" / "storage.test.tsx").write_text(
        "import { beforeEach, it } from 'vitest'\n"
        "beforeEach(() => localStorage.clear())\n"
        "it('stores', () => localStorage.setItem('x', '1'))\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    setup_text = (tmp_git_repo / "src" / "setupTests.ts").read_text()
    assert "src/setupTests.ts" in changed
    assert "Object.defineProperty(globalThis, 'localStorage'" in setup_text
    assert "clear: vi.fn" in setup_text


def test_react_vite_scaffold_repairs_unique_relative_import_target(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src" / "utils").mkdir(parents=True)
    (tmp_git_repo / "src" / "gameLogic.ts").write_text("export const checkWin = () => true\n")
    (tmp_git_repo / "src" / "utils" / "gameLogic.test.ts").write_text(
        "import { describe, expect, it } from 'vitest'\n"
        "import { checkWin } from './gameLogic'\n"
        "describe('gameLogic', () => {\n"
        "  it('works', () => {\n"
        "    expect(checkWin()).toBe(true)\n"
        "  })\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "utils" / "gameLogic.test.ts").read_text()
    assert "src/utils/gameLogic.test.ts" in changed
    assert "from '../gameLogic'" in text
    passed, output, findings = entrypoint._run_delivery_profile_gate(tmp_git_repo, ticket)
    assert passed is True
    assert "unresolved-relative-import" not in output
    assert not [finding for finding in findings if finding.code == "unresolved-relative-import"]


def test_react_vite_scaffold_repairs_unique_relative_require_target(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src" / "hooks").mkdir(parents=True)
    (tmp_git_repo / "src" / "hooks" / "useGameState.ts").write_text(
        "export const checkDraw = () => false\n"
    )
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "tests" / "GameState.test.tsx").write_text(
        "import { describe, expect, it } from 'vitest'\n"
        "describe('draw', () => {\n"
        "  it('checks draw', () => {\n"
        "    const { checkDraw } = require('../hooks/useGameState')\n"
        "    expect(checkDraw()).toBe(false)\n"
        "  })\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "tests" / "GameState.test.tsx").read_text()
    assert "tests/GameState.test.tsx" in changed
    assert "require('../src/hooks/useGameState')" in text
    passed, output, findings = entrypoint._run_delivery_profile_gate(tmp_git_repo, ticket)
    assert passed is True
    assert "unresolved-relative-import" not in output
    assert not [finding for finding in findings if finding.code == "unresolved-relative-import"]


def test_react_vite_scaffold_adds_missing_position_type_contract(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src" / "components").mkdir(parents=True)
    (tmp_git_repo / "src" / "types.ts").write_text(
        "export type Player = 'black' | 'white'\n"
        "export type BoardState = (Player | null)[][]\n"
    )
    (tmp_git_repo / "src" / "components" / "Board.tsx").write_text(
        "import { type BoardState } from '../types'\n"
        "interface BoardProps {\n"
        "  board: BoardState\n"
        "  lastMove: Position | null\n"
        "}\n"
        "export function Board({ board, lastMove }: BoardProps) {\n"
        "  return <div>{board.length}{lastMove?.row}</div>\n"
        "}\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    board_text = (tmp_git_repo / "src" / "components" / "Board.tsx").read_text()
    types_text = (tmp_git_repo / "src" / "types.ts").read_text()
    assert "src/components/Board.tsx" in changed
    assert "src/types.ts" in changed
    assert "type Position" in board_text
    assert "export interface Position" in types_text
    assert "row: number" in types_text
    assert "col: number" in types_text


def test_react_vite_scaffold_keeps_existing_import_type_position(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "types.ts").write_text(
        "export type Stone = 'black' | 'white'\n"
        "export type Board = (Stone | null)[][]\n"
        "export interface Position {\n"
        "  row: number\n"
        "  col: number\n"
        "}\n"
    )
    (tmp_git_repo / "src" / "App.tsx").write_text(
        "import { useState } from 'react'\n"
        "import type { Board as BoardType, Position, Stone } from './types'\n"
        "\n"
        "export default function App() {\n"
        "  const [lastMove] = useState<Position | null>(null)\n"
        "  return <div>{lastMove?.row}</div>\n"
        "}\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "App.tsx").read_text()
    assert "src/App.tsx" not in changed
    assert text.count("Position") == 2
    assert "import { type Position }" not in text


def test_react_vite_scaffold_aliases_board_type_import_collision(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src" / "components").mkdir(parents=True)
    (tmp_git_repo / "src" / "types.ts").write_text(
        "export type Stone = 'black' | 'white'\n"
        "export type Board = (Stone | null)[][]\n"
        "export interface Position {\n"
        "  row: number\n"
        "  col: number\n"
        "}\n"
    )
    (tmp_git_repo / "src" / "components" / "Board.tsx").write_text(
        "import type { Board, Position } from '../types'\n"
        "\n"
        "interface BoardProps {\n"
        "  board: Board\n"
        "  lastMove?: Position | null\n"
        "}\n"
        "\n"
        "export const Board = ({ board, lastMove }: BoardProps) => {\n"
        "  return <div>{board.length}{lastMove?.row}</div>\n"
        "}\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "components" / "Board.tsx").read_text()
    assert "src/components/Board.tsx" in changed
    assert "Board as BoardState" in text
    assert "board: BoardState" in text
    assert "board: Board\n" not in text
    assert "from '../types'\ninterface BoardProps" in text


def test_react_vite_scaffold_repairs_user_event_dynamic_import(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "App.test.tsx").write_text(
        "import { describe, expect, it } from 'vitest'\n"
        "describe('App', () => {\n"
        "  it('clicks', async () => {\n"
        "    const { user } = await import('@testing-library/user-event')\n"
        "    await user.click(document.body)\n"
        "    expect(true).toBe(true)\n"
        "  })\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "App.test.tsx").read_text()
    assert "src/App.test.tsx" in changed
    assert "const user = (await import('@testing-library/user-event')).default" in text
    assert "const { user }" not in text


def test_react_vite_scaffold_rewrites_bare_dom_clicks_in_tests(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "Board.test.tsx").write_text(
        "import { describe, expect, it } from 'vitest'\n"
        "import { render, screen } from '@testing-library/react'\n"
        "\n"
        "describe('Board', () => {\n"
        "  it('clicks', () => {\n"
        "    render(<button>行1列1, 空</button>)\n"
        "    const cell = screen.getByRole('button', { name: /^行1列1, 空$/ })\n"
        "    cell.click()\n"
        "    expect(cell).toBeDefined()\n"
        "  })\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "Board.test.tsx").read_text()
    assert "src/Board.test.tsx" in changed
    assert "import { fireEvent, render, screen } from '@testing-library/react'" in text
    assert "fireEvent.click(cell)" in text
    assert "cell.click()" not in text


def test_react_vite_scaffold_types_null_board_test_factory(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src" / "components").mkdir(parents=True)
    (tmp_git_repo / "src" / "types.ts").write_text(
        "export type Stone = 'black' | 'white' | null\n"
        "export type Board = Stone[][]\n"
    )
    (tmp_git_repo / "src" / "components" / "Board.test.tsx").write_text(
        "import { describe, expect, it } from 'vitest'\n"
        "import Board from './Board'\n"
        "\n"
        "const createEmptyBoard = (): (null)[][] =>\n"
        "  Array.from({ length: 15 }, () => Array.from({ length: 15 }, () => null))\n"
        "\n"
        "describe('Board', () => {\n"
        "  it('renders stones', () => {\n"
        "    const board = createEmptyBoard()\n"
        "    board[7][7] = 'black'\n"
        "    expect(board[7][7]).toBe('black')\n"
        "  })\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "components" / "Board.test.tsx").read_text()
    assert "src/components/Board.test.tsx" in changed
    assert "import type { Board as BoardState } from '../types'" in text
    assert "const createEmptyBoard = (): BoardState =>" in text
    assert "(): (null)[][]" not in text


def test_react_vite_scaffold_preserves_board_type_helpers_imported_by_tests(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "src" / "types.ts").write_text(
        "export type Player = 'black' | 'white'\n"
        "export type CellState = Player | null\n"
        "export type BoardState = CellState[][]\n"
        "export const BOARD_SIZE = 15\n"
    )
    (tmp_git_repo / "tests" / "Board.test.tsx").write_text(
        "import { createEmptyBoard, Board as BoardType } from '../src/types'\n"
        "\n"
        "const board: BoardType = createEmptyBoard()\n"
        "board[7][7] = 'black'\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "types.ts").read_text()
    assert "src/types.ts" in changed
    assert "export type Board = BoardState" in text
    assert "export function createEmptyBoard(): BoardState" in text
    assert "BOARD_SIZE" in text


def test_react_vite_test_stabilizer_rewrites_computed_style_truthy_layout_assertion(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "tests").mkdir(parents=True)
    (tmp_git_repo / "tests" / "Grid.test.tsx").write_text(
        "import { expect, test } from 'vitest'\n"
        "\n"
        "test('grid fits mobile viewport', () => {\n"
        "  const grid = container.querySelector('.game-grid')\n"
        "  expect(grid).toBeInTheDocument()\n"
        "  const style = window.getComputedStyle(grid!)\n"
        "  expect(style.width).toBeTruthy()\n"
        "})\n"
    )

    changed = entrypoint._stabilize_react_vite_tests(tmp_git_repo)

    text = (tmp_git_repo / "tests" / "Grid.test.tsx").read_text()
    assert "tests/Grid.test.tsx" in changed
    assert "getComputedStyle" not in text
    assert "style.width" not in text
    assert "expect(grid).toHaveClass('game-grid')" in text


def test_react_vite_test_stabilizer_rewrites_computed_style_numeric_layout_assertion(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "tests").mkdir(parents=True)
    (tmp_git_repo / "tests" / "Grid.test.tsx").write_text(
        "import { expect, test } from 'vitest'\n"
        "\n"
        "test('grid fits mobile viewport', () => {\n"
        "  const grid = container.querySelector('.game-grid') as HTMLElement\n"
        "  expect(grid).toBeTruthy()\n"
        "  const style = window.getComputedStyle(grid)\n"
        "  expect(parseFloat(style.width)).toBeLessThanOrEqual(375)\n"
        "})\n"
    )

    changed = entrypoint._stabilize_react_vite_tests(tmp_git_repo)

    text = (tmp_git_repo / "tests" / "Grid.test.tsx").read_text()
    assert "tests/Grid.test.tsx" in changed
    assert "getComputedStyle" not in text
    assert "parseFloat" not in text
    assert "toBeTruthy" not in text
    assert "expect(grid).toHaveClass('game-grid')" in text


def test_react_vite_test_stabilizer_rewrites_exact_visual_style_assertion(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "tests").mkdir(parents=True)
    (tmp_git_repo / "tests" / "Controls.test.tsx").write_text(
        "import { expect, test } from 'vitest'\n"
        "\n"
        "test('controls render with their configured visual treatment', () => {\n"
        "  const buttons = screen.getAllByRole('button')\n"
        "  buttons.forEach((button) => {\n"
        "    expect(button).toHaveStyle({ backgroundColor: '#e8c47c' })\n"
        "  })\n"
        "})\n"
    )

    changed = entrypoint._stabilize_react_vite_tests(tmp_git_repo)

    text = (tmp_git_repo / "tests" / "Controls.test.tsx").read_text()
    assert "tests/Controls.test.tsx" in changed
    assert "toHaveStyle" not in text
    assert "backgroundColor" not in text
    assert "expect(button).toBeVisible()" in text


def test_react_vite_scaffold_replaces_brittle_ready_smoke_test(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "App.test.tsx").write_text(
        "import { describe, expect, it } from 'vitest'\n"
        "import { render, screen } from '@testing-library/react'\n"
        "import App from './App'\n"
        "\n"
        "describe('App', () => {\n"
        "  it('renders the app shell', () => {\n"
        "    render(<App />)\n"
        "\n"
        "    expect(screen.getByText('Ready')).toBeDefined()\n"
        "  })\n"
        "})\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "App.test.tsx").read_text()
    assert "src/App.test.tsx" in changed
    assert "Ready" not in text
    assert "expect(container).toBeDefined()" in text


def test_react_vite_scaffold_removes_blank_app_placeholder_text(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "App.tsx").write_text(
        "export default function App() {\n"
        "  return <main>Ready</main>\n"
        "}\n"
    )
    ticket = {
        "delivery_profile": {"stack_id": "react-vite"},
        "acceptance_criteria": ["Given project created, When npm run dev, Then browser shows a blank app"],
    }

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "App.tsx").read_text()
    assert "src/App.tsx" in changed
    assert "Ready" not in text
    assert "return <main />" in text


def test_react_vite_scaffold_stabilizes_board_cell_css_geometry(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "index.css").write_text(
        ".board { display: flex; }\n"
        ".cell { width: 2rem; height: 2rem; position: relative; }\n"
        ".cell::before { content: ''; position: absolute; top: 50%; left: 0; right: 0; height: 1px; }\n"
        ".cell::after { content: ''; position: absolute; left: 50%; top: 0; bottom: 0; width: 1px; }\n"
        ".cell.star::before,\n"
        ".cell.star::after { display: none; }\n"
        ".cell.star { width: 0.75rem; height: 0.75rem; margin: auto; }\n"
        ".cell.black,\n"
        ".cell.white { width: 1.5rem; height: 1.5rem; margin: auto; }\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    text = (tmp_git_repo / "src" / "index.css").read_text()
    assert "src/index.css" in changed
    assert "code_minions: keep board cell geometry stable" in text
    assert ".board .cell {" in text
    assert "border-right: 1px solid #333" in text
    assert ".board .cell.star," in text
    assert "background: radial-gradient(circle at center, #333 0 0.18rem, transparent 0.2rem)" in text


def test_react_vite_scaffold_renames_ts_tests_that_contain_jsx(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "App.tsx").write_text("export default function App() { return <div /> }\n")
    (tmp_git_repo / "src" / "useGameState.test.ts").write_text(
        "import { render } from '@testing-library/react'\n"
        "import App from './App'\n"
        "\n"
        "test('renders', () => render(<App />))\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    assert "src/useGameState.test.ts" in changed
    assert "src/useGameState.test.tsx" in changed
    assert not (tmp_git_repo / "src" / "useGameState.test.ts").exists()
    assert (tmp_git_repo / "src" / "useGameState.test.tsx").read_text().endswith("render(<App />))\n")


def test_react_vite_scaffold_removes_duplicate_ts_jsx_test_when_tsx_exists(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "App.tsx").write_text("export default function App() { return <div /> }\n")
    (tmp_git_repo / "src" / "useGameState.test.ts").write_text(
        "import { render } from '@testing-library/react'\n"
        "import App from './App'\n"
        "test('renders duplicate', () => render(<App />))\n"
    )
    (tmp_git_repo / "src" / "useGameState.test.tsx").write_text(
        "test('existing tsx test', () => expect(true).toBe(true))\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    assert "src/useGameState.test.ts" in changed
    assert not (tmp_git_repo / "src" / "useGameState.test.ts").exists()
    assert "existing tsx test" in (tmp_git_repo / "src" / "useGameState.test.tsx").read_text()


def test_react_vite_scaffold_removes_duplicate_types_self_barrel(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src" / "types").mkdir(parents=True)
    (tmp_git_repo / "src" / "types.ts").write_text(
        "export type Player = 'black' | 'white'\n"
        "export type Cell = Player | null\n"
    )
    (tmp_git_repo / "src" / "types" / "index.ts").write_text(
        "export { type Player, type Cell } from '.';\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    assert "src/types/index.ts" in changed
    assert not (tmp_git_repo / "src" / "types" / "index.ts").exists()
    assert "export type Player" in (tmp_git_repo / "src" / "types.ts").read_text()


def test_react_vite_scaffold_removes_duplicate_types_star_barrel_with_siblings(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src" / "types").mkdir(parents=True)
    (tmp_git_repo / "src" / "types.ts").write_text("export type Direction = 'left' | 'right'\n")
    (tmp_git_repo / "src" / "types" / "index.ts").write_text("export * from '../types'\n")
    (tmp_git_repo / "src" / "types" / "Direction.ts").write_text("export type DirectionName = string\n")
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    assert "src/types/index.ts" in changed
    assert not (tmp_git_repo / "src" / "types" / "index.ts").exists()
    assert (tmp_git_repo / "src" / "types" / "Direction.ts").exists()
    assert (tmp_git_repo / "src" / "types").exists()


def test_react_vite_scaffold_removes_duplicate_types_delete_sentinel(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src" / "types").mkdir(parents=True)
    (tmp_git_repo / "src" / "types.ts").write_text("export type Direction = 'LEFT' | 'RIGHT'\n")
    (tmp_git_repo / "src" / "types" / "index.ts").write_text("DELETE\n")
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    assert "src/types/index.ts" in changed
    assert not (tmp_git_repo / "src" / "types" / "index.ts").exists()
    assert not (tmp_git_repo / "src" / "types").exists()


def test_react_vite_scaffold_removes_duplicate_types_comment_only_file(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src" / "types").mkdir(parents=True)
    (tmp_git_repo / "src" / "types.ts").write_text("export type Direction = 'LEFT' | 'RIGHT'\n")
    (tmp_git_repo / "src" / "types" / "index.ts").write_text(
        "// This file intentionally removed to avoid duplicate type module entry\n"
        "// Import shared types from '../types'\n"
    )
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    assert "src/types/index.ts" in changed
    assert not (tmp_git_repo / "src" / "types" / "index.ts").exists()
    assert not (tmp_git_repo / "src" / "types").exists()


def test_python_web_guidance_rejects_dict_response_model():
    entrypoint = _load_entrypoint()
    ticket = {"delivery_profile": {"stack_id": "python-web"}}

    guidance = entrypoint._delivery_guidance_context(ticket)

    assert "Do not pass a dict literal to FastAPI `response_model`" in guidance
    assert "Pydantic model" in guidance
    assert "python-multipart" in guidance
    assert "`/openapi.json`" in guidance
    assert "Do not make HTML tests depend on single vs double attribute quotes" in guidance
    assert "Path(__file__).parent / \"templates\"" in guidance
    assert "jinja2" in guidance


def test_xcodegen_duplicate_product_name_failure_gets_repair_hint(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "project.yml").write_text(
        """name: MacCalc
settings:
  base:
    PRODUCT_NAME: MacCalc
targets:
  MacCalc:
    type: application
    platform: macOS
    sources:
      - path: src
  MacCalcTests:
    type: bundle.unit-test
    platform: macOS
    sources:
      - path: tests
    dependencies:
      - target: MacCalc
schemes:
  MacCalc:
    test:
      targets:
        - MacCalcTests
"""
    )

    def fake_run(cmd, **kw):
        if cmd[:2] == ["xcodegen", "generate"]:
            return MagicMock(returncode=0, stdout="generated", stderr="")
        return MagicMock(
            returncode=65,
            stdout="",
            stderr=(
                "Testing failed:\n"
                "\tMultiple commands produce '/DerivedData/MacCalc.swiftmodule/arm64-apple-macos.swiftmodule'\n"
            ),
        )

    monkeypatch.setattr("subprocess.run", fake_run)

    passed, output = entrypoint._run_tests(tmp_git_repo)

    assert passed is False
    assert "XcodeGen diagnostic" in output
    assert "PRODUCT_NAME" in output
    assert "test target" in output


def test_project_context_includes_authoritative_xcodegen_file(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "project.yml").write_text(
        """name: MacCalc
packages:
  BigNumber:
    url: https://github.com/abedshafii/BigNumber.git
"""
    )

    context = entrypoint._project_context(tmp_git_repo)

    assert "project.yml" in context
    assert "Authoritative build/test configuration" in context
    assert "https://github.com/abedshafii/BigNumber.git" in context


def test_project_context_includes_existing_source_contract_excerpts(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "types.ts").write_text("export type Player = 'black' | 'white'\n")
    (tmp_git_repo / "src" / "App.tsx").write_text("export default function App() { return <div /> }\n")

    context = entrypoint._project_context(tmp_git_repo)

    assert "Existing source files" in context
    assert "src/types.ts" in context
    assert "export type Player = 'black' | 'white'" in context
    assert "src/App.tsx" in context


def test_project_context_includes_project_memory_from_project_root(tmp_git_repo: Path, tmp_path: Path):
    entrypoint = _load_entrypoint()
    project_root = tmp_path / "project"
    worktree = tmp_path / "worktree"
    (project_root / ".devflow").mkdir(parents=True)
    worktree.mkdir()
    (project_root / ".devflow" / "memory.md").write_text(
        "# code_minions Project Memory\n\n- Prefer Vitest for UI tests.\n"
    )

    context = entrypoint._project_context(worktree, project_root=project_root)

    assert "Project memory" in context
    assert "Prefer Vitest for UI tests." in context


def test_self_heal_prompt_includes_current_build_file_after_failure(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, Usage

    (tmp_git_repo / "project.yml").write_text(
        """name: MacCalc
packages:
  BigNumber:
    url: https://github.com/abedshafii/BigNumber.git
"""
    )
    llm = MagicMock()
    llm.chat.side_effect = [
        Response(
            message=Message(
                role="assistant",
                content='{"files_written": [{"path": "x.swift", "content": "broken\\n"}], "reasoning": "initial"}',
            ),
            usage=Usage(1, 1),
            model="MiniMax-M2.7",
            stop_reason="end_turn",
        ),
        Response(
            message=Message(
                role="assistant",
                content='{"files_written": [{"path": "x.swift", "content": "fixed\\n"}], "reasoning": "repair"}',
            ),
            usage=Usage(1, 1),
            model="MiniMax-M2.7",
            stop_reason="end_turn",
        ),
    ]
    monkeypatch.setattr(entrypoint, "_run_tests", MagicMock(side_effect=[
        (False, "Failed to clone repository https://github.com/abedshafii/BigNumber.git"),
        (True, "green"),
    ]))
    monkeypatch.setattr(entrypoint, "_git_commit", lambda workdir, msg, **kwargs: "abc123")

    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello"}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = MagicMock()
    ctx.skill = SimpleNamespace(meta=SimpleNamespace(policies={
        "self_heal_max_rounds": 1,
        "reviewer_max_rounds": 0,
    }))

    entrypoint.run(ctx)

    repair_user_message = llm.chat.call_args_list[1].kwargs["messages"][1].content
    assert "Current build/test configuration" in repair_user_message
    assert "project.yml" in repair_user_message
    assert "https://github.com/abedshafii/BigNumber.git" in repair_user_message


def test_self_heal_prompt_includes_failure_playbook_hints(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, Usage

    llm = MagicMock()
    llm.chat.side_effect = [
        Response(
            message=Message(
                role="assistant",
                content='{"files_written": [{"path": "package.json", "content": "{\\"scripts\\":{\\"test\\":\\"vitest\\"}}"}], "reasoning": "initial"}',
            ),
            usage=Usage(1, 1),
            model="MiniMax-M2.7",
            stop_reason="end_turn",
        ),
        Response(
            message=Message(
                role="assistant",
                content='{"files_written": [{"path": "package.json", "content": "{\\"scripts\\":{\\"test\\":\\"vitest\\"},\\"devDependencies\\":{\\"@testing-library/jest-dom\\":\\"^6.0.0\\"}}"}], "reasoning": "repair"}',
            ),
            usage=Usage(1, 1),
            model="MiniMax-M2.7",
            stop_reason="end_turn",
        ),
    ]
    monkeypatch.setattr(entrypoint, "_run_tests", MagicMock(side_effect=[
        (
            False,
            'Error: Failed to resolve import "@testing-library/jest-dom" from "src/setupTests.ts". Does the file exist?',
        ),
        (True, "green"),
    ]))
    monkeypatch.setattr(entrypoint, "_git_commit", lambda workdir, msg, **kwargs: "abc123")

    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello"}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = MagicMock()
    ctx.skill = SimpleNamespace(meta=SimpleNamespace(policies={
        "self_heal_max_rounds": 1,
        "reviewer_max_rounds": 0,
    }))

    entrypoint.run(ctx)

    repair_user_message = llm.chat.call_args_list[1].kwargs["messages"][1].content
    assert "Failure playbook hints" in repair_user_message
    assert "failed-to-resolve-import-testing-library-jest-dom" in repair_user_message
    assert "auto_fixable: False" in repair_user_message
    assert "Add it to devDependencies or remove the setup import" in repair_user_message


def test_ticket_delivery_profile_is_included_in_coder_prompt(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, Usage

    llm = MagicMock()
    llm.chat.return_value = Response(
        message=Message(
            role="assistant",
            content='{"files_written": [{"path": "go.mod", "content": "module example.com/app\\n"}, {"path": "main.go", "content": "package main\\nfunc main() {}\\n"}], "reasoning": "ok"}',
        ),
        usage=Usage(1, 1),
        model="fake",
        stop_reason="end_turn",
    )
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: MagicMock(returncode=0, stdout="ok", stderr=""))

    profile = {
        "kind": "web-service",
        "language": "go",
        "required_files": ["go.mod", "**/*.go"],
        "forbidden_product_languages": ["python"],
    }
    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello", "delivery_profile": profile}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = lambda name, inputs: {"issues": [], "summary": "lgtm", "approved": True}
    ctx.skill = None

    entrypoint.run(ctx)

    coder_user = llm.chat.call_args.kwargs["messages"][1].content
    assert "Delivery profile" in coder_user
    assert '"language": "go"' in coder_user


def test_partial_ticket_delivery_profile_is_normalized_in_coder_prompt(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, Usage

    llm = MagicMock()
    llm.chat.return_value = Response(
        message=Message(
            role="assistant",
            content='{"files_written": [{"path": "project.yml", "content": "name: MacCalc\\n"}, {"path": "MacCalcApp.swift", "content": "import SwiftUI\\n@main\\nstruct MacCalcApp: App { var body: some Scene { WindowGroup { Text(\\"Hi\\") } } }\\n"}], "reasoning": "ok"}',
        ),
        usage=Usage(1, 1),
        model="fake",
        stop_reason="end_turn",
    )
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: MagicMock(returncode=0, stdout="ok", stderr=""))

    ctx = MagicMock()
    ctx.inputs = {
        "ticket": {
            "id": "T1",
            "title": "hello",
            "delivery_profile": {
                "kind": "native macOS desktop application",
                "language": "Swift 6",
                "framework": "SwiftUI",
                "build_system": "Xcode 16+",
                "required_files": None,
                "forbidden_product_languages": None,
            },
        },
    }
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = lambda name, inputs: {"issues": [], "summary": "lgtm", "approved": True}
    ctx.skill = None

    entrypoint.run(ctx)

    coder_user = llm.chat.call_args.kwargs["messages"][1].content
    assert '"kind": "native-macos-app"' in coder_user
    assert '"required_files": ["project.yml", "**/*.swift", "**/*App.swift"]' in coder_user
    assert '"python"' in coder_user


def test_react_vite_profile_adds_test_environment_guidance_to_coder_prompt(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, Usage

    llm = MagicMock()
    llm.chat.return_value = Response(
        message=Message(
            role="assistant",
            content='{"files_written": [{"path": "package.json", "content": "{\\"scripts\\":{\\"test\\":\\"vitest run\\"}}\\n"}, {"path": "index.html", "content": "<div id=\\"root\\"></div>\\n"}, {"path": "src/App.tsx", "content": "export default function App() { return <div /> }\\n"}, {"path": "src/App.test.tsx", "content": "import { test } from \\"vitest\\"\\ntest(\\"runs\\", () => {})\\n"}], "reasoning": "ok"}',
        ),
        usage=Usage(1, 1),
        model="fake",
        stop_reason="end_turn",
    )
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: MagicMock(returncode=0, stdout="ok", stderr=""))

    profile = {
        "kind": "web-app",
        "language": "typescript",
        "framework": "react",
        "build_system": "vite",
        "test_command": "npm test",
    }
    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello", "delivery_profile": profile}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = lambda name, inputs: {"issues": [], "summary": "lgtm", "approved": True}
    ctx.skill = None

    entrypoint.run(ctx)

    coder_user = llm.chat.call_args.kwargs["messages"][1].content
    assert "Delivery guidance" in coder_user
    assert "jsdom" in coder_user
    assert "React Testing Library" in coder_user
    assert "afterEach(cleanup)" in coder_user
    assert "relative imports" in coder_user
    assert "orphan tests" in coder_user
    assert "vi.fn()" in coder_user
    assert "jest.*" in coder_user
    assert "describe" in coder_user
    assert "globals: true" in coder_user
    assert "@testing-library/jest-dom/vitest" in coder_user
    assert "toHaveTextContent" in coder_user
    assert "CSS-style `@import`" in coder_user
    assert "tsc --noEmit" in coder_user
    assert "existing callers" in coder_user
    assert "*.test.ts" in coder_user
    assert "no-test" in coder_user
    assert "published, conservative package ranges" in coder_user
    assert "omit a dependency" in coder_user
    assert "postcss.config" in coder_user
    assert "tailwindcss" in coder_user
    assert "autoprefixer" in coder_user
    assert "user.pointer" in coder_user
    assert "pointerdown" in coder_user
    assert "getBoundingClientRect" in coder_user
    assert "jsdom does not compute layout" in coder_user
    assert "semantic click targets" in coder_user
    assert "Preserve existing exported type contracts" in coder_user
    assert "single canonical shared type module" in coder_user
    assert "src/types.ts" in coder_user
    assert "do not create `src/types/index.ts`" in coder_user
    assert "import React hooks explicitly" in coder_user


def test_swift_xcodegen_profile_adds_infoplist_guidance_to_coder_prompt(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, Usage

    llm = MagicMock()
    llm.chat.return_value = Response(
        message=Message(
            role="assistant",
            content='{"files_written": [{"path": "project.yml", "content": "name: MacCalc\\n"}, {"path": "Sources/MacCalc/MacCalcApp.swift", "content": "import SwiftUI\\n@main\\nstruct MacCalcApp: App { var body: some Scene { WindowGroup { Text(\\"Hi\\") } } }\\n"}], "reasoning": "ok"}',
        ),
        usage=Usage(1, 1),
        model="fake",
        stop_reason="end_turn",
    )
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: MagicMock(returncode=0, stdout="ok", stderr=""))

    profile = {
        "kind": "native-macos-app",
        "language": "swift",
        "framework": "swiftui",
        "build_system": "xcodegen",
        "test_command": "xcodegen generate && xcodebuild test -scheme MacCalc",
    }
    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello", "delivery_profile": profile}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = lambda name, inputs: {"issues": [], "summary": "lgtm", "approved": True}
    ctx.skill = None

    entrypoint.run(ctx)

    coder_user = llm.chat.call_args.kwargs["messages"][1].content
    assert "Delivery guidance" in coder_user
    assert "GENERATE_INFOPLIST_FILE" in coder_user
    assert "unit-test" in coder_user


def test_delivery_profile_failure_enters_self_heal_loop(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, Usage

    llm = MagicMock()
    llm.chat.side_effect = [
            Response(
                message=Message(
                    role="assistant",
                    content='{"files_written": [{"path": "main.go", "content": "package main\\n"}], "reasoning": "missing go.mod"}',
                ),
            usage=Usage(1, 1),
            model="fake",
            stop_reason="end_turn",
        ),
        Response(
            message=Message(
                role="assistant",
                content='{"files_written": [{"path": "go.mod", "content": "module example.com/app\\n"}, {"path": "main.go", "content": "package main\\nfunc main() {}\\n"}], "reasoning": "fixed"}',
            ),
            usage=Usage(1, 1),
            model="fake",
            stop_reason="end_turn",
        ),
    ]
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: MagicMock(returncode=0, stdout="ok", stderr=""))

    profile = {
        "kind": "web-service",
        "language": "go",
        "required_files": ["go.mod", "**/*.go"],
        "forbidden_product_languages": ["python"],
    }
    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello", "delivery_profile": profile}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = lambda name, inputs: {"issues": [], "summary": "lgtm", "approved": True}
    ctx.skill = SimpleNamespace(meta=SimpleNamespace(policies={
        "self_heal_max_rounds": 1,
        "reviewer_max_rounds": 0,
    }))

    out = entrypoint.run(ctx)

    assert out["test_result"]["passed"] is True
    assert llm.chat.call_count == 2
    repair_user = llm.chat.call_args_list[1].kwargs["messages"][1].content
    assert "Delivery profile check failed" in repair_user
    assert "missing-required-file" in repair_user


def test_delivery_profile_failure_outputs_gate_findings(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.engine.skill_runtime import SkillExecutionError

    ctx = MagicMock()
    ctx.inputs = {
        "ticket": {
            "id": "task-1",
            "title": "Board",
            "delivery_profile": {
                "stack_id": "react-vite",
                "required_files": ["must-exist.txt"],
            },
        }
    }
    ctx.workdir = tmp_git_repo
    ctx.llm = MagicMock()
    ctx.invoke_skill = lambda name, inputs: {"issues": [], "summary": "lgtm", "approved": True}
    ctx.skill = SimpleNamespace(meta=SimpleNamespace(policies={
        "self_heal_max_rounds": 0,
        "reviewer_max_rounds": 0,
    }))
    monkeypatch.setattr(entrypoint, "_llm_call", lambda *args, **kwargs: {"files_written": []})

    with pytest.raises(SkillExecutionError) as exc_info:
        entrypoint.run(ctx)

    output = exc_info.value.output
    assert output["agent_profile"]["profile_id"] == "react-vite/implementer"
    assert output["gate_findings"][0]["code"] == "missing-required-file"
    assert output["gate_findings"][0]["stage"] == "preflight"


def test_runtime_failure_findings_are_sent_to_repair_prompt(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    calls: list[str] = []

    def fake_llm_call(ctx, system, user, **kwargs):
        calls.append(user)
        return {"files_written": [{"path": "package.json", "content": '{"scripts":{"test":"vitest run"}}'}]}

    attempts = iter([
        (False, "ReferenceError: document is not defined"),
        (True, "ok"),
    ])

    monkeypatch.setattr(entrypoint, "_llm_call", fake_llm_call)
    monkeypatch.setattr(
        entrypoint,
        "_run_delivery_profile_gate",
        lambda workdir, ticket: (True, "Delivery profile check passed.", []),
    )
    monkeypatch.setattr(entrypoint, "_run_tests", lambda workdir, profile: next(attempts))
    monkeypatch.setattr(entrypoint, "_git_commit", lambda workdir, msg, ignored_paths=None: "abc123")

    ctx = MagicMock()
    ctx.inputs = {
        "ticket": {
            "id": "task-1",
            "title": "Board",
            "delivery_profile": {"stack_id": "react-vite"},
        }
    }
    ctx.workdir = tmp_git_repo
    ctx.llm = MagicMock()
    ctx.invoke_skill = lambda name, inputs: {"issues": [], "summary": "lgtm", "approved": True}
    ctx.skill = SimpleNamespace(meta=SimpleNamespace(policies={
        "self_heal_max_rounds": 1,
        "reviewer_max_rounds": 0,
    }))

    output = entrypoint.run(ctx)

    assert output["test_result"]["passed"] is True
    assert "Gate findings:" in calls[1]
    assert "jsdom" in calls[1].lower()


def test_relaxed_delivery_profile_warnings_do_not_enter_self_heal_loop(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, Usage

    llm = MagicMock()
    llm.chat.return_value = Response(
        message=Message(
            role="assistant",
            content='{"files_written": [{"path": "package.json", "content": "{\\"scripts\\":{\\"test\\":\\"vitest run\\"}}\\n"}, {"path": "index.html", "content": "<div id=\\"root\\"></div>\\n"}, {"path": "src/App.tsx", "content": "export default function App() { return <div /> }\\n"}], "reasoning": "ok"}',
        ),
        usage=Usage(1, 1),
        model="fake",
        stop_reason="end_turn",
    )
    monkeypatch.setattr(entrypoint, "_run_tests", lambda workdir, profile: (True, "tests passed"))
    monkeypatch.setattr(entrypoint, "_git_commit", lambda workdir, msg, **kwargs: "abc123")

    profile = {
        "kind": "web-app",
        "language": "typescript",
        "framework": "react",
        "build_system": "vite",
        "test_command": "npm test",
        "gate_strictness": "relaxed",
    }
    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello", "delivery_profile": profile}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = lambda name, inputs: {"issues": [], "summary": "lgtm", "approved": True}
    ctx.skill = SimpleNamespace(meta=SimpleNamespace(policies={
        "self_heal_max_rounds": 1,
        "reviewer_max_rounds": 0,
    }))

    out = entrypoint.run(ctx)

    assert out["test_result"]["passed"] is True
    assert out["test_result"]["output"] == "tests passed"
    assert "src/App.test.tsx" in out["files_changed"]
    assert llm.chat.call_count == 1


def test_self_heal_reruns_tests_after_repair(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, Usage

    llm = MagicMock()
    llm.chat.side_effect = [
        Response(
            message=Message(
                role="assistant",
                content='{"files_written": [{"path": "x.py", "content": "broken\\n"}], "reasoning": "initial"}',
            ),
            usage=Usage(1, 1),
            model="gemini",
            stop_reason="end_turn",
        ),
        Response(
            message=Message(
                role="assistant",
                content='{"files_written": [{"path": "x.py", "content": "fixed\\n"}], "reasoning": "repair"}',
            ),
            usage=Usage(1, 1),
            model="gemini",
            stop_reason="end_turn",
        ),
    ]
    test_results = [(False, "failed before repair"), (True, "green after repair")]
    run_tests = MagicMock(side_effect=test_results)
    monkeypatch.setattr(entrypoint, "_run_tests", run_tests)
    monkeypatch.setattr(entrypoint, "_git_commit", lambda workdir, msg, **kwargs: "abc123")

    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello"}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = MagicMock()
    ctx.skill = SimpleNamespace(meta=SimpleNamespace(policies={
        "self_heal_max_rounds": 1,
        "reviewer_max_rounds": 0,
    }))

    out = entrypoint.run(ctx)

    assert out["test_result"] == {"passed": True, "output": "green after repair"}
    assert out["commit_sha"] == "abc123"
    assert run_tests.call_count == 2
    assert (tmp_git_repo / "x.py").read_text() == "fixed\n"


def test_reviewer_can_be_disabled(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, Usage

    llm = MagicMock()
    llm.chat.return_value = Response(
        message=Message(
            role="assistant",
            content='{"files_written": [{"path": "x.py", "content": "x = 1\\n"}], "reasoning": "ok"}',
        ),
        usage=Usage(1, 1),
        model="fake",
        stop_reason="end_turn",
    )

    def fake_run(cmd, **kw):
        return MagicMock(returncode=0, stdout="abc123\n", stderr="")
    monkeypatch.setattr("subprocess.run", fake_run)

    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello"}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = MagicMock()
    ctx.skill = SimpleNamespace(meta=SimpleNamespace(policies={
        "self_heal_max_rounds": 1,
        "reviewer_max_rounds": 0,
    }))

    out = entrypoint.run(ctx)

    assert out["test_result"]["passed"] is True
    assert out["review_report"]["summary"] == "review skipped"
    ctx.invoke_skill.assert_not_called()
    assert llm.chat.call_args.kwargs["max_tokens"] == 16000


def test_llm_can_write_files_with_tools(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, ToolCall, Usage

    llm = MagicMock()
    llm.chat.side_effect = [
        Response(
            message=Message(
                role="assistant",
                tool_calls=[ToolCall(
                    id="call-1",
                    name="Write",
                    arguments={"path": "x.py", "content": "x = 1\n"},
                )],
            ),
            usage=Usage(1, 1),
            model="fake",
            stop_reason="tool_use",
        ),
        Response(
            message=Message(role="assistant", content='{"reasoning": "done"}'),
            usage=Usage(1, 1),
            model="fake",
            stop_reason="end_turn",
        ),
    ]

    def fake_run(cmd, **kw):
        return MagicMock(returncode=0, stdout="abc123\n", stderr="")
    monkeypatch.setattr("subprocess.run", fake_run)

    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello"}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = lambda name, inputs: {"issues": [], "summary": "lgtm", "approved": True}
    ctx.skill = None

    out = entrypoint.run(ctx)

    assert (tmp_git_repo / "x.py").read_text() == "x = 1\n"
    assert out["files_changed"] == ["x.py"]


def test_llm_tool_write_with_file_path_alias_is_recorded(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, ToolCall, Usage

    llm = MagicMock()
    llm.chat.side_effect = [
        Response(
            message=Message(
                role="assistant",
                tool_calls=[ToolCall(
                    id="call-1",
                    name="Write",
                    arguments={"file_path": "x.py", "content": "x = 1\n"},
                )],
            ),
            usage=Usage(1, 1),
            model="fake",
            stop_reason="tool_use",
        ),
        Response(
            message=Message(role="assistant", content='{"reasoning": "done"}'),
            usage=Usage(1, 1),
            model="fake",
            stop_reason="end_turn",
        ),
    ]

    def fake_run(cmd, **kw):
        return MagicMock(returncode=0, stdout="abc123\n", stderr="")
    monkeypatch.setattr("subprocess.run", fake_run)

    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello"}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = lambda name, inputs: {"issues": [], "summary": "lgtm", "approved": True}
    ctx.skill = None

    out = entrypoint.run(ctx)

    assert (tmp_git_repo / "x.py").read_text() == "x = 1\n"
    assert out["files_changed"] == ["x.py"]
    assert llm.chat.call_count == 2


def test_tool_writes_are_recorded(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, ToolCall, Usage

    llm = MagicMock()
    llm.chat.side_effect = [
        Response(
            message=Message(
                role="assistant",
                tool_calls=[ToolCall(
                    id="call-1",
                    name="Write",
                    arguments={"path": "x.py", "content": "x = 1\n"},
                )],
            ),
            usage=Usage(1, 1),
            model="fake",
            stop_reason="tool_use",
        ),
        Response(
            message=Message(role="assistant", content='{"reasoning": "done"}'),
            usage=Usage(1, 1),
            model="fake",
            stop_reason="end_turn",
        ),
    ]

    def fake_run(cmd, **kw):
        return MagicMock(returncode=0, stdout="abc123\n", stderr="")
    monkeypatch.setattr("subprocess.run", fake_run)

    events: list[dict] = []
    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello"}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = lambda name, inputs: {"issues": [], "summary": "lgtm", "approved": True}
    ctx.skill = None
    ctx.extras = {
        "current_step_id": "implement[0]",
        "run_event_recorder": lambda event_type, payload: events.append({
            "event_type": event_type,
            "payload": payload,
        }),
    }

    entrypoint.run(ctx)

    tool_events = [e for e in events if e["event_type"] == "tool_call"]
    assert tool_events == [{
        "event_type": "tool_call",
        "payload": {
            "step_id": "implement[0]",
            "tool": "Write",
            "call_id": "call-1",
            "status": "success",
            "read_only": False,
        },
    }]


def test_git_commit_excludes_execution_profile_ignored_paths(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    commands: list[list[str]] = []

    def fake_run(cmd, **kw):
        commands.append(cmd)
        if cmd[:2] == ["git", "rev-parse"]:
            return MagicMock(returncode=0, stdout="abc123\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    sha = entrypoint._git_commit(
        tmp_git_repo,
        "feat: web app",
        ignored_paths=["node_modules", "dist", "coverage"],
    )

    assert sha == "abc123"
    assert ["git", "add", "-A"] in commands
    assert ["git", "reset", "--", "node_modules", "dist", "coverage"] in commands


def test_tool_written_files_do_not_require_final_json(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, ToolCall, Usage

    llm = MagicMock()
    llm.chat.side_effect = [
        Response(
            message=Message(
                role="assistant",
                tool_calls=[ToolCall(
                    id="call-1",
                    name="Write",
                    arguments={"path": "x.py", "content": "x = 1\n"},
                )],
            ),
            usage=Usage(1, 1),
            model="MiniMax-M2.7",
            stop_reason="tool_use",
        ),
        Response(
            message=Message(role="assistant", content="<think>\nDone, tests should pass now."),
            usage=Usage(10, 5),
            model="MiniMax-M2.7",
            stop_reason="end_turn",
        ),
    ]

    def fake_run(cmd, **kw):
        return MagicMock(returncode=0, stdout="abc123\n", stderr="")
    monkeypatch.setattr("subprocess.run", fake_run)

    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello"}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = lambda name, inputs: {"issues": [], "summary": "lgtm", "approved": True}
    ctx.skill = None

    out = entrypoint.run(ctx)

    assert out["files_changed"] == ["x.py"]
    assert (tmp_git_repo / "x.py").read_text() == "x = 1\n"


def test_invalid_json_response_recovers_existing_worktree_changes(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, Usage

    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "tests" / "useGame.test.ts").write_text("render(<App />)\n")
    (tmp_git_repo / "node_modules").mkdir()
    (tmp_git_repo / "node_modules" / "ignored.js").write_text("do not snapshot\n")

    llm = MagicMock()
    llm.chat.side_effect = [
        Response(
            message=Message(role="assistant", content="<think>I will write the files.</think>"),
            usage=Usage(10, 5),
            model="MiniMax-M2.7",
            stop_reason="stop",
        ),
        Response(
            message=Message(role="assistant", content="<think>Still no JSON.</think>"),
            usage=Usage(10, 5),
            model="MiniMax-M2.7",
            stop_reason="stop",
        ),
    ]

    ctx = MagicMock()
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.skill = None
    ctx.extras = {}

    out = entrypoint._llm_call(ctx, "system", "user", max_attempts=2)

    assert out["reasoning"].startswith("<think>Still no JSON")
    assert out["files_written"] == [
        {"path": "src/App.tsx", "content": "export default function App() { return null }\n"},
        {"path": "tests/useGame.test.ts", "content": "render(<App />)\n"},
    ]


def test_coder_llm_calls_are_recorded(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, Usage

    llm = MagicMock()
    llm.chat.return_value = Response(
        message=Message(
            role="assistant",
            content='{"files_written": [{"path": "x.py", "content": "x = 1\\n"}], "reasoning": "ok"}',
        ),
        usage=Usage(7, 3),
        model="fake",
        stop_reason="end_turn",
    )

    def fake_run(cmd, **kw):
        return MagicMock(returncode=0, stdout="abc123\n", stderr="")
    monkeypatch.setattr("subprocess.run", fake_run)

    events: list[dict] = []
    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello"}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = lambda name, inputs: {"issues": [], "summary": "lgtm", "approved": True}
    ctx.skill = None
    ctx.extras = {
        "current_step_id": "implement[0]",
        "run_event_recorder": lambda event_type, payload: events.append({
            "event_type": event_type,
            "payload": payload,
        }),
    }

    entrypoint.run(ctx)

    llm_events = [e for e in events if e["event_type"].startswith("llm_call_")]
    assert [event["event_type"] for event in llm_events] == [
        "llm_call_started",
        "llm_call_finished",
    ]
    assert llm_events[0]["payload"]["step_id"] == "implement[0]"
    assert llm_events[0]["payload"]["skill"] == "implement-with-tdd"
    assert llm_events[0]["payload"]["role"] == "implementer"
    assert llm_events[1]["payload"]["model"] == "fake"
    assert llm_events[1]["payload"]["stop_reason"] == "end_turn"
    assert llm_events[1]["payload"]["usage"] == {"input_tokens": 7, "output_tokens": 3}
    assert llm_events[1]["payload"]["tool_calls"] == []


def test_tool_call_rounds_do_not_consume_json_retry_budget(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, ToolCall, Usage

    llm = MagicMock()
    llm.chat.side_effect = [
        Response(
            message=Message(
                role="assistant",
                tool_calls=[ToolCall(
                    id="call-1",
                    name="Write",
                    arguments={"path": "x.py", "content": "x = 1\n"},
                )],
            ),
            usage=Usage(1, 1),
            model="gemini",
            stop_reason="tool_use",
        ),
        Response(
            message=Message(
                role="assistant",
                tool_calls=[ToolCall(
                    id="call-2",
                    name="Edit",
                    arguments={"path": "x.py", "old_text": "x = 1\n", "new_text": "x = 2\n"},
                )],
            ),
            usage=Usage(1, 1),
            model="gemini",
            stop_reason="tool_use",
        ),
        Response(
            message=Message(role="assistant", content='{"reasoning": "done"}'),
            usage=Usage(1, 1),
            model="gemini",
            stop_reason="end_turn",
        ),
    ]

    def fake_run(cmd, **kw):
        return MagicMock(returncode=0, stdout="abc123\n", stderr="")
    monkeypatch.setattr("subprocess.run", fake_run)

    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello"}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = lambda name, inputs: {"issues": [], "summary": "lgtm", "approved": True}
    ctx.skill = None

    out = entrypoint.run(ctx)

    assert (tmp_git_repo / "x.py").read_text() == "x = 2\n"
    assert out["files_changed"] == ["x.py"]
    assert llm.chat.call_count == 3


def test_coder_stops_offering_tools_after_mutating_tool_round(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, ToolCall, Usage

    llm = MagicMock()
    llm.chat.side_effect = [
        Response(
            message=Message(
                role="assistant",
                tool_calls=[ToolCall(
                    id="call-1",
                    name="Write",
                    arguments={"path": "x.py", "content": "x = 1\n"},
                )],
            ),
            usage=Usage(1, 1),
            model="MiniMax-M2.7",
            stop_reason="tool_use",
        ),
        Response(
            message=Message(role="assistant", content='{"reasoning": "done"}'),
            usage=Usage(1, 1),
            model="MiniMax-M2.7",
            stop_reason="end_turn",
        ),
    ]

    def fake_run(cmd, **kw):
        return MagicMock(returncode=0, stdout="abc123\n", stderr="")
    monkeypatch.setattr("subprocess.run", fake_run)

    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello"}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = lambda name, inputs: {"issues": [], "summary": "lgtm", "approved": True}
    ctx.skill = None

    entrypoint.run(ctx)

    assert llm.chat.call_args_list[0].kwargs["tools"]
    assert llm.chat.call_args_list[1].kwargs["tools"] is None
    assert "reply with a small JSON object now" in llm.chat.call_args_list[1].kwargs["messages"][-2].content


def test_repeated_read_calls_can_recover_to_write(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, ToolCall, Usage

    llm = MagicMock()
    read_responses = [
        Response(
            message=Message(
                role="assistant",
                tool_calls=[ToolCall(
                    id=f"read-{idx}",
                    name="Read",
                    arguments={"path": "README.md"},
                )],
            ),
            usage=Usage(10, 5),
            model="MiniMax-M2.7",
            stop_reason="tool_use",
        )
        for idx in range(13)
    ]
    llm.chat.side_effect = [
        *read_responses,
        Response(
            message=Message(
                role="assistant",
                tool_calls=[ToolCall(
                    id="write-1",
                    name="Write",
                    arguments={"path": "x.py", "content": "x = 1\n"},
                )],
            ),
            usage=Usage(10, 5),
            model="MiniMax-M2.7",
            stop_reason="tool_use",
        ),
        Response(
            message=Message(role="assistant", content='{"reasoning": "done"}'),
            usage=Usage(1, 1),
            model="MiniMax-M2.7",
            stop_reason="end_turn",
        ),
    ]

    def fake_run(cmd, **kw):
        return MagicMock(returncode=0, stdout="abc123\n", stderr="")
    monkeypatch.setattr("subprocess.run", fake_run)

    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello"}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = lambda name, inputs: {"issues": [], "summary": "lgtm", "approved": True}
    ctx.skill = None

    out = entrypoint.run(ctx)

    assert (tmp_git_repo / "x.py").read_text() == "x = 1\n"
    assert out["files_changed"] == ["x.py"]
    assert llm.chat.call_count == 15


def test_read_budget_exhaustion_disables_tools_for_json_recovery(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, ToolCall, Usage

    llm = MagicMock()
    llm.chat.side_effect = [
        Response(
            message=Message(
                role="assistant",
                tool_calls=[ToolCall(
                    id="read-1",
                    name="Read",
                    arguments={"path": "README.md"},
                )],
            ),
            usage=Usage(10, 5),
            model="MiniMax-M2.7",
            stop_reason="tool_use",
        ),
        Response(
            message=Message(
                role="assistant",
                tool_calls=[ToolCall(
                    id="read-2",
                    name="Read",
                    arguments={"path": "README.md"},
                )],
            ),
            usage=Usage(10, 5),
            model="MiniMax-M2.7",
            stop_reason="tool_use",
        ),
        Response(
            message=Message(
                role="assistant",
                content='{"files_written": [{"path": "x.py", "content": "x = 1\\n"}], "reasoning": "done"}',
            ),
            usage=Usage(10, 5),
            model="MiniMax-M2.7",
            stop_reason="end_turn",
        ),
    ]

    ctx = MagicMock()
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.skill = None

    out = entrypoint._llm_call(ctx, "system", "user", max_read_calls=1)

    assert out["files_written"] == [{"path": "x.py", "content": "x = 1\n"}]
    assert llm.chat.call_args_list[0].kwargs["tools"]
    assert llm.chat.call_args_list[1].kwargs["tools"]
    assert llm.chat.call_args_list[2].kwargs["tools"] is None
    assert "Read budget is exhausted" in llm.chat.call_args_list[2].kwargs["messages"][-2].content


def test_invalid_coder_response_reports_provider_diagnostics(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, Usage

    llm = MagicMock()
    llm.chat.side_effect = [
        Response(
            message=Message(role="assistant", content=""),
            usage=Usage(1234, 2048),
            model="gemini-3.1-pro-preview",
            stop_reason="max_tokens",
        ),
        Response(
            message=Message(role="assistant", content=""),
            usage=Usage(1300, 2048),
            model="gemini-3.1-pro-preview",
            stop_reason="max_tokens",
        ),
    ]

    ctx = MagicMock()
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello"}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = lambda name, inputs: {"issues": [], "summary": "lgtm", "approved": True}
    ctx.skill = SimpleNamespace(meta=SimpleNamespace(policies={
        "self_heal_max_rounds": 1,
        "reviewer_max_rounds": 0,
    }))

    with pytest.raises(RuntimeError) as exc_info:
        entrypoint.run(ctx)

    error = str(exc_info.value)
    assert "LLM did not return JSON" in error
    assert "content=''" in error
    assert "stop_reason=max_tokens" in error
    assert "model=gemini-3.1-pro-preview" in error
    assert "usage=input:1300,output:2048" in error


def test_tool_call_round_limit_reports_diagnostics(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, ToolCall, Usage

    llm = MagicMock()
    llm.chat.return_value = Response(
        message=Message(
            role="assistant",
            tool_calls=[ToolCall(
                id="call-1",
                name="Read",
                arguments={"path": "README.md"},
            )],
        ),
        usage=Usage(10, 5),
        model="gemini-3.1-pro-preview",
        stop_reason="tool_use",
    )

    ctx = MagicMock()
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.skill = None

    with pytest.raises(RuntimeError) as exc_info:
        entrypoint._llm_call(ctx, "system", "user", max_attempts=2, max_tool_rounds=2)

    error = str(exc_info.value)
    assert "tool_call round limit=2" in error
    assert "tool_calls=[Read]" in error
    assert "stop_reason=tool_use" in error
    assert "model=gemini-3.1-pro-preview" in error


def test_python_cli_stabilizer_removes_modules_that_shadow_src_package(tmp_path: Path):
    entrypoint = _load_entrypoint()
    package_dir = tmp_path / "src" / "calc_lite"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("def main():\n    print('ok')\n")
    (tmp_path / "calc_lite.py").write_text("from src.calc_lite import main\n")
    (tmp_path / "src" / "calc_lite.py").write_text("print('shadow')\n")
    ticket = {
        "delivery_profile": {
            "stack_id": "python-cli",
            "kind": "cli",
            "language": "python",
            "build_system": "python",
        }
    }

    changed = entrypoint._stabilize_python_cli_scaffold(tmp_path, ticket)

    assert "calc_lite.py" in changed
    assert "src/calc_lite.py" in changed
    assert "src/calc_lite/__main__.py" in changed
    assert not (tmp_path / "calc_lite.py").exists()
    assert not (tmp_path / "src" / "calc_lite.py").exists()
    assert "from . import main" in (package_dir / "__main__.py").read_text()


def test_python_cli_stabilizer_removes_nested_worktree_and_src_module_cli_tests(tmp_path: Path):
    entrypoint = _load_entrypoint()
    (tmp_path / "src" / "calc_lite").mkdir(parents=True)
    (tmp_path / "src" / "calc_lite" / "__init__.py").write_text("")
    (tmp_path / "worktree" / "src").mkdir(parents=True)
    (tmp_path / "worktree" / "src" / "cli.py").write_text("print('nested')\n")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    test_path = tests_dir / "test_cli.py"
    test_path.write_text(
        "class TestCLI:\n"
        "    def test_cli_module_division_by_zero(self):\n"
        "        result = subprocess.run([sys.executable, '-m', 'src', '1 / 0'])\n"
        "        assert result.returncode != 0\n"
        "\n"
        "    def test_real_behavior(self):\n"
        "        assert True\n"
    )
    ticket = {
        "delivery_profile": {
            "stack_id": "python-cli",
            "kind": "cli",
            "language": "python",
            "build_system": "python",
        }
    }

    changed = entrypoint._stabilize_python_cli_scaffold(tmp_path, ticket)

    assert "worktree" in changed
    assert not (tmp_path / "worktree").exists()
    updated = test_path.read_text()
    assert "python -m', 'src" not in updated
    assert "test_cli_module_division_by_zero" not in updated
    assert "test_real_behavior" in updated


def test_python_cli_stabilizer_removes_generic_src_cli_shims_when_package_exists(tmp_path: Path):
    entrypoint = _load_entrypoint()
    package_dir = tmp_path / "src" / "textcount"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("")
    (tmp_path / "src" / "__init__.py").write_text("")
    (tmp_path / "src" / "cli.py").write_text("print('shadow cli')\n")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    test_path = tests_dir / "test_cli.py"
    test_path.write_text(
        "def test_shadow_src_cli():\n"
        "    result = subprocess.run([sys.executable, '-m', 'src.cli', 'hello world'])\n"
        "    assert '10' in result.stdout\n"
        "\n"
        "def test_existing_package():\n"
        "    assert True\n"
    )
    ticket = {
        "delivery_profile": {
            "stack_id": "python-cli",
            "kind": "cli",
            "language": "python",
            "build_system": "python",
        }
    }

    changed = entrypoint._stabilize_python_cli_scaffold(tmp_path, ticket)

    assert "src/cli.py" in changed
    assert "src/__init__.py" in changed
    assert not (tmp_path / "src" / "cli.py").exists()
    assert not (tmp_path / "src" / "__init__.py").exists()
    updated = test_path.read_text()
    assert "src.cli" not in updated
    assert "test_existing_package" in updated


def test_python_cli_stabilizer_removes_brittle_src_main_path_tests(tmp_path: Path):
    entrypoint = _load_entrypoint()
    package_dir = tmp_path / "src" / "text_count"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    test_path = tests_dir / "test_help.py"
    test_path.write_text(
        "def test_main_entry_point_exists():\n"
        "    import os\n"
        "    assert os.path.exists('src/__main__.py')\n"
        "\n"
        "def test_help_still_exists():\n"
        "    assert True\n"
    )
    ticket = {
        "delivery_profile": {
            "stack_id": "python-cli",
            "kind": "cli",
            "language": "python",
            "build_system": "python",
        }
    }

    changed = entrypoint._stabilize_python_cli_scaffold(tmp_path, ticket)

    assert "tests/test_help.py" in changed
    updated = test_path.read_text()
    assert "src/__main__.py" not in updated
    assert "test_main_entry_point_exists" not in updated
    assert "test_help_still_exists" in updated


def test_python_cli_stabilizer_rewrites_input_call_to_read_full_stdin(tmp_path: Path):
    entrypoint = _load_entrypoint()
    package_dir = tmp_path / "src" / "text_count"
    package_dir.mkdir(parents=True)
    source_path = package_dir / "__init__.py"
    source_path.write_text(
        "import argparse\n\n"
        "def main():\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('text', nargs='?')\n"
        "    args = parser.parse_args()\n"
        "    if args.text is not None:\n"
        "        text = args.text\n"
        "    else:\n"
        "        text = input()\n"
        "    print(text)\n"
    )
    ticket = {
        "delivery_profile": {
            "stack_id": "python-cli",
            "kind": "cli",
            "language": "python",
            "build_system": "python",
        }
    }

    changed = entrypoint._stabilize_python_cli_scaffold(tmp_path, ticket)

    updated = source_path.read_text()
    assert "src/text_count/__init__.py" in changed
    assert "import sys" in updated
    assert "text = sys.stdin.read()" in updated
    assert "text = input()" not in updated


def test_python_cli_stabilizer_normalizes_trailing_newline_line_count(tmp_path: Path):
    entrypoint = _load_entrypoint()
    package_dir = tmp_path / "src" / "text_count"
    package_dir.mkdir(parents=True)
    source_path = package_dir / "__init__.py"
    source_path.write_text(
        "def count_text(text):\n"
        "    if not text:\n"
        "        return {'lines': 0, 'words': 0, 'chars': 0}\n"
        "    lines = text.count('\\n')\n"
        "    if text.endswith('\\n'):\n"
        "        lines += 1\n"
        "    else:\n"
        "        lines += 1\n"
        "    return {'lines': lines, 'words': len(text.split()), 'chars': len(text)}\n"
    )
    ticket = {
        "delivery_profile": {
            "stack_id": "python-cli",
            "kind": "cli",
            "language": "python",
            "build_system": "python",
        }
    }

    changed = entrypoint._stabilize_python_cli_scaffold(tmp_path, ticket)

    updated = source_path.read_text()
    assert "src/text_count/__init__.py" in changed
    assert "lines = text.count('\\n') + (0 if text.endswith('\\n') else 1)" in updated
    assert "else 1)\n    return" in updated
    assert "if text.endswith('\\n'):" not in updated


def test_python_cli_stabilizer_keeps_public_tokenize_from_returning_eof(tmp_path: Path):
    entrypoint = _load_entrypoint()
    package_dir = tmp_path / "src" / "calc_lite"
    package_dir.mkdir(parents=True)
    source = (
        "from calc_lite.tokenizer import Token, TokenType\n\n"
        "def tokenize(text):\n"
        "    tokens = []\n"
        "    tokens.append(Token(TokenType.NUM, 1))\n"
        "    tokens.append(Token(TokenType.EOF, None))\n"
        "    return tokens\n\n"
        "def parse(tokens):\n"
        "    pos = [0]\n"
        "    def current():\n"
        "        return tokens[pos[0]]\n"
        "    if current().type != TokenType.EOF:\n"
        "        pass\n"
    )
    (package_dir / "__init__.py").write_text(source)
    ticket = {
        "delivery_profile": {
            "stack_id": "python-cli",
            "kind": "cli",
            "language": "python",
            "build_system": "python",
        }
    }

    changed = entrypoint._stabilize_python_cli_scaffold(tmp_path, ticket)

    assert "src/calc_lite/__init__.py" in changed
    updated = (package_dir / "__init__.py").read_text()
    assert "tokens.append(Token(TokenType.EOF, None))" not in updated
    assert "if pos[0] >= len(tokens):" in updated
    assert "return Token(TokenType.EOF, None)" in updated


def test_python_cli_stabilizer_adopts_orphan_src_modules_into_canonical_package(tmp_path: Path):
    entrypoint = _load_entrypoint()
    package_dir = tmp_path / "src" / "calc_lite"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("")
    (package_dir / "parser.py").write_text("class Parser:\n    pass\n")
    (tmp_path / "src" / "ast").mkdir()
    (tmp_path / "src" / "ast" / "__init__.py").write_text("from ast.nodes import NUM\n")
    (tmp_path / "src" / "ast" / "nodes.py").write_text("class NUM:\n    pass\n")
    (tmp_path / "src" / "evaluator.py").write_text("from ast import NUM\n\ndef evaluate(node):\n    return 1\n")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_evaluator.py").write_text(
        "from ast import NUM\nfrom evaluator import evaluate\n\ndef test_eval():\n    assert evaluate(NUM()) == 1\n"
    )
    (tests_dir / "test_parser.py").write_text("from parser import parse\n\ndef test_parse():\n    assert parse('1')\n")
    ticket = {
        "delivery_profile": {
            "stack_id": "python-cli",
            "kind": "cli",
            "language": "python",
            "build_system": "python",
        }
    }

    changed = entrypoint._stabilize_python_cli_scaffold(tmp_path, ticket)

    assert "src/calc_lite/ast/__init__.py" in changed
    assert "src/calc_lite/evaluator.py" in changed
    assert not (tmp_path / "src" / "ast").exists()
    assert not (tmp_path / "src" / "evaluator.py").exists()
    assert "from .nodes import NUM" in (package_dir / "ast" / "__init__.py").read_text()
    assert "from .ast import NUM" in (package_dir / "evaluator.py").read_text()
    assert "from calc_lite.ast import NUM" in (tests_dir / "test_evaluator.py").read_text()
    assert "from calc_lite.evaluator import evaluate" in (tests_dir / "test_evaluator.py").read_text()
    assert not (tests_dir / "test_parser.py").exists()
