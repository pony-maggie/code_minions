from __future__ import annotations

import importlib.util
from pathlib import Path

import code_minions
from code_minions.engine.skill import load_skill


def _load_entrypoint():
    root = Path(code_minions.__file__).resolve().parent / "builtin" / "skills" / "web-ui-acceptance-review"
    spec = importlib.util.spec_from_file_location("web_ui_acceptance_entrypoint", root / "scripts" / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_builtin_web_ui_acceptance_skill_uses_deterministic_entrypoint() -> None:
    root = Path(code_minions.__file__).resolve().parent / "builtin" / "skills" / "web-ui-acceptance-review"
    skill = load_skill(root)

    assert skill.meta.entrypoint_script == "scripts/run.py"


def test_non_web_prd_is_skipped_without_failure(tmp_git_repo: Path) -> None:
    entrypoint = _load_entrypoint()
    ctx = type("Ctx", (), {})()
    ctx.workdir = tmp_git_repo
    ctx.inputs = {
        "structured_prd": {
            "goal": "Build a CLI calculator",
            "delivery_profile": {"stack_id": "python-cli", "kind": "cli"},
        },
    }

    out = entrypoint.run(ctx)

    assert out["accepted"] is True
    assert out["supported"] is False
    assert out["scenarios"][0]["status"] == "skip"
    assert "not a Web UI" in out["scenarios"][0]["message"]


def test_unsupported_web_stack_warns_without_blocking(tmp_git_repo: Path) -> None:
    entrypoint = _load_entrypoint()
    ctx = type("Ctx", (), {})()
    ctx.workdir = tmp_git_repo
    ctx.inputs = {
        "structured_prd": {
            "goal": "Build a browser app",
            "delivery_profile": {"kind": "web-app", "stack_id": "python-web"},
        },
    }

    out = entrypoint.run(ctx)

    assert out["accepted"] is True
    assert out["supported"] is False
    assert out["scenarios"][0]["status"] == "warn"
    assert "not supported yet" in out["scenarios"][0]["message"]


def test_missing_playwright_blocks_supported_web_stack(tmp_git_repo: Path, monkeypatch) -> None:
    entrypoint = _load_entrypoint()
    monkeypatch.setattr(entrypoint, "_load_sync_playwright", lambda: None)
    (tmp_git_repo / "package.json").write_text(
        '{"scripts":{"build":"vite build","dev":"vite --host 127.0.0.1"}}\n'
    )
    ctx = type("Ctx", (), {})()
    ctx.workdir = tmp_git_repo
    ctx.inputs = {
        "structured_prd": {
            "goal": "Build a React Vite web app",
            "delivery_profile": {"kind": "web-app", "stack_id": "react-vite"},
        },
    }

    out = entrypoint.run(ctx)

    assert out["accepted"] is False
    assert out["supported"] is True
    assert out["scenarios"][0]["status"] == "fail"
    assert "Playwright" in out["scenarios"][0]["message"]


def test_supported_web_stack_requires_browser_screenshot_artifacts(tmp_git_repo: Path, monkeypatch) -> None:
    entrypoint = _load_entrypoint()
    monkeypatch.setattr(entrypoint, "_install_dependencies_if_needed", lambda _workdir, _scenarios: True)
    monkeypatch.setattr(entrypoint, "_run_command", lambda _command, _workdir, timeout=120: (True, "ok"))
    monkeypatch.setattr(entrypoint, "_free_port", lambda: 5173)
    monkeypatch.setattr(entrypoint, "_wait_for_url", lambda _url: True)

    class FakePage:
        def on(self, *_args, **_kwargs) -> None:
            pass

        def goto(self, *_args, **_kwargs) -> None:
            pass

        def screenshot(self, *_args, **_kwargs) -> None:
            pass

        def evaluate(self, _script):
            return {"scroll_width": 390, "inner_width": 390, "interactive_count": 1, "button_overflows": []}

        def close(self) -> None:
            pass

    class FakeBrowser:
        def new_page(self, *_args, **_kwargs) -> FakePage:
            return FakePage()

        def close(self) -> None:
            pass

    class FakeChromium:
        def launch(self) -> FakeBrowser:
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeSyncPlaywright:
        def __call__(self):
            return self

        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, *_args) -> None:
            pass

    monkeypatch.setattr(entrypoint, "_load_sync_playwright", lambda: FakeSyncPlaywright())

    class FakeProcess:
        stdout = None

        def terminate(self) -> None:
            pass

        def wait(self, timeout: int = 0) -> None:
            pass

        def kill(self) -> None:
            pass

    monkeypatch.setattr(entrypoint.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    (tmp_git_repo / "package.json").write_text(
        '{"scripts":{"build":"vite build","dev":"vite --host 127.0.0.1"}}\n'
    )

    result = entrypoint._run_react_vite(tmp_git_repo)

    assert result["accepted"] is False
    assert any(item["id"] == "browser:screenshot-artifacts" and item["status"] == "fail" for item in result["scenarios"])


def test_layout_result_fails_when_primary_controls_are_far_from_game_surface(tmp_git_repo: Path) -> None:
    entrypoint = _load_entrypoint()
    result = entrypoint._layout_scenarios_from_metrics({
        "scroll_width": 1440,
        "inner_width": 1440,
        "interactive_count": 5,
        "surface": {"x": 0, "y": 260, "width": 450, "height": 450},
        "controls": {"x": 820, "y": 900, "width": 170, "height": 240},
        "button_overflows": [],
    })

    assert any(item["id"] == "browser:control-proximity" and item["status"] == "fail" for item in result)
