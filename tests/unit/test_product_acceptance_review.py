from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_entrypoint():
    import code_minions

    root = Path(code_minions.__file__).resolve().parent / "builtin" / "skills" / "product-acceptance-review"
    spec = importlib.util.spec_from_file_location("product_acceptance_entrypoint", root / "scripts" / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _macos_prd() -> dict:
    return {
        "goal": "打造 macOS 平台上最流畅、最美观、最实用的计算器工具 MacCalc",
        "constraints": [
            "技术栈：Swift 6 + SwiftUI 或 AppKit + SwiftUI 混合，MVVM 架构",
            "构建工具：Xcode 16+",
        ],
        "features": [
            {
                "name": "macOS 原生集成",
                "description": "菜单栏、Spotlight、VoiceOver、国际化",
                "acceptance_criteria": ["VoiceOver 全支持", "支持简体中文、繁体中文、English"],
            }
        ],
    }


def test_macos_prd_with_python_only_output_is_rejected(tmp_git_repo: Path) -> None:
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "calculator.py").write_text("def add(a, b): return a + b\n")
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "tests" / "test_calculator.py").write_text("from src.calculator import add\n")

    ctx = type("Ctx", (), {})()
    ctx.workdir = tmp_git_repo
    ctx.inputs = {
        "structured_prd": _macos_prd(),
        "tasks": [{"id": "T1", "title": "项目初始化与基础计算器"}],
        "implement_results": [{
            "files_changed": ["src/calculator.py", "tests/test_calculator.py"],
            "test_result": {"passed": True, "output": "16 passed"},
        }],
    }

    out = entrypoint.run(ctx)

    assert out["accepted"] is False
    assert out["artifact_level"] == "prototype"
    assert any("Swift" in issue["message"] for issue in out["blockers"])
    assert any("macOS" in issue["message"] for issue in out["blockers"])
    assert out["evidence"]["languages"]["python"] == 2
    assert out["evidence"]["languages"].get("swift", 0) == 0


def test_macos_prd_with_swift_app_skeleton_is_app_skeleton(tmp_git_repo: Path) -> None:
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "project.yml").write_text("name: MacCalc\nschemes:\n  MacCalc:\n    test: {}\n")
    app = tmp_git_repo / "MacCalc"
    app.mkdir()
    (app / "MacCalcApp.swift").write_text(
        "import SwiftUI\n@main\nstruct MacCalcApp: App { var body: some Scene { WindowGroup { Text(\"MacCalc\") } } }\n"
    )
    tests = tmp_git_repo / "MacCalcTests"
    tests.mkdir()
    (tests / "MacCalcTests.swift").write_text("import XCTest\nfinal class MacCalcTests: XCTestCase {}\n")

    ctx = type("Ctx", (), {})()
    ctx.workdir = tmp_git_repo
    ctx.inputs = {
        "structured_prd": _macos_prd(),
        "tasks": [{"id": "T1", "title": "项目初始化与基础计算器"}],
        "implement_results": [{
            "files_changed": ["MacCalc/MacCalcApp.swift", "MacCalcTests/MacCalcTests.swift", "project.yml"],
            "test_result": {"passed": True, "output": "Test Succeeded"},
        }],
    }

    out = entrypoint.run(ctx)

    assert out["accepted"] is True
    assert out["artifact_level"] in {"app-skeleton", "mvp-candidate"}
    assert out["blockers"] == []
    assert out["evidence"]["has_swift_app_entry"] is True
    assert out["evidence"]["build_system"] == "xcodegen"


def test_scans_files_inside_devflow_worktree_path(tmp_path: Path) -> None:
    entrypoint = _load_entrypoint()
    worktree = tmp_path / ".devflow" / "runs" / "r_1" / "worktree"
    (worktree / "src").mkdir(parents=True)
    (worktree / "src" / "calculator.py").write_text("def add(a, b): return a + b\n")

    ctx = type("Ctx", (), {})()
    ctx.workdir = worktree
    ctx.inputs = {
        "structured_prd": _macos_prd(),
        "tasks": [{"id": "T1", "title": "项目初始化与基础计算器"}],
        "implement_results": [{
            "files_changed": ["src/calculator.py"],
            "test_result": {"passed": True, "output": "1 passed"},
        }],
    }

    out = entrypoint.run(ctx)

    assert out["evidence"]["file_count"] == 1
    assert out["evidence"]["languages"]["python"] == 1


