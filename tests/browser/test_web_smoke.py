"""Opt-in Playwright smoke tests for the Web dashboard.

Run with:
    CODE_MINIONS_BROWSER_E2E=1 pytest tests/browser -q
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest

BROWSER_E2E_ENABLED = os.environ.get("CODE_MINIONS_BROWSER_E2E") == "1"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _python_env() -> dict[str, str]:
    env = os.environ.copy()
    src = Path(__file__).resolve().parents[2] / "src"
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{src}{os.pathsep}{existing}" if existing else str(src)
    return env


def _wait_for_http_ok(url: str, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1.0) as response:
                if response.status == 200:
                    return
        except (OSError, URLError) as exc:
            last_error = exc
        time.sleep(0.1)
    raise AssertionError(f"server did not become ready at {url}: {last_error}")


def _run_cli(project_root: Path, *args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "code_minions.cli.main", *args],
        cwd=project_root,
        env=_python_env(),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


@pytest.fixture
def web_project(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "--allow-empty", "-q", "-m", "init"],
        cwd=tmp_path,
        check=True,
    )
    _run_cli(tmp_path, "init", ".")
    return tmp_path


@pytest.fixture
def web_server(web_project: Path):
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "code_minions.cli.main", "web", "--port", str(port)],
        cwd=web_project,
        env=_python_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_http_ok(base_url)
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.skipif(
    not BROWSER_E2E_ENABLED,
    reason="browser smoke tests are opt-in; set CODE_MINIONS_BROWSER_E2E=1",
)
def test_web_dashboard_starts_hello_world_run(web_server: str, tmp_path: Path) -> None:
    sync_api = pytest.importorskip("playwright.sync_api", reason="install with .[web-e2e]")

    screenshot = tmp_path / "web-smoke-failure.png"
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        try:
            page.goto(web_server, wait_until="domcontentloaded")
            page.get_by_text("No runs yet").wait_for()
            page.get_by_role("link", name="+ New Run").click()

            page.locator('select[name="workflow"]').select_option("hello-world")
            page.locator('input[name="name"]').wait_for()
            page.locator('input[name="name"]').fill("browser")
            page.get_by_role("button", name="Start Run").click()

            page.locator("#run-status").get_by_text("success").wait_for(timeout=10_000)
            page.locator("#step-greet").get_by_text("success").wait_for(timeout=10_000)
            assert "/runs/r_" in page.url
        except Exception:
            page.screenshot(path=str(screenshot), full_page=True)
            raise
        finally:
            browser.close()
