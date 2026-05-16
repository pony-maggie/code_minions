from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from code_minions.delivery import infer_delivery_profile
from code_minions.stacks import stack_id_for_delivery

WEB_STACK_HINTS = ("web-app", "browser", "frontend", "react", "vite", "vue", "svelte", "next")
SUPPORTED_STACKS = {"react-vite"}


def _scenario(
    scenario_id: str,
    title: str,
    status: str,
    message: str,
    *,
    severity: str = "error",
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": scenario_id,
        "title": title,
        "status": status,
        "severity": severity,
        "message": message,
        "evidence": evidence or {},
    }


def _load_sync_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    return sync_playwright


def _is_web_ui_profile(profile: dict[str, Any]) -> bool:
    text = "\n".join(str(profile.get(key, "")) for key in ("kind", "framework", "build_system", "stack_id")).lower()
    return any(hint in text for hint in WEB_STACK_HINTS)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _npm_scripts(workdir: Path) -> dict[str, str]:
    package_json = workdir / "package.json"
    if not package_json.exists():
        return {}
    try:
        data = json.loads(package_json.read_text())
    except json.JSONDecodeError:
        return {}
    scripts = data.get("scripts")
    return scripts if isinstance(scripts, dict) else {}


def _run_command(command: list[str], workdir: Path, timeout: int = 120) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            command,
            cwd=workdir,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return False, str(exc)
    output = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
    return proc.returncode == 0, output


def _wait_for_url(url: str, timeout: int = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 500:
                    return True
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.4)
    return False


def _rect_distance(a: dict[str, Any], b: dict[str, Any]) -> tuple[float, float]:
    left_gap = max(float(a["x"]) - (float(b["x"]) + float(b["width"])), float(b["x"]) - (float(a["x"]) + float(a["width"])), 0)
    top_gap = max(float(a["y"]) - (float(b["y"]) + float(b["height"])), float(b["y"]) - (float(a["y"]) + float(a["height"])), 0)
    return left_gap, top_gap