def test_delivery_profile_rejects_python_for_go_web_service(tmp_git_repo: Path) -> None:
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "server.py").write_text("print('server')\n")

    ctx = type("Ctx", (), {})()
    ctx.workdir = tmp_git_repo
    ctx.inputs = {
        "structured_prd": {
            "goal": "Build a Go web service",
            "delivery_profile": {
                "kind": "web-service",
                "language": "go",
                "build_system": "go-mod",
                "required_files": ["go.mod", "**/*.go"],
                "forbidden_product_languages": ["python"],
            },
        },
        "tasks": [{"id": "T1", "title": "Go service scaffold"}],
        "implement_results": [{
            "files_changed": ["src/server.py"],
            "test_result": {"passed": True, "output": "1 passed"},
        }],
    }

    out = entrypoint.run(ctx)

    assert out["accepted"] is False
    codes = {issue["code"] for issue in out["blockers"]}
    assert "missing-required-file" in codes
    assert "forbidden-product-language" in codes


def test_relaxed_delivery_profile_issues_are_warnings_not_blockers(tmp_git_repo: Path) -> None:
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "index.html").write_text('<div id="root"></div>\n')
    (tmp_git_repo / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_git_repo / "vite.config.ts").write_text("export default {}\n")
    (tmp_git_repo / "src" / "App.tsx").write_text("export default function App() { return <div /> }\n")

    ctx = type("Ctx", (), {})()
    ctx.workdir = tmp_git_repo
    ctx.inputs = {
        "structured_prd": {
            "goal": "Build a React web app",
            "delivery_profile": {
                "kind": "web-app",
                "language": "typescript",
                "framework": "react",
                "build_system": "vite",
                "test_command": "npm test",
                "gate_strictness": "relaxed",
            },
        },
        "tasks": [{"id": "T1", "title": "React app scaffold"}],
        "implement_results": [{
            "files_changed": ["src/App.tsx"],
            "test_result": {"passed": True, "output": "tests passed"},
        }],
    }

    out = entrypoint.run(ctx)

    assert out["accepted"] is True
    assert not out["blockers"]
    assert any(issue["code"] == "missing-test-file" for issue in out["warnings"])


def test_acceptance_review_outputs_acceptance_items_and_verifier_rounds(tmp_git_repo: Path) -> None:
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src" / "minicalc_api").mkdir(parents=True)
    (tmp_git_repo / "src" / "minicalc_api" / "__init__.py").write_text("")
    (tmp_git_repo / "src" / "minicalc_api" / "app.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/health')\ndef health(): return {'status': 'ok'}\n"
    )
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "tests" / "test_app.py").write_text("from minicalc_api.app import app\n")
    (tmp_git_repo / "pyproject.toml").write_text(
        "[project]\nname = 'minicalc-api'\n[tool.pytest.ini_options]\npythonpath = ['src']\n"
    )

    ctx = type("Ctx", (), {})()
    ctx.workdir = tmp_git_repo
    ctx.inputs = {
        "structured_prd": {
            "goal": "Build a Python FastAPI web API",
            "delivery_profile": {"stack_id": "python-web", "required_files": ["pyproject.toml", "src", "tests"]},
        },
        "tasks": [{"id": "T1", "title": "FastAPI scaffold"}],
        "implement_results": [{
            "files_changed": ["pyproject.toml", "src/minicalc_api/app.py", "tests/test_app.py"],
            "test_result": {"passed": True, "output": "1 passed"},
        }],
    }

    out = entrypoint.run(ctx)

    assert out["accepted"] is True
    assert any(item["id"] == "task:T1" and item["status"] == "pass" for item in out["acceptance_items"])
    assert any(item["id"] == "delivery-profile" and item["status"] == "pass" for item in out["acceptance_items"])
    assert out["verifier_rounds"] == [
        {
            "id": "acceptance-verifier-1",
            "qc_no": 1,
            "verifier": "deterministic-acceptance-verifier",
            "status": "pass",
            "verdict": {"pass": True, "failures": 0, "warnings": 0},
            "feedback": "All acceptance items passed.",
            "input_item_ids": [item["id"] for item in out["acceptance_items"]],
        }
    ]


