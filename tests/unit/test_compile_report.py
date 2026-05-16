from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_entrypoint():
    import code_minions

    root = Path(code_minions.__file__).resolve().parent / "builtin" / "skills" / "compile-report"
    spec = importlib.util.spec_from_file_location("compile_report_entrypoint", root / "scripts" / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_compile_report_includes_product_acceptance(tmp_git_repo: Path) -> None:
    entrypoint = _load_entrypoint()
    ctx = type("Ctx", (), {})()
    ctx.workdir = tmp_git_repo
    ctx.inputs = {
        "implement_results": [{
            "trace_id": "cm_task_1",
            "commit_sha": "abc123",
            "files_changed": ["src/calculator.py"],
            "test_result": {"passed": True, "output": "1 passed"},
            "review_report": {"summary": "review skipped"},
            "rounds_used": 1,
        }],
        "tickets_output": {},
        "acceptance_output": {
            "accepted": False,
            "artifact_level": "prototype",
            "coverage": [{"id": "T1", "title": "Calculator", "status": "passed"}],
            "acceptance_items": [
                {"id": "task:T1", "title": "Calculator", "status": "pass", "kind": "task"},
                {"id": "delivery-profile:language-mismatch", "title": "Swift required", "status": "fail", "kind": "delivery-profile"},
            ],
            "verifier_rounds": [
                {
                    "id": "acceptance-verifier-1",
                    "status": "fail",
                    "verifier": "deterministic-acceptance-verifier",
                    "feedback": "Blocking acceptance item failed.",
                }
            ],
            "blockers": [{"code": "language-mismatch", "message": "Swift required"}],
            "warnings": [],
            "evidence": {
                "build_system": "python",
                "delivery_profile": {
                    "kind": "native-macos-app",
                    "language": "swift",
                    "required_files": ["**/*.swift"],
                },
            },
        },
        "output_path": "report.md",
    }

    out = entrypoint.run(ctx)

    assert out["report_path"] == "report.md"
    assert ".devflow/evidence/traceability.json" in out["evidence_paths"]
    report = (tmp_git_repo / "report.md").read_text()
    assert "# Implementation Report" in report
    assert "Product Acceptance" in report
    assert "AI Narrative" in report
    assert "Deterministic Evidence" in report
    assert "Failure Classification" in report
    assert "`acceptance_failed`" in report
    assert "Accepted:** No" in report
    assert "Artifact Level:** prototype" in report
    assert "Delivery Profile" in report
    assert "native-macos-app" in report
    assert "language-mismatch" in report
    assert "Acceptance Items" in report
    assert "Verifier Rounds" in report
    assert "deterministic-acceptance-verifier" in report
    assert "Evidence Artifacts" in report
    traceability = (tmp_git_repo / ".devflow" / "evidence" / "traceability.md").read_text()
    assert "abc123" in traceability
    assert "Calculator" in traceability
    assert "cm_task_1" in traceability
    traceability_json = tmp_git_repo / ".devflow" / "evidence" / "traceability.json"
    assert '"trace_id": "cm_task_1"' in traceability_json.read_text()


def test_compile_report_includes_browser_acceptance_artifacts(tmp_git_repo: Path) -> None:
    entrypoint = _load_entrypoint()
    ctx = type("Ctx", (), {})()
    ctx.workdir = tmp_git_repo
    ctx.inputs = {
        "implement_results": [],
        "tickets_output": {},
        "browser_acceptance_output": {
            "accepted": False,
            "supported": True,
            "stack_id": "react-vite",
            "artifacts": {
                "desktop_screenshot": ".devflow/browser-evidence/desktop.png",
                "mobile_screenshot": ".devflow/browser-evidence/mobile.png",
            },
            "scenarios": [{
                "id": "browser:control-proximity",
                "status": "fail",
                "message": "Primary controls are visually detached from the main surface.",
            }],
        },
        "output_path": "report.md",
    }

    out = entrypoint.run(ctx)

    assert out["report_path"] == "report.md"
    assert ".devflow/evidence/browser-acceptance-output.json" in out["evidence_paths"]
    report = (tmp_git_repo / "report.md").read_text()
    assert "Browser Acceptance" in report
    assert "Failure Classification" in report
    assert "`acceptance_failed`" in report
    assert "desktop_screenshot" in report
    assert ".devflow/browser-evidence/mobile.png" in report
    assert "browser:control-proximity" in report