def _layout_scenarios_from_metrics(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    scroll_width = float(metrics.get("scroll_width") or 0)
    inner_width = float(metrics.get("inner_width") or 0)
    if inner_width and scroll_width > inner_width + 2:
        scenarios.append(_scenario(
            "browser:mobile-overflow",
            "No horizontal overflow",
            "fail",
            f"Page scroll width {scroll_width:g}px exceeds viewport width {inner_width:g}px.",
            evidence={"scroll_width": scroll_width, "inner_width": inner_width},
        ))
    else:
        scenarios.append(_scenario(
            "browser:mobile-overflow",
            "No horizontal overflow",
            "pass",
            "Viewport has no horizontal overflow.",
            severity="info",
            evidence={"scroll_width": scroll_width, "inner_width": inner_width},
        ))

    if int(metrics.get("interactive_count") or 0) <= 0:
        scenarios.append(_scenario(
            "browser:interactive-elements",
            "Interactive controls exist",
            "fail",
            "No visible buttons, links, inputs, or controls were found.",
        ))
    else:
        scenarios.append(_scenario(
            "browser:interactive-elements",
            "Interactive controls exist",
            "pass",
            "Visible interactive controls were found.",
            severity="info",
        ))

    button_overflows = metrics.get("button_overflows") or []
    if button_overflows:
        scenarios.append(_scenario(
            "browser:button-text-overflow",
            "Button labels fit",
            "fail",
            "One or more button labels overflow their button box.",
            evidence={"button_overflows": button_overflows},
        ))
    else:
        scenarios.append(_scenario(
            "browser:button-text-overflow",
            "Button labels fit",
            "pass",
            "Button labels fit inside their controls.",
            severity="info",
        ))

    surface = metrics.get("surface")
    controls = metrics.get("controls")
    if surface and controls:
        horizontal_gap, vertical_gap = _rect_distance(surface, controls)
        if horizontal_gap > 240 or vertical_gap > 160:
            scenarios.append(_scenario(
                "browser:control-proximity",
                "Primary controls are near the play surface",
                "fail",
                "Primary controls are visually detached from the main surface.",
                evidence={
                    "surface": surface,
                    "controls": controls,
                    "horizontal_gap": horizontal_gap,
                    "vertical_gap": vertical_gap,
                },
            ))
        else:
            scenarios.append(_scenario(
                "browser:control-proximity",
                "Primary controls are near the play surface",
                "pass",
                "Primary controls are positioned near the main surface.",
                severity="info",
                evidence={"horizontal_gap": horizontal_gap, "vertical_gap": vertical_gap},
            ))
    return scenarios


def _artifact_scenarios(workdir: Path, artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    required = ("desktop_screenshot", "mobile_screenshot")
    missing = [
        key
        for key in required
        if not artifacts.get(key) or not (workdir / str(artifacts[key])).is_file()
    ]
    if missing:
        return [_scenario(
            "browser:screenshot-artifacts",
            "Browser screenshots were captured",
            "fail",
            "Supported Web UI browser acceptance did not produce the required desktop and mobile screenshots.",
            evidence={"missing": missing, "artifacts": artifacts},
        )]
    return [_scenario(
        "browser:screenshot-artifacts",
        "Browser screenshots were captured",
        "pass",
        "Desktop and mobile screenshots were captured.",
        severity="info",
        evidence={"artifacts": artifacts},
    )]


LAYOUT_METRICS_JS = """
() => {
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 1 && rect.height > 1 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const rect = (el) => {
    if (!el) return null;
    const box = el.getBoundingClientRect();
    return { x: box.x, y: box.y, width: box.width, height: box.height };
  };
  const surfaceSelectors = [
    '[data-testid*="board"]',
    '[class*="board"]',
    '[role="grid"]',
    'canvas',
    'main'
  ];
  let surface = null;
  for (const selector of surfaceSelectors) {
    const candidates = Array.from(document.querySelectorAll(selector)).filter(visible);
    if (candidates.length) {
      surface = candidates.sort((a, b) => {
        const ar = a.getBoundingClientRect();
        const br = b.getBoundingClientRect();
        return (br.width * br.height) - (ar.width * ar.height);
      })[0];
      break;
    }
  }
  const interactive = Array.from(document.querySelectorAll('button, [role="button"], a[href], input, select, textarea')).filter(visible);
  let controls = null;
  if (interactive.length) {
    const boxes = interactive.map((el) => el.getBoundingClientRect());
    const left = Math.min(...boxes.map((box) => box.left));
    const top = Math.min(...boxes.map((box) => box.top));
    const right = Math.max(...boxes.map((box) => box.right));
    const bottom = Math.max(...boxes.map((box) => box.bottom));
    controls = { x: left, y: top, width: right - left, height: bottom - top };
  }
  const buttonOverflows = interactive
    .filter((el) => el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1)
    .map((el) => ({ text: (el.textContent || '').trim().slice(0, 80), scrollWidth: el.scrollWidth, clientWidth: el.clientWidth }));
  return {
    scroll_width: document.documentElement.scrollWidth,
    inner_width: window.innerWidth,
    interactive_count: interactive.length,
    surface: rect(surface),
    controls,
    button_overflows: buttonOverflows
  };
}
"""


def _install_dependencies_if_needed(workdir: Path, scenarios: list[dict[str, Any]]) -> bool:
    if (workdir / "node_modules").exists():
        return True
    if not shutil.which("npm"):
        scenarios.append(_scenario(
            "browser:npm",
            "npm is available",
            "warn",
            "npm is not available; browser acceptance could not start the Web UI.",
            severity="warning",
        ))
        return False
    ok, output = _run_command(["npm", "install", "--no-audit", "--fund=false"], workdir, timeout=180)
    if not ok:
        scenarios.append(_scenario(
            "browser:npm-install",
            "Dependencies install",
            "warn",
            "npm install failed; browser acceptance could not start the Web UI.",
            severity="warning",
            evidence={"output_tail": output[-4000:]},
        ))
    return ok


def _run_react_vite(workdir: Path) -> dict[str, Any]:
    scenarios: list[dict[str, Any]] = []
    artifacts_dir = workdir / ".devflow" / "browser-evidence"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    scripts = _npm_scripts(workdir)
    if not scripts:
        return {
            "accepted": False,
            "supported": True,
            "scenarios": [_scenario("browser:package-json", "package.json scripts exist", "fail", "No npm scripts were found.")],
            "artifacts": {},
        }

    sync_playwright = _load_sync_playwright()
    if sync_playwright is None:
        scenarios.append(_scenario(
            "browser:playwright",
            "Playwright available",
            "fail",
            "Playwright is not installed, so supported Web UI browser screenshot acceptance cannot run.",
        ))
        return {"accepted": False, "supported": True, "scenarios": scenarios, "artifacts": {}}

    if not _install_dependencies_if_needed(workdir, scenarios):
        return {"accepted": True, "supported": True, "scenarios": scenarios, "artifacts": {}}

    if "build" in scripts:
        ok, output = _run_command(["npm", "run", "build"], workdir, timeout=180)
        scenarios.append(_scenario(
            "browser:build",
            "Production build",
            "pass" if ok else "fail",
            "npm run build passed." if ok else "npm run build failed.",
            severity="info" if ok else "error",
            evidence={"output_tail": output[-4000:]},
        ))
        if not ok:
            return {"accepted": False, "supported": True, "scenarios": scenarios, "artifacts": {}}

    port = _free_port()
    url = f"http://127.0.0.1:{port}/"
    command = ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", str(port)]
    proc = subprocess.Popen(command, cwd=workdir, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    artifacts: dict[str, Any] = {"url": url}
    try:
        if not _wait_for_url(url):
            output = ""
            if proc.stdout:
                try:
                    output = proc.stdout.read()
                except OSError:
                    output = ""
            scenarios.append(_scenario(
                "browser:dev-server",
                "Dev server starts",
                "fail",
                "Vite dev server did not become reachable.",
                evidence={"output_tail": output[-4000:]},
            ))
            return {"accepted": False, "supported": True, "scenarios": scenarios, "artifacts": artifacts}
        scenarios.append(_scenario("browser:dev-server", "Dev server starts", "pass", "Vite dev server is reachable.", severity="info"))

        console_errors: list[str] = []
        page_errors: list[str] = []
        request_failures: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                for name, viewport in {
                    "desktop": {"width": 1440, "height": 1000},
                    "mobile": {"width": 390, "height": 844},
                }.items():
                    page = browser.new_page(viewport=viewport)
                    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
                    page.on("pageerror", lambda exc: page_errors.append(str(exc)))
                    page.on("requestfailed", lambda req: request_failures.append(req.url))
                    page.goto(url, wait_until="networkidle", timeout=30000)
                    screenshot_path = artifacts_dir / f"{name}.png"
                    page.screenshot(path=str(screenshot_path), full_page=True)
                    artifacts[f"{name}_screenshot"] = str(screenshot_path.relative_to(workdir))
                    metrics = page.evaluate(LAYOUT_METRICS_JS)
                    (artifacts_dir / f"{name}-layout.json").write_text(json.dumps(metrics, indent=2, sort_keys=True))
                    artifacts[f"{name}_layout"] = str((artifacts_dir / f"{name}-layout.json").relative_to(workdir))
                    if name == "mobile":
                        scenarios.extend(_layout_scenarios_from_metrics(metrics))
                    page.close()
            finally:
                browser.close()
        scenarios.extend(_artifact_scenarios(workdir, artifacts))

        runtime_messages = console_errors + page_errors + request_failures
        scenarios.append(_scenario(
            "browser:runtime-errors",
            "No browser runtime errors",
            "fail" if runtime_messages else "pass",
            "Browser reported runtime errors." if runtime_messages else "No console/page/request errors were observed.",
            severity="error" if runtime_messages else "info",
            evidence={"messages": runtime_messages[:20]},
        ))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    return {
        "accepted": not any(item["status"] == "fail" for item in scenarios),
        "supported": True,
        "scenarios": scenarios,
        "artifacts": artifacts,
    }


def run(ctx) -> dict[str, Any]:
    workdir = Path(ctx.workdir)
    structured_prd = ctx.inputs["structured_prd"]
    delivery_profile = infer_delivery_profile(structured_prd)
    stack_id = stack_id_for_delivery(delivery_profile)

    if not _is_web_ui_profile(delivery_profile):
        return {
            "accepted": True,
            "supported": False,
            "stack_id": stack_id,
            "scenarios": [_scenario(
                "browser:not-web-ui",
                "Web UI profile",
                "skip",
                "Delivery profile is not a Web UI, so browser acceptance was skipped.",
                severity="info",
                evidence={"delivery_profile": delivery_profile},
            )],
            "artifacts": {},
        }

    if stack_id not in SUPPORTED_STACKS:
        return {
            "accepted": True,
            "supported": False,
            "stack_id": stack_id,
            "scenarios": [_scenario(
                "browser:unsupported-stack",
                "Supported Web UI stack",
                "warn",
                f"Web UI stack `{stack_id or 'unknown'}` is not supported yet for browser automation.",
                severity="warning",
                evidence={"delivery_profile": delivery_profile},
            )],
            "artifacts": {},
        }

    result = _run_react_vite(workdir)
    return {
        "accepted": result["accepted"],
        "supported": result["supported"],
        "stack_id": stack_id,
        "scenarios": result["scenarios"],
        "artifacts": result["artifacts"],
    }