def test_acceptance_review_maps_each_criterion_to_test_evidence(tmp_git_repo: Path) -> None:
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "calculator.py").write_text("def add(a, b): return a + b\n")
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "tests" / "test_calculator.py").write_text(
        "from src.calculator import add\n\n"
        "def test_adds_numbers():\n"
        "    assert add(1, 2) == 3\n"
    )
    (tmp_git_repo / "pyproject.toml").write_text("[project]\nname = 'calc'\n")

    ctx = type("Ctx", (), {})()
    ctx.workdir = tmp_git_repo
    ctx.inputs = {
        "structured_prd": {"goal": "Build a calculator"},
        "tasks": [{
            "id": "T1",
            "trace_id": "cm_task_1",
            "title": "Addition",
            "acceptance_criteria": [
                "Given two numbers, when the user adds them, then the sum is returned.",
                "Invalid input is rejected.",
            ],
        }],
        "implement_results": [{
            "trace_id": "cm_task_1",
            "files_changed": ["src/calculator.py", "tests/test_calculator.py"],
            "test_result": {"passed": True, "output": "1 passed"},
        }],
    }

    out = entrypoint.run(ctx)

    criteria_items = [item for item in out["acceptance_items"] if item["kind"] == "criterion"]
    assert [item["id"] for item in criteria_items] == [
        "criterion:cm_task_1:1",
        "criterion:cm_task_1:2",
    ]
    assert all(item["status"] == "pass" for item in criteria_items)
    assert criteria_items[0]["evidence"]["test_files"] == ["tests/test_calculator.py"]
    assert criteria_items[0]["evidence"]["trace_id"] == "cm_task_1"


def test_acceptance_review_blocks_criterion_without_test_evidence(tmp_git_repo: Path) -> None:
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "calculator.py").write_text("def add(a, b): return a + b\n")

    ctx = type("Ctx", (), {})()
    ctx.workdir = tmp_git_repo
    ctx.inputs = {
        "structured_prd": {"goal": "Build a calculator"},
        "tasks": [{
            "id": "T1",
            "trace_id": "cm_task_1",
            "title": "Addition",
            "acceptance_criteria": ["Addition returns the sum."],
        }],
        "implement_results": [{
            "trace_id": "cm_task_1",
            "files_changed": ["src/calculator.py"],
            "test_result": {"passed": True, "output": "1 passed"},
        }],
    }

    out = entrypoint.run(ctx)

    criterion_item = next(item for item in out["acceptance_items"] if item["kind"] == "criterion")
    assert out["accepted"] is False
    assert criterion_item["status"] == "fail"
    assert criterion_item["evidence"]["test_files"] == []
    assert any(issue["code"] == "missing-criterion-evidence" for issue in out["blockers"])


def test_acceptance_review_blocks_plan_commitment_drift(tmp_git_repo: Path) -> None:
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "app.py").write_text("print('ok')\n")
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "tests" / "test_app.py").write_text("def test_ok(): assert True\n")

    ctx = type("Ctx", (), {})()
    ctx.workdir = tmp_git_repo
    ctx.inputs = {
        "structured_prd": {"goal": "Build an app"},
        "tasks": [{"id": "T1", "trace_id": "cm_task_1", "title": "App"}],
        "implement_results": [{
            "trace_id": "cm_task_1",
            "plan_commitment": {
                "trace_id": "cm_task_1",
                "task_id": "T1",
                "will_change_paths": ["src/**", "tests/**"],
            },
            "files_changed": ["src/app.py", "tests/test_app.py", "README.md"],
            "test_result": {"passed": True, "output": "1 passed"},
        }],
    }

    out = entrypoint.run(ctx)

    item = next(item for item in out["acceptance_items"] if item["id"] == "commitment:cm_task_1")
    assert out["accepted"] is False
    assert item["status"] == "fail"
    assert item["evidence"]["unexpected_files"] == ["README.md"]
    assert any(issue["code"] == "plan-commitment-drift" for issue in out["blockers"])


