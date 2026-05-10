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

    assert out == {"report_path": "report.md"}
    report = (tmp_git_repo / "report.md").read_text()
    assert "# Implementation Report" in report
    assert "Product Acceptance" in report
    assert "Accepted:** No" in report
    assert "Artifact Level:** prototype" in report
    assert "Delivery Profile" in report
    assert "native-macos-app" in report
    assert "language-mismatch" in report
    assert "Acceptance Items" in report
    assert "Verifier Rounds" in report
    assert "deterministic-acceptance-verifier" in report
