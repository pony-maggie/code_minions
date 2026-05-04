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
