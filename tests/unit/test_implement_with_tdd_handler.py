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
    ctx.inputs = {"ticket": {"id": "T1", "title": "hello"}}
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = invoke_skill

    out = entrypoint.run(ctx)
    assert out["test_result"]["passed"] is True
    assert out["rounds_used"] == 1
    assert out["review_report"]["approved"] is True


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
        ["npx", "tsc", "--noEmit", "--noUnusedLocals", "false", "--noUnusedParameters", "false"],
        ["npm", "run", "test:unit"],
    ]
    assert "npx tsc --noEmit --noUnusedLocals false --noUnusedParameters false ok" in output
    assert "npm run test:unit ok" in output


def test_delivery_profile_typecheck_failure_stops_before_npm_test(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "package.json").write_text(
        '{"scripts": {"test": "vitest run"}, "devDependencies": {"typescript": "^5.0.0", "vitest": "^1.6.0"}}\n'
    )
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["npx", "tsc"]:
            return MagicMock(
                returncode=2,
                stdout="src/App.tsx(24,32): error TS2339: Property 'cells' does not exist on type 'true'.",
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
        ["npx", "tsc", "--noEmit", "--noUnusedLocals", "false", "--noUnusedParameters", "false"],
    ]
    assert "Property 'cells' does not exist" in output


def test_react_vite_scaffold_creates_stable_project_files(tmp_git_repo: Path):
    entrypoint = _load_entrypoint()
    ticket = {"delivery_profile": {"stack_id": "react-vite"}}

    changed = entrypoint._stabilize_react_vite_scaffold(tmp_git_repo, ticket)

    assert "package.json" in changed
    assert "vite.config.ts" in changed
    assert "tsconfig.json" in changed
    assert "tsconfig.node.json" in changed
    assert "src/setupTests.ts" in changed
    assert "src/vite-env.d.ts" in changed
    assert "src/index.css" in changed
    assert "src/App.test.tsx" in changed
    assert "src/main.tsx" in changed
    package_json = (tmp_git_repo / "package.json").read_text()
    assert '"@testing-library/user-event": "14.6.1"' in package_json
    assert '"vite": "5.4.11"' in package_json
    assert "afterEach(cleanup)" in (tmp_git_repo / "src" / "setupTests.ts").read_text()
    assert "afterEach(cleanup())" not in (tmp_git_repo / "src" / "setupTests.ts").read_text()
    assert (tmp_git_repo / "src" / "vite-env.d.ts").read_text() == "/// <reference types=\"vite/client\" />\n"


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


def test_turn_based_board_game_guidance_limits_complex_gomoku_rule_tests():
    entrypoint = _load_entrypoint()
    ticket = {
        "delivery_profile": {"stack_id": "react-vite"},
        "description": "五子棋，黑白轮流落子，白方纵向五子也要正确获胜",
    }

    guidance = entrypoint._delivery_guidance_context(ticket)

    assert "Keep Gomoku tests lightweight" in guidance
    assert "acceptance-level" in guidance
    assert "Avoid exhaustive public-click tests" in guidance
    assert "Do not let synthetic full-board" in guidance
    assert "Do not write UI tests that fill or click the whole board to prove a draw" in guidance
    assert "omit automated draw tests for the Gomoku MVP" in guidance


def test_turn_based_board_game_guidance_keeps_gomoku_tests_lightweight():
    entrypoint = _load_entrypoint()
    ticket = {
        "delivery_profile": {"stack_id": "react-vite"},
        "description": "五子棋，黑白轮流落子，支持基础胜负判定",
    }

    guidance = entrypoint._delivery_guidance_context(ticket)

    assert "Keep Gomoku tests lightweight" in guidance
    assert "one black horizontal win" in guidance
    assert "(10,10)" not in guidance


def test_turn_based_board_game_guidance_defers_game_over_sequences_before_win_detection():
    entrypoint = _load_entrypoint()
    ticket = {
        "delivery_profile": {"stack_id": "react-vite"},
        "title": "核心落子交互与回合管理",
        "description": "实现五子棋棋盘点击落子、回合切换、已有棋子不可重复落子",
    }

    guidance = entrypoint._delivery_guidance_context(ticket)

    assert "Do not test game-over behavior by constructing a five-in-row sequence" in guidance
    assert "defer that assertion to the win-detection task" in guidance


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
    assert "Stone.Black" in coder_user
    assert "single canonical shared type module" in coder_user
    assert "src/types.ts" in coder_user
    assert "do not create `src/types/index.ts`" in coder_user
    assert "import React hooks explicitly" in coder_user


def test_turn_based_board_game_ticket_adds_valid_move_sequence_guidance(tmp_git_repo: Path, monkeypatch):
    entrypoint = _load_entrypoint()
    from code_minions.llm.types import Message, Response, Usage

    llm = MagicMock()
    llm.chat.return_value = Response(
        message=Message(
            role="assistant",
            content='{"files_written": [{"path": "package.json", "content": "{\\"scripts\\":{\\"test\\":\\"vitest run\\"}}\\n"}, {"path": "index.html", "content": "<div id=\\"root\\"></div>\\n"}, {"path": "src/game.ts", "content": "export const ok = true\\n"}, {"path": "src/game.test.ts", "content": "import { test } from \\"vitest\\"\\ntest(\\"runs\\", () => {})\\n"}], "reasoning": "ok"}',
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
    ctx.inputs = {
        "ticket": {
            "id": "T1",
            "title": "Gomoku board",
            "description": "实现 15x15 五子棋，本地双人对战，黑白双方轮流落子，黑棋先手。",
            "acceptance_criteria": ["黑方横向连续五子获胜", "白方纵向连续五子获胜"],
            "delivery_profile": profile,
        }
    }
    ctx.workdir = tmp_git_repo
    ctx.llm = llm
    ctx.invoke_skill = lambda name, inputs: {"issues": [], "summary": "lgtm", "approved": True}
    ctx.skill = None

    entrypoint.run(ctx)

    coder_user = llm.chat.call_args.kwargs["messages"][1].content
    assert "turn-based board game" in coder_user
    assert "Keep Gomoku tests lightweight" in coder_user
    assert "one black horizontal win" in coder_user
    assert "Avoid exhaustive public-click tests" in coder_user
    assert "pure helper test" in coder_user


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
                content='{"files_written": [{"path": "README.md", "content": "wrong stack\\n"}], "reasoning": "wrong stack"}',
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

    llm_events = [e for e in events if e["event_type"] == "llm_call"]
    assert llm_events == [{
        "event_type": "llm_call",
        "payload": {
            "step_id": "implement[0]",
            "skill": "implement-with-tdd",
            "model": "fake",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 7, "output_tokens": 3},
            "tool_calls": [],
        },
    }]


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