def test_browser_acceptance_failures_block_product_acceptance(tmp_git_repo: Path) -> None:
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "App.tsx").write_text("export default function App() { return <button>Start</button> }\n")
    (tmp_git_repo / "src" / "App.test.tsx").write_text("test('smoke', () => {})\n")
    (tmp_git_repo / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_git_repo / "index.html").write_text('<div id="root"></div>\n')

    ctx = type("Ctx", (), {})()
    ctx.workdir = tmp_git_repo
    ctx.inputs = {
        "structured_prd": {
            "goal": "Build a React Vite web app",
            "delivery_profile": {"stack_id": "react-vite", "gate_strictness": "relaxed"},
        },
        "tasks": [{"id": "T1", "title": "React app scaffold"}],
        "implement_results": [{
            "files_changed": ["src/App.tsx", "src/App.test.tsx", "package.json", "index.html"],
            "test_result": {"passed": True, "output": "1 passed"},
        }],
        "browser_acceptance_output": {
            "accepted": False,
            "supported": True,
            "stack_id": "react-vite",
            "artifacts": {"mobile_screenshot": ".devflow/browser-evidence/mobile.png"},
            "scenarios": [{
                "id": "browser:control-proximity",
                "title": "Primary controls are near the play surface",
                "status": "fail",
                "message": "Primary controls are visually detached from the main surface.",
            }],
        },
    }

    out = entrypoint.run(ctx)

    assert out["accepted"] is False
    assert any(issue["code"] == "browser:control-proximity" for issue in out["blockers"])
    browser_item = next(item for item in out["acceptance_items"] if item["id"] == "browser:control-proximity")
    assert browser_item["kind"] == "browser"
    assert browser_item["status"] == "fail"
    assert browser_item["evidence"]["artifacts"]["mobile_screenshot"].endswith("mobile.png")


def test_browser_acceptance_rejected_output_blocks_even_without_failed_scenario_status(tmp_git_repo: Path) -> None:
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "App.tsx").write_text("export default function App() { return <button>Start</button> }\n")
    (tmp_git_repo / "tests").mkdir()
    (tmp_git_repo / "tests" / "App.test.tsx").write_text("test('smoke', () => {})\n")
    (tmp_git_repo / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_git_repo / "index.html").write_text('<div id="root"></div>\n')

    ctx = type("Ctx", (), {})()
    ctx.workdir = tmp_git_repo
    ctx.inputs = {
        "structured_prd": {
            "goal": "Build a React Vite web app",
            "delivery_profile": {"stack_id": "react-vite", "gate_strictness": "relaxed"},
        },
        "tasks": [{"id": "T1", "title": "React app scaffold"}],
        "implement_results": [{
            "files_changed": ["src/App.tsx", "tests/App.test.tsx", "package.json", "index.html"],
            "test_result": {"passed": True, "output": "1 passed"},
        }],
        "browser_acceptance_output": {
            "accepted": False,
            "supported": True,
            "stack_id": "react-vite",
            "artifacts": {},
            "scenarios": [{
                "id": "s4",
                "name": "Undo and restart",
                "result": "warn",
                "notes": "Browser acceptance did not accept the UI.",
            }],
        },
    }

    out = entrypoint.run(ctx)

    assert out["accepted"] is False
    assert any(issue["code"] == "browser:accepted" for issue in out["blockers"])
    rejected_item = next(item for item in out["acceptance_items"] if item["id"] == "browser:accepted")
    assert rejected_item["status"] == "fail"


def test_acceptance_review_verifier_round_fails_on_blocking_items(tmp_git_repo: Path) -> None:
    entrypoint = _load_entrypoint()
    (tmp_git_repo / "src").mkdir()
    (tmp_git_repo / "src" / "server.py").write_text("print('server')\n")

    ctx = type("Ctx", (), {})()
    ctx.workdir = tmp_git_repo
    ctx.inputs = {
        "structured_prd": {
            "goal": "Build a Go web service",
            "delivery_profile": {
                "kind": "web-service",
                "language": "go",
                "build_system": "go-mod",
                "required_files": ["go.mod", "**/*.go"],
                "forbidden_product_languages": ["python"],
            },
        },
        "tasks": [{"id": "T1", "title": "Go service scaffold"}],
        "implement_results": [{
            "files_changed": ["src/server.py"],
            "test_result": {"passed": True, "output": "1 passed"},
        }],
    }

    out = entrypoint.run(ctx)

    assert out["accepted"] is False
    assert any(item["id"] == "delivery-profile:missing-required-file" for item in out["acceptance_items"])
    assert any(item["id"] == "delivery-profile:forbidden-product-language" for item in out["acceptance_items"])
    verifier = out["verifier_rounds"][0]
    assert verifier["status"] == "fail"
    assert verifier["verdict"]["pass"] is False
    assert verifier["verdict"]["failures"] >= 2
    assert "Blocking acceptance item" in verifier["feedback"]
