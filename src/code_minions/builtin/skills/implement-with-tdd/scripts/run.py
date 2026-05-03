"""implement-with-tdd entrypoint.

Orchestrates the TDD + review double loop:

  outer (reviewer loop, up to reviewer_max_rounds):
      inner (self-heal loop, up to self_heal_max_rounds):
          LLM -> write/update tests + implementation -> shell run tests
          if green: break
      if tests never green: bail
      run ai-code-review (nested skill)
      if no blocker/major issues: break
      feed issues back to coder
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any

from code_minions.agent_profiles import resolve_agent_profile
from code_minions.delivery import (
    execution_profile_for_delivery,
    infer_delivery_profile,
    repair_unique_unresolved_relative_imports,
    validate_delivery_profile,
)
from code_minions.engine.skill_runtime import SkillExecutionError
from code_minions.failure_playbook import failure_hints_for_output
from code_minions.gates import (
    GateFinding,
    delivery_issues_to_findings,
    findings_to_dicts,
    findings_to_text,
    runtime_findings_for_output,
)
from code_minions.implementation_context import build_implementation_context
from code_minions.stacks import stack_id_for_delivery

CODER_SYS = """You are Coder. Given a ticket and project context, write failing tests FIRST,
then minimal implementation to make them pass. Put files inside the worktree.
Prefer using the Write/Edit tools to create or update files. If you use tools, final reply should be
a small JSON object such as {"reasoning": "done"}. If you cannot use tools, reply with JSON:
{"files_written": [{"path": "...", "content": "..."}], "reasoning": "..."}.
Do not introduce a different language or framework than the existing project unless the ticket explicitly requires it.
Use Delete to remove stale or wrongly placed files that break the requested project layout.
Do not invent remote package URLs. If a dependency name is requested without a verified URL, implement the MVP
with standard-library/local code first and leave a TODO instead of adding an unverified package dependency.
For XcodeGen projects, the root project.yml is the authoritative build file; update it instead of creating
nested project.yml files unless the root project is intentionally nested.
Do not include explanatory prose outside the final JSON.
"""


def _extract_json_object(content: str, *, require_files: bool) -> dict[str, Any]:
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        raise ValueError(f"LLM did not return JSON: {content[:200]}")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned invalid JSON: {e}; content={content[:200]!r}") from e
    if not isinstance(data, dict):
        raise ValueError(f"LLM JSON must be an object, got {type(data).__name__}")
    if require_files:
        files = data.get("files_written")
        if not isinstance(files, list) or not files:
            raise ValueError("LLM JSON must include non-empty files_written list")
    return data


def _response_diagnostics(resp) -> str:
    usage = getattr(resp, "usage", None)
    input_tokens = getattr(usage, "input_tokens", "?")
    output_tokens = getattr(usage, "output_tokens", "?")
    message = getattr(resp, "message", None)
    content = getattr(message, "content", "") if message is not None else ""
    tool_calls = getattr(message, "tool_calls", []) if message is not None else []
    if tool_calls:
        calls = ", ".join(getattr(tc, "name", "?") for tc in tool_calls)
        message_summary = f"tool_calls=[{calls}]"
    else:
        message_summary = f"content={content[:500]!r}"
    return (
        f"{message_summary}; stop_reason={getattr(resp, 'stop_reason', '?')}; "
        f"model={getattr(resp, 'model', '?')}; usage=input:{input_tokens},output:{output_tokens}"
    )


def _written_files_from_paths(workdir, changed_paths: set[str]) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    for path in sorted(changed_paths):
        file_path = workdir / path
        if file_path.is_file():
            files.append({"path": path, "content": file_path.read_text()})
    return files


def _llm_call(
    ctx,
    system: str,
    user: str,
    *,
    max_attempts: int = 2,
    max_tool_rounds: int = 24,
    max_read_calls: int = 4,
) -> dict[str, Any]:
    from code_minions.engine.skill_runtime import LOCAL_TOOL_SCHEMAS
    from code_minions.engine.tool_executor import (
        ToolExecutionContext,
        ToolExecutor,
        record_llm_call,
    )
    from code_minions.llm.types import Message, Tool
    messages = [Message(role="system", content=system), Message(role="user", content=user)]
    tools = [
        Tool(name=name, description=f"Built-in local tool {name}", input_schema=LOCAL_TOOL_SCHEMAS[name])
        for name in ("Read", "Write", "Edit", "Delete")
    ]
    extras = getattr(ctx, "extras", {}) or {}
    executor = ToolExecutor(ToolExecutionContext(
        workdir=ctx.workdir,
        workspace_mode=extras.get("workspace_mode", "git-worktree"),
        event_recorder=extras.get("run_event_recorder"),
        step_id=extras.get("current_step_id"),
    ))
    changed_paths: set[str] = set()
    last_error = ""
    last_diagnostics = ""
    json_attempts = 0
    tool_rounds = 0
    read_calls = 0
    close_after_mutation = False
    while json_attempts < max_attempts and tool_rounds < max_tool_rounds:
        resp = ctx.llm.chat(
            messages=messages,
            tools=None if close_after_mutation else tools,
            temperature=0.2,
            max_tokens=_llm_max_tokens(ctx),
        )
        record_llm_call(
            extras.get("run_event_recorder"),
            step_id=extras.get("current_step_id"),
            skill="implement-with-tdd",
            response=resp,
        )
        messages.append(resp.message)
        diagnostics = _response_diagnostics(resp)
        last_diagnostics = diagnostics
        if resp.message.tool_calls:
            tool_rounds += 1
            mutated = False
            for tc in resp.message.tool_calls:
                try:
                    if tc.name == "Read":
                        read_calls += 1
                    if tc.name == "Read" and read_calls > max_read_calls:
                        result = (
                            "[error] Read budget exceeded for this implementation step. "
                            "Stop calling Read. Use Write or Edit now, then finish with a small JSON object."
                        )
                    else:
                        result = executor.run_local(tc.name, tc.arguments, call_id=tc.id)
                    if tc.name in {"Write", "Edit", "Delete"} and isinstance(tc.arguments.get("path"), str):
                        changed_paths.add(tc.arguments["path"])
                        mutated = True
                except Exception as e:
                    result = f"[error] {e}"
                messages.append(Message(role="tool", tool_call_id=tc.id, content=result, name=tc.name))
            if mutated:
                close_after_mutation = True
                messages.append(Message(
                    role="user",
                    content=(
                        "You have made file changes. Stop calling tools for this implementation pass; "
                        "reply with a small JSON object now, such as {\"reasoning\": \"done\"}."
                    ),
                ))
            continue
        try:
            data = _extract_json_object(resp.message.content, require_files=not changed_paths)
            if changed_paths:
                data["files_written"] = [
                    {"path": p, "content": (ctx.workdir / p).read_text()}
                    for p in sorted(changed_paths)
                    if (ctx.workdir / p).is_file()
                ]
            return data
        except ValueError as e:
            if changed_paths:
                return {
                    "files_written": _written_files_from_paths(ctx.workdir, changed_paths),
                    "reasoning": resp.message.content[:1000],
                }
            json_attempts += 1
            last_error = f"{e}; {diagnostics}"
            if json_attempts >= max_attempts:
                raise RuntimeError(last_error) from e
            messages.append(Message(
                role="user",
                content=(
                    f"{last_error}\n\n"
                    "Use the Write/Edit/Delete tools to make file changes, then reply with a small valid JSON object only. "
                    "If tools are unavailable, include a non-empty files_written list with path/content entries. "
                    "Use double-quoted property names and string values. "
                    "Do not include markdown fences or explanatory prose."
                ),
            ))
    if tool_rounds >= max_tool_rounds:
        raise RuntimeError(
            f"LLM exceeded tool_call round limit={max_tool_rounds}; "
            f"last assistant response: {last_diagnostics}"
        )
    raise RuntimeError(last_error or f"LLM did not return JSON; last assistant response: {last_diagnostics}")


def _policies(ctx) -> dict[str, Any]:
    skill = getattr(ctx, "skill", None)
    meta = getattr(skill, "meta", None)
    policies = getattr(meta, "policies", {}) if meta is not None else {}
    return policies if isinstance(policies, dict) else {}


def _llm_max_tokens(ctx) -> int:
    skill = getattr(ctx, "skill", None)
    meta = getattr(skill, "meta", None)
    llm = getattr(meta, "llm", None) if meta is not None else None
    value = getattr(llm, "max_tokens", None)
    return int(value) if value is not None else 16000


def _build_file_context(workdir) -> str:
    parts = []
    for name in ("Package.swift", "project.yml", "package.json", "pyproject.toml", "pytest.ini"):
        path = workdir / name
        if path.is_file():
            note = ""
            if name == "project.yml":
                note = " (XcodeGen root build file)"
            parts.append(f"--- {name}{note} ---\n{path.read_text()[:4000]}")
    if not parts:
        return "No recognized root build/test configuration files found."
    return "\n\n".join(parts)


def _source_context(workdir) -> str:
    src = workdir / "src"
    if not src.is_dir():
        return "No src/ directory found."

    files = [
        path
        for path in sorted(src.rglob("*"))
        if path.is_file()
        and path.suffix in {".ts", ".tsx"}
        and ".test." not in path.name.lower()
        and ".spec." not in path.name.lower()
    ]
    if not files:
        return "No existing TypeScript source files found under src/."

    rel_files = [path.relative_to(workdir).as_posix() for path in files[:80]]
    priority = {"src/types.ts", "src/App.tsx", "src/GameBoard.tsx", "src/game/types.ts"}
    excerpt_paths = [path for path in files if path.relative_to(workdir).as_posix() in priority]
    for path in files:
        if len(excerpt_paths) >= 6:
            break
        if path not in excerpt_paths:
            excerpt_paths.append(path)

    excerpts = []
    for path in excerpt_paths:
        rel = path.relative_to(workdir).as_posix()
        excerpts.append(f"--- {rel} ---\n{path.read_text(errors='ignore')[:2200]}")

    return (
        "Existing source files:\n"
        + "\n".join(f"- {path}" for path in rel_files)
        + "\n\nExisting source excerpts:\n"
        + "\n\n".join(excerpts)
    )


def _project_context(workdir) -> str:
    root_entries = sorted(p.name + ("/" if p.is_dir() else "") for p in workdir.iterdir())[:80]
    markers = [
        name for name in (
            "AGENTS.md",
            "Package.swift",
            "project.yml",
            "pyproject.toml",
            "package.json",
            "pytest.ini",
        )
        if (workdir / name).exists()
    ]
    agents = ""
    agents_path = workdir / "AGENTS.md"
    if agents_path.is_file():
        agents = agents_path.read_text()[:4000]
    return (
        f"Project markers: {markers}\n"
        f"Top-level entries: {root_entries}\n\n"
        f"AGENTS.md excerpt:\n{agents}\n\n"
        f"Authoritative build/test configuration:\n{_build_file_context(workdir)}\n\n"
        f"Current source context:\n{_source_context(workdir)}"
    )


def _ticket_delivery_profile(ticket: dict[str, Any]) -> dict[str, Any]:
    profile = ticket.get("delivery_profile")
    if not isinstance(profile, dict) or not profile:
        return {}
    return infer_delivery_profile({
        "goal": f"{ticket.get('title', '')}\n{ticket.get('description', '')}",
        "delivery_profile": profile,
    })


def _agent_profile_for_ticket(ticket: dict[str, Any], policies: dict[str, Any]):
    delivery_profile = _ticket_delivery_profile(ticket)
    requested = policies.get("agent_profile")
    return resolve_agent_profile(
        role="implementer",
        delivery_profile=delivery_profile,
        requested_profile_id=str(requested) if requested else None,
    )


def _source_for_profile(profile: dict[str, Any]) -> str:
    return stack_id_for_delivery(profile) or "default"


def _delivery_profile_context(ticket: dict[str, Any]) -> str:
    profile = _ticket_delivery_profile(ticket)
    if not profile:
        return "No explicit delivery profile. Follow the PRD, ticket, and existing project conventions."
    return json.dumps(profile, ensure_ascii=False, sort_keys=True)


def _looks_like_react_vite_profile(profile: dict[str, Any]) -> bool:
    text = "\n".join(
        str(profile.get(key, ""))
        for key in ("kind", "language", "framework", "build_system", "test_command")
    ).lower()
    return "typescript" in text and "react" in text and "vite" in text


def _looks_like_swift_xcodegen_profile(profile: dict[str, Any]) -> bool:
    text = "\n".join(
        str(profile.get(key, ""))
        for key in ("kind", "language", "framework", "build_system", "test_command")
    ).lower()
    return "swift" in text and ("xcodegen" in text or "native-macos-app" in text)


def _ticket_text(ticket: dict[str, Any]) -> str:
    return json.dumps(ticket, ensure_ascii=False).lower()


def _looks_like_turn_based_board_game(ticket: dict[str, Any]) -> bool:
    text = _ticket_text(ticket)
    has_board_game = any(token in text for token in ("gomoku", "五子棋", "棋盘", "board game"))
    has_turns = any(token in text for token in ("轮流", "回合", "turn", "currentplayer", "black", "white", "黑棋", "白棋"))
    return has_board_game and has_turns


def _delivery_guidance_context(ticket: dict[str, Any]) -> str:
    profile = _ticket_delivery_profile(ticket)
    if not profile:
        return "No delivery-specific guidance."

    lines: list[str] = []
    if _looks_like_react_vite_profile(profile):
        lines.append(
            "For React/Vite projects that use Vitest and React Testing Library, configure Vitest with "
            "`test: { environment: 'jsdom' }` in `vite.config.ts` or `vitest.config.ts`, keep `jsdom` "
            "in devDependencies, add setupFiles, and explicitly clean up rendered DOM between tests "
            "with a setup file such as `import { cleanup } from '@testing-library/react'; "
            "import { afterEach } from 'vitest'; afterEach(cleanup);`. Ensure test relative imports "
            "match files you actually create; create the referenced component/hook/module, update the "
            "import path, or delete orphan tests instead of leaving imports to missing files. Use Vitest "
            "mock helpers such as `vi.fn()` imported from `vitest`; do not use Jest globals like `jest.*`. "
            "Keep the Vitest test API mode consistent: either explicitly import every used test API "
            "(`describe`, `it`/`test`, `expect`, `beforeEach`/`afterEach`, `vi`) from `vitest` in each "
            "test file, or deliberately configure `test: { globals: true }`; do not leave tests using "
            "implicit globals under the default Vitest config. "
            "If you use Testing Library jest-dom matchers such as `toHaveTextContent` or "
            "`toBeInTheDocument`, add `@testing-library/jest-dom` to devDependencies and import "
            "`@testing-library/jest-dom/vitest` from the Vitest setup file; otherwise use built-in "
            "assertions like `expect(element.textContent).toContain(...)`. In TypeScript setup files, "
            "use ES module import syntax such as `import '@testing-library/jest-dom/vitest';`; never "
            "write CSS-style `@import` statements in `.ts` or `.tsx` files. "
            "Preserve existing exported type contracts across tasks, especially "
            "shared player/cell/board types. Do not replace a working `'black' | 'white'` union with an "
            "incompatible enum unless every caller is updated; if you do use an enum, use members such as "
            "`Stone.Black` instead of raw string literals in setters and comparisons. "
            "Use a single canonical shared type module. If `src/types.ts` already exists, extend it and "
            "do not create `src/types/index.ts` or another shadow type entry; update imports consistently "
            "so callers do not split between incompatible `Cell`/`CellState` or board type definitions. "
            "When adding React hooks such as `useState`, `useCallback`, `useMemo`, or `useEffect`, import "
            "React hooks explicitly from `react` in the file that uses them. "
            "Keep TypeScript interfaces consistent across existing callers and new modules; `tsc --noEmit` "
            "must pass before tests run. Create at least one real `*.test.ts`, `*.test.tsx`, `*.spec.ts`, "
            "or `*.spec.tsx` file for delivered behavior; do not rely on placeholder or no-test Vitest runs. "
            "The current working directory is already the project root; never create a nested `worktree/` "
            "or `workspace/` app inside it. Put `index.html`, `package.json`, source files, and tests at "
            "the root-level React/Vite project paths. "
            "Do not invent future npm dependency versions. Use published, conservative package ranges for "
            "React/Vite testing dependencies, and omit a dependency if the generated code does not need it. "
            "Prefer plain CSS for lightweight React/Vite MVPs. If you add `postcss.config.*`, "
            "`tailwind.config.*`, or Tailwind/PostCSS plugins, package.json must declare every referenced "
            "plugin such as `tailwindcss` and `autoprefixer` in devDependencies; otherwise remove the config. "
            "When Testing Library queries board cells by accessible name, use exact names or anchored regexes "
            "such as `{ name: /^行1列1, 空$/ }`; broad coordinate regexes like `/行1列1.*空/` can also match "
            "`行1列10` and produce false test failures. Stable per-cell test ids are also acceptable. "
            "Across multi-task workflows, preserve the stable DOM contracts that earlier generated tests "
            "already assert. For board games, if a cell test id or class such as `cell-7-7`, `black`, "
            "`white`, `last-move`, or `winning` has been introduced, extend it rather than replacing it "
            "with an incompatible child-only marker. "
            "For mouse/touch activation acceptance, prefer `await user.click(cell)` against the same clickable "
            "cell control. Do not use low-level `user.pointer(... '[pointerdown]')` as a generic touch-support "
            "test in jsdom; only use `fireEvent.pointerDown(cell)` when intentionally testing an explicit "
            "`onPointerDown` contract. "
            "jsdom does not compute layout, so tests must not depend on real `getBoundingClientRect()` "
            "sizes for coordinate-based clicks. Prefer semantic click targets such as board-cell buttons, "
            "roles, labels, or stable test ids; if coordinate clicks are unavoidable, mock "
            "`getBoundingClientRect` with non-zero width/height before firing events. "
            "Do not assert flex/grid centering or responsive layout with `window.getComputedStyle()` in "
            "Vitest/jsdom; external CSS imports may not be reflected there. Assert classes/structure in "
            "unit tests and reserve visual layout verification for browser/e2e checks."
        )
    if _looks_like_swift_xcodegen_profile(profile):
        lines.append(
            "For Swift/XcodeGen projects, every application and `bundle.unit-test` target must either "
            "set `GENERATE_INFOPLIST_FILE: YES` in target settings or provide an explicit "
            "`INFOPLIST_FILE`/`info.path`; do this for the test bundle as well as the app target."
        )
    if _looks_like_turn_based_board_game(ticket):
        lines.append(
            "For turn-based board game tests, use valid public move sequences that alternate players. "
            "When testing one player's win, interleave opponent filler moves that do not block the line "
            "or accidentally create an earlier win. Do not assert impossible same-player consecutive "
            "moves through the normal move API. For Gomoku white-win tests through the public click API, "
            "remember white only moves on turns 2, 4, 6, 8, and 10; place black filler stones far away "
            "from the target line so black cannot complete five first. For full-board draw tests, prefer "
            "a pure board-state/draw helper test or a fast deterministic setup over 225 slow `userEvent` "
            "clicks, which often time out or accidentally create a win before the board is full."
        )

    return "\n".join(lines) if lines else "No delivery-specific guidance."


def _run_delivery_profile_check(workdir, ticket: dict[str, Any]) -> tuple[bool, str]:
    profile = _ticket_delivery_profile(ticket)
    if not profile:
        return True, ""
    issues = validate_delivery_profile(workdir, profile)
    if not issues:
        return True, "Delivery profile check passed."
    errors = [issue for issue in issues if issue.get("severity", "error") == "error"]
    warnings = [issue for issue in issues if issue.get("severity") == "warning"]
    lines = ["Delivery profile check failed:" if errors else "Delivery profile warnings:"]
    for issue in errors:
        lines.append(f"- error {issue['code']}: {issue['message']}")
    for issue in warnings:
        lines.append(f"- warning {issue['code']}: {issue['message']}")
    lines.append("")
    lines.append("Delivery profile:")
    lines.append(json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True))
    return not errors, "\n".join(lines)


def _run_delivery_profile_gate(workdir, ticket: dict[str, Any]) -> tuple[bool, str, list[GateFinding]]:
    profile = _ticket_delivery_profile(ticket)
    if not profile:
        return True, "", []
    issues = validate_delivery_profile(workdir, profile)
    findings = delivery_issues_to_findings(issues, source=_source_for_profile(profile))
    errors = [finding for finding in findings if finding.severity == "error"]
    warnings = [finding for finding in findings if finding.severity == "warning"]
    heading = "Delivery profile check failed:" if errors else "Delivery profile warnings:" if warnings else ""
    text = findings_to_text(findings)
    if heading and text:
        text = f"{heading}\n{text}"
    if not findings:
        text = "Delivery profile check passed."
    return not errors, text, findings


def _runtime_gate_findings(output: str, ticket: dict[str, Any]) -> list[GateFinding]:
    profile = _ticket_delivery_profile(ticket)
    return runtime_findings_for_output(output, source=_source_for_profile(profile))


def _record_gate_findings(ctx, findings: list[GateFinding]) -> None:
    if not findings:
        return
    extras = getattr(ctx, "extras", {}) or {}
    recorder = extras.get("run_event_recorder")
    step_id = extras.get("current_step_id")
    if recorder:
        recorder("gate.findings", {
            "step_id": step_id,
            "findings": findings_to_dicts(findings),
        })


def _failure_playbook_context(output: str) -> str:
    hints = failure_hints_for_output(output)
    if not hints:
        return ""
    lines = ["Failure playbook hints:"]
    lines.extend(f"- {hint}" for hint in hints)
    return "\n".join(lines)


REACT_VITE_PACKAGE_DEPENDENCIES = {
    "react": "18.3.1",
    "react-dom": "18.3.1",
}

REACT_VITE_PACKAGE_DEV_DEPENDENCIES = {
    "@testing-library/jest-dom": "6.6.3",
    "@testing-library/react": "16.3.2",
    "@testing-library/user-event": "14.6.1",
    "@types/react": "18.3.12",
    "@types/react-dom": "18.3.1",
    "@vitejs/plugin-react": "4.3.4",
    "jsdom": "25.0.1",
    "typescript": "5.6.3",
    "vite": "5.4.11",
    "vitest": "2.1.8",
}

REACT_VITE_SETUP_TESTS = """import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(cleanup)
"""

REACT_VITE_VITE_CONFIG = """import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: './src/setupTests.ts',
  },
})
"""

REACT_VITE_TSCONFIG = """{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
"""

REACT_VITE_TSCONFIG_NODE = """{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true,
    "strict": true
  },
  "include": ["vite.config.ts"]
}
"""

REACT_VITE_INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Gomoku</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
"""

REACT_VITE_MAIN = """import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
"""

REACT_VITE_APP = """export default function App() {
  return <main>Ready</main>
}
"""

REACT_VITE_INDEX_CSS = """html {
  box-sizing: border-box;
}

*, *::before, *::after {
  box-sizing: inherit;
}

body {
  margin: 0;
  font-family: system-ui, sans-serif;
}
"""

REACT_VITE_APP_TEST = """import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import App from './App'

describe('App', () => {
  it('renders the app shell', () => {
    const { container } = render(<App />)

    expect(container).toBeDefined()
  })
})
"""

REACT_VITE_TEST_API_NAMES = ("afterEach", "beforeEach", "describe", "expect", "it", "test", "vi")
REACT_VITE_VITEST_IMPORT_RE = re.compile(
    r"""import\s*\{(?P<names>[^}]+)\}\s*from\s*['"]vitest['"]\s*;?\n?""",
    re.MULTILINE | re.DOTALL,
)
REACT_TESTING_LIBRARY_IMPORT_RE = re.compile(
    r"""import\s*\{(?P<names>[^}]+)\}\s*from\s*['"]@testing-library/react['"]\s*;?\n?""",
    re.MULTILINE | re.DOTALL,
)
REACT_VITE_BARE_DOM_CLICK_RE = re.compile(
    r"""(?m)^(?P<indent>\s*)(?P<target>[A-Za-z_$][\w$]*)\.click\(\)\s*;?\s*$"""
)
REACT_VITE_BOARD_COORDINATE_RE = re.compile(
    r"""行\s*\d+\s*列\s*\d+|第\s*\d+\s*行\s*第\s*\d+\s*列|"""
    r"""row\s*\d+[\s\S]{0,40}(?:col|column)\s*\d+""",
    re.IGNORECASE,
)
REACT_VITE_REGEX_LITERAL_RE = re.compile(r"""/(?P<pattern>\^(?:\\.|[^/\n])*)(?P<suffix>/[a-z]*)""")
REACT_VITE_NAMED_IMPORT_RE = re.compile(
    r"""import\s+(?:type\s+)?\{(?P<names>[^}]+)\}\s*from\s*['"][^'"]+['"]\s*;?""",
    re.MULTILINE | re.DOTALL,
)
REACT_VITE_TYPES_IMPORT_RE = re.compile(
    r"""import\s+(?P<typeonly>type\s+)?\{(?P<names>[^}]+)\}\s*from\s*['"](?P<source>\.{1,2}/[^'"]*types)['"]\s*;?""",
    re.MULTILINE | re.DOTALL,
)
REACT_VITE_SINGLE_POSITION_TYPES_IMPORT_RE = re.compile(
    r"""^import\s*\{\s*type\s+Position\s*\}\s*from\s*['"](?P<source>\.{1,2}/[^'"]*types)['"]\s*;?\n""",
    re.MULTILINE,
)
REACT_VITE_GLUED_IMPORT_DECLARATION_RE = re.compile(
    r"""(from\s*['"][^'"]+['"])(?=(?:interface|type|const|export|function|class)\b)"""
)
REACT_VITE_POSITION_EXPORT_RE = re.compile(r"""\bexport\s+(?:interface|type)\s+Position\b""")
REACT_VITE_POSITION_LOCAL_RE = re.compile(r"""\b(?:interface|type)\s+Position\b""")

REACT_VITE_POSITION_TYPE = """export interface Position {
  row: number
  col: number
}
"""
REACT_VITE_BAD_USER_EVENT_DYNAMIC_IMPORT_RE = re.compile(
    r"""const\s*\{\s*user\s*\}\s*=\s*await\s+import\(\s*['"]@testing-library/user-event['"]\s*\)"""
)


def _write_text_if_changed(workdir, rel_path: str, content: str) -> str | None:
    path = workdir / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_text() == content:
        return None
    path.write_text(content)
    return rel_path


def _react_vite_package_json(workdir) -> str:
    package_path = workdir / "package.json"
    if package_path.is_file():
        try:
            data = json.loads(package_path.read_text())
        except json.JSONDecodeError:
            data = {}
    else:
        data = {}

    if not isinstance(data, dict):
        data = {}
    scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
    dependencies = data.get("dependencies") if isinstance(data.get("dependencies"), dict) else {}
    dev_dependencies = data.get("devDependencies") if isinstance(data.get("devDependencies"), dict) else {}

    data["name"] = str(data.get("name") or "react-vite-app")
    data["private"] = True
    data["version"] = str(data.get("version") or "0.0.0")
    data["type"] = "module"
    data["scripts"] = {
        **scripts,
        "dev": "vite",
        "build": "tsc && vite build",
        "preview": "vite preview",
        "test": "vitest run",
    }
    data["dependencies"] = {
        **dependencies,
        **REACT_VITE_PACKAGE_DEPENDENCIES,
    }
    data["devDependencies"] = {
        **dev_dependencies,
        **REACT_VITE_PACKAGE_DEV_DEPENDENCIES,
    }
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _has_js_ts_test_file(workdir) -> bool:
    src = workdir / "src"
    if not src.is_dir():
        return False
    return any(
        path.is_file()
        and path.suffix in {".ts", ".tsx"}
        and (".test." in path.name or ".spec." in path.name)
        for path in src.rglob("*")
    )


def _react_vite_test_files(workdir) -> list:
    ignored = {".git", ".devflow", "node_modules", "dist", "coverage"}
    test_files = []
    for path in sorted(workdir.rglob("*")):
        if not path.is_file() or path.suffix not in {".ts", ".tsx", ".js", ".jsx"}:
            continue
        if any(part in ignored for part in path.relative_to(workdir).parts):
            continue
        lower_name = path.name.lower()
        if ".test." in lower_name or ".spec." in lower_name:
            test_files.append(path)
    return test_files


def _vitest_imported_test_api_names(text: str) -> set[str]:
    imported: set[str] = set()
    for match in REACT_VITE_VITEST_IMPORT_RE.finditer(text):
        for raw_name in match.group("names").split(","):
            name = raw_name.strip()
            if not name:
                continue
            imported.add(name.split(" as ", 1)[0].strip())
    return imported


def _uses_vitest_test_api(text: str, name: str) -> bool:
    if name == "vi":
        return bool(re.search(r"\bvi\s*(?:\.|\()", text))
    return bool(re.search(rf"\b{re.escape(name)}\s*\(", text))


def _stabilize_vitest_imports(text: str) -> str:
    used = {
        name
        for name in REACT_VITE_TEST_API_NAMES
        if _uses_vitest_test_api(text, name)
    }
    missing = used - _vitest_imported_test_api_names(text)
    if not missing:
        return text

    match = REACT_VITE_VITEST_IMPORT_RE.search(text)
    if match:
        names = _vitest_imported_test_api_names(match.group(0))
        names.update(missing)
        replacement = f"import {{ {', '.join(sorted(names))} }} from 'vitest'\n"
        return text[:match.start()] + replacement + text[match.end():]

    return f"import {{ {', '.join(sorted(missing))} }} from 'vitest'\n{text}"


def _anchor_board_coordinate_regex_queries(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        pattern = match.group("pattern")
        if pattern.endswith("$") or not REACT_VITE_BOARD_COORDINATE_RE.search(pattern):
            return match.group(0)
        return f"/{pattern}${match.group('suffix')}"

    return REACT_VITE_REGEX_LITERAL_RE.sub(replace, text)


def _stabilize_user_event_imports(text: str) -> str:
    return REACT_VITE_BAD_USER_EVENT_DYNAMIC_IMPORT_RE.sub(
        "const user = (await import('@testing-library/user-event')).default",
        text,
    )


def _ensure_testing_library_import(text: str, name: str) -> str:
    match = REACT_TESTING_LIBRARY_IMPORT_RE.search(text)
    if match:
        names = {part.strip() for part in match.group("names").split(",") if part.strip()}
        if name in names:
            return text
        names.add(name)
        replacement = f"import {{ {', '.join(sorted(names))} }} from '@testing-library/react'\n"
        return text[:match.start()] + replacement + text[match.end():]

    return f"import {{ {name} }} from '@testing-library/react'\n{text}"


def _stabilize_bare_dom_clicks(text: str) -> str:
    updated = REACT_VITE_BARE_DOM_CLICK_RE.sub(
        lambda match: f"{match.group('indent')}fireEvent.click({match.group('target')})",
        text,
    )
    if updated == text:
        return text
    return _ensure_testing_library_import(updated, "fireEvent")


def _stabilize_placeholder_app_smoke_test(path, text: str) -> str:
    if path.name != "App.test.tsx":
        return text
    if "renders the app shell" not in text or "getByText('Ready')" not in text:
        return text
    return REACT_VITE_APP_TEST


def _stabilize_react_vite_tests(workdir) -> set[str]:
    changed: set[str] = set()
    for path in _react_vite_test_files(workdir):
        original = path.read_text()
        updated = _stabilize_bare_dom_clicks(
            _stabilize_user_event_imports(
                _anchor_board_coordinate_regex_queries(
                    _stabilize_vitest_imports(_stabilize_placeholder_app_smoke_test(path, original))
                )
            )
        )
        if updated == original:
            continue
        path.write_text(updated)
        changed.add(path.relative_to(workdir).as_posix())
    return changed


def _imported_symbol_name(raw_name: str) -> str:
    name = raw_name.strip()
    if name.startswith("type "):
        name = name.removeprefix("type ").strip()
    return re.split(r"\s+as\s+", name, maxsplit=1)[0].strip()


def _imports_named_symbol(text: str, symbol: str) -> bool:
    for match in REACT_VITE_NAMED_IMPORT_RE.finditer(text):
        if any(_imported_symbol_name(name) == symbol for name in match.group("names").split(",")):
            return True
    return False


def _uses_position_without_local_definition(text: str) -> bool:
    return (
        bool(re.search(r"\bPosition\b", text))
        and not REACT_VITE_POSITION_LOCAL_RE.search(text)
        and not _imports_named_symbol(text, "Position")
    )


def _types_import_specifier(workdir, path) -> str:
    rel = os.path.relpath(workdir / "src" / "types", start=path.parent)
    specifier = rel.replace(os.sep, "/")
    if not specifier.startswith("."):
        specifier = f"./{specifier}"
    return specifier


def _has_local_symbol_declaration(text: str, symbol: str) -> bool:
    return bool(
        re.search(
            rf"""\b(?:export\s+)?(?:const|let|var|function|class|interface|type)\s+{re.escape(symbol)}\b""",
            text,
        )
    )


def _types_import_has_symbol(text: str, symbol: str) -> bool:
    for match in REACT_VITE_TYPES_IMPORT_RE.finditer(text):
        if any(_imported_symbol_name(name) == symbol for name in match.group("names").split(",")):
            return True
    return False


def _remove_redundant_position_type_imports(text: str) -> str:
    if not _types_import_has_symbol(text, "Position"):
        return text
    matches = list(REACT_VITE_SINGLE_POSITION_TYPES_IMPORT_RE.finditer(text))
    if not matches:
        return text
    return REACT_VITE_SINGLE_POSITION_TYPES_IMPORT_RE.sub("", text)


def _separate_glued_import_declarations(text: str) -> str:
    return REACT_VITE_GLUED_IMPORT_DECLARATION_RE.sub(r"\1\n", text)


def _alias_board_type_import_collision(text: str) -> str:
    if not _has_local_symbol_declaration(text, "Board"):
        return text

    def replace_import(match: re.Match[str]) -> str:
        names = [name.strip() for name in match.group("names").split(",") if name.strip()]
        changed = False
        updated_names: list[str] = []
        for name in names:
            imported = _imported_symbol_name(name)
            if imported == "Board" and " as " not in name:
                changed = True
                if match.group("typeonly"):
                    updated_names.append("Board as BoardState")
                elif name.startswith("type "):
                    updated_names.append("type Board as BoardState")
                else:
                    updated_names.append("type Board as BoardState")
            else:
                updated_names.append(name)
        if not changed:
            return match.group(0)
        import_keyword = "import type" if match.group("typeonly") else "import"
        return f"{import_keyword} {{ {', '.join(updated_names)} }} from '{match.group('source')}'\n"

    updated = REACT_VITE_TYPES_IMPORT_RE.sub(replace_import, text)
    if updated == text:
        return text

    updated = re.sub(r"""(:\s*)Board\b""", r"\1BoardState", updated)
    updated = re.sub(r"""(<\s*)Board(\s*[,>])""", r"\1BoardState\2", updated)
    return updated


def _stabilize_position_type_contract(workdir) -> set[str]:
    src = workdir / "src"
    types_path = src / "types.ts"
    if not src.is_dir() or not types_path.is_file():
        return set()

    source_files = [
        path
        for path in src.rglob("*")
        if path.is_file()
        and path.suffix in {".ts", ".tsx"}
        and ".test." not in path.name.lower()
        and ".spec." not in path.name.lower()
        and path != types_path
    ]
    changed: set[str] = set()
    for path in source_files:
        text = path.read_text(errors="ignore")
        updated = _alias_board_type_import_collision(
            _remove_redundant_position_type_imports(_separate_glued_import_declarations(text))
        )
        if updated != text:
            path.write_text(updated)
            changed.add(path.relative_to(workdir).as_posix())

    position_users = [
        path for path in source_files if _uses_position_without_local_definition(path.read_text(errors="ignore"))
    ]
    if not position_users:
        return changed

    types_text = types_path.read_text(errors="ignore")
    if not REACT_VITE_POSITION_EXPORT_RE.search(types_text):
        types_path.write_text(types_text.rstrip() + "\n\n" + REACT_VITE_POSITION_TYPE)
        changed.add("src/types.ts")

    for path in position_users:
        text = path.read_text(errors="ignore")
        if _types_import_has_symbol(text, "Position"):
            continue

        match = REACT_VITE_TYPES_IMPORT_RE.search(text)
        if match:
            names = [name.strip() for name in match.group("names").split(",") if name.strip()]
            names.append("Position" if match.group("typeonly") else "type Position")
            import_keyword = "import type" if match.group("typeonly") else "import"
            replacement = f"{import_keyword} {{ {', '.join(names)} }} from '{match.group('source')}'\n"
            updated = text[:match.start()] + replacement + text[match.end():]
        else:
            specifier = _types_import_specifier(workdir, path)
            updated = f"import {{ type Position }} from '{specifier}'\n{text}"

        if updated != text:
            path.write_text(updated)
            changed.add(path.relative_to(workdir).as_posix())

    return changed


def _stabilize_react_vite_scaffold(workdir, ticket: dict[str, Any]) -> set[str]:
    if not _looks_like_react_vite_profile(_ticket_delivery_profile(ticket)):
        return set()

    changed: set[str] = set()
    for rel_path, content in {
        "package.json": _react_vite_package_json(workdir),
        "index.html": REACT_VITE_INDEX_HTML,
        "vite.config.ts": REACT_VITE_VITE_CONFIG,
        "tsconfig.json": REACT_VITE_TSCONFIG,
        "tsconfig.node.json": REACT_VITE_TSCONFIG_NODE,
        "src/setupTests.ts": REACT_VITE_SETUP_TESTS,
    }.items():
        written = _write_text_if_changed(workdir, rel_path, content)
        if written:
            changed.add(written)

    if not (workdir / "src" / "main.tsx").is_file():
        written = _write_text_if_changed(workdir, "src/main.tsx", REACT_VITE_MAIN)
        if written:
            changed.add(written)
    if not (workdir / "src" / "index.css").is_file():
        written = _write_text_if_changed(workdir, "src/index.css", REACT_VITE_INDEX_CSS)
        if written:
            changed.add(written)
    if not (workdir / "src" / "App.tsx").is_file():
        written = _write_text_if_changed(workdir, "src/App.tsx", REACT_VITE_APP)
        if written:
            changed.add(written)
    if not _has_js_ts_test_file(workdir):
        written = _write_text_if_changed(workdir, "src/App.test.tsx", REACT_VITE_APP_TEST)
        if written:
            changed.add(written)
    changed.update(_stabilize_react_vite_tests(workdir))
    changed.update(_stabilize_position_type_contract(workdir))
    changed.update(repair_unique_unresolved_relative_imports(workdir))

    return changed


def _write_files(workdir, files: list[dict]) -> list[str]:
    paths: list[str] = []
    for f in files:
        p = workdir / f["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f["content"])
        paths.append(f["path"])
    return paths


def _run_tests(workdir, profile: dict[str, Any] | None = None) -> tuple[bool, str]:
    execution_profile = execution_profile_for_delivery(profile)
    if execution_profile:
        return _run_execution_profile_tests(workdir, execution_profile)

    if (workdir / "Package.swift").exists():
        cmd = ["swift", "test"]
        env = None
    elif (workdir / "go.mod").exists():
        cmd = ["go", "test", "./..."]
        env = None
    elif (workdir / "project.yml").exists():
        return _run_xcodegen_tests(workdir)
    elif (workdir / "package.json").exists():
        return _run_node_tests(workdir)
    else:
        cmd = [sys.executable, "-m", "pytest", "-q"]
        env = os.environ.copy()
        existing = env.get("PYTHONPATH")
        paths = [str(workdir)]
        if existing:
            paths.append(existing)
        env["PYTHONPATH"] = os.pathsep.join(paths)
    result = subprocess.run(
        cmd, cwd=workdir, capture_output=True, text=True, timeout=300, env=env,
    )
    passed = result.returncode == 0
    out = (result.stdout + "\n" + result.stderr)[-4000:]
    return passed, out


def _run_node_tests(workdir) -> tuple[bool, str]:
    return _run_execution_profile_tests(workdir, {
        "install_command": ["npm", "install", "--no-audit", "--fund=false"],
        "test_command": ["npm", "test"],
        "env": {"CI": "true"},
    })


def _run_execution_profile_tests(workdir, execution_profile: dict[str, Any]) -> tuple[bool, str]:
    output = ""
    env = os.environ.copy()
    env.update({str(k): str(v) for k, v in (execution_profile.get("env") or {}).items()})

    for command_key in ("install_command", "pre_test_command"):
        command = execution_profile.get(command_key)
        if not command:
            continue
        result = subprocess.run(
            command,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        output += result.stdout + "\n" + result.stderr
        if result.returncode != 0:
            return False, output[-4000:]

    test_command = execution_profile.get("test_command")
    if not test_command:
        return False, "Delivery execution profile has no test_command."
    tested = subprocess.run(
        test_command,
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    output += tested.stdout + "\n" + tested.stderr
    if tested.returncode != 0 and test_command[:2] == ["xcodebuild", "test"]:
        hint = _xcodegen_failure_hint(workdir, output)
        if hint:
            output += "\n" + hint
    return tested.returncode == 0, output[-4000:]


def _run_xcodegen_tests(workdir) -> tuple[bool, str]:
    scheme = _xcodegen_scheme(workdir)
    output = ""
    try:
        generated = subprocess.run(
            ["xcodegen", "generate"],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError:
        return False, (
            "xcodegen not found. Install XcodeGen or create a Package.swift / "
            ".xcodeproj so code-minions can run Swift tests."
        )
    output += generated.stdout + "\n" + generated.stderr
    if generated.returncode != 0:
        return False, output[-4000:]

    tested = subprocess.run(
        ["xcodebuild", "test", "-scheme", scheme],
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=300,
    )
    output += tested.stdout + "\n" + tested.stderr
    if tested.returncode != 0:
        hint = _xcodegen_failure_hint(workdir, output)
        if hint:
            output += "\n" + hint
    return tested.returncode == 0, output[-4000:]


def _xcodegen_failure_hint(workdir, output: str) -> str:
    if "Multiple commands produce" not in output:
        return ""
    project = workdir / "project.yml"
    if not project.is_file():
        return ""
    text = project.read_text()
    if re.search(r"(?m)^\s{4}PRODUCT_NAME:\s*\S+", text):
        return (
            "XcodeGen diagnostic: xcodebuild reports duplicate Swift module outputs and "
            "root project.yml defines PRODUCT_NAME in top-level settings.base. Move "
            "PRODUCT_NAME into each target with a unique value, or remove it from global "
            "settings so the test target does not build with the app target's module name."
        )
    return (
        "XcodeGen diagnostic: xcodebuild reports duplicate outputs. Check root project.yml "
        "for duplicated source paths, nested project layouts, or test targets compiling app "
        "sources directly instead of depending on the app target."
    )


def _xcodegen_scheme(workdir) -> str:
    text = (workdir / "project.yml").read_text()
    in_schemes = False
    for line in text.splitlines():
        if line.strip() == "schemes:":
            in_schemes = True
            continue
        if not in_schemes:
            continue
        if line.strip() and not line.startswith((" ", "\t")):
            break
        match = re.match(r"\s{2,}([A-Za-z0-9_.-]+):\s*$", line)
        if match:
            return match.group(1)

    name_match = re.search(r"(?m)^name:\s*([A-Za-z0-9_.-]+)\s*$", text)
    if name_match:
        return name_match.group(1)
    return "App"


def _git_diff(workdir) -> str:
    r = subprocess.run(["git", "diff", "HEAD"], cwd=workdir, capture_output=True, text=True)
    return r.stdout


def _execution_profile_for_ticket(ticket: dict[str, Any]) -> dict[str, Any]:
    return execution_profile_for_delivery(_ticket_delivery_profile(ticket))


def _git_commit(workdir, msg: str, ignored_paths: list[str] | None = None) -> str:
    subprocess.run(["git", "add", "-A"], cwd=workdir, check=True)
    if ignored_paths:
        subprocess.run(["git", "reset", "--", *ignored_paths], cwd=workdir, check=True)
    subprocess.run(["git", "commit", "-m", msg, "--allow-empty"], cwd=workdir, capture_output=True, text=True)
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=workdir, capture_output=True, text=True)
    return r.stdout.strip()


def run(ctx):
    ticket = ctx.inputs["ticket"]
    workdir = ctx.workdir

    policies = _policies(ctx)
    self_heal_max = int(policies.get("self_heal_max_rounds", 3))
    reviewer_max = int(policies.get("reviewer_max_rounds", 3))
    agent_profile = _agent_profile_for_ticket(ticket, policies)

    reviewer_feedback: str = ""
    all_paths: set[str] = set()
    test_output: str = ""
    review: dict[str, Any] = {}
    latest_gate_findings: list[GateFinding] = []
    all_paths.update(_stabilize_react_vite_scaffold(workdir, ticket))

    reviewer_loops = max(1, reviewer_max)
    for reviewer_round in range(1, reviewer_loops + 1):
        context_package = build_implementation_context(
            workdir=workdir,
            ticket=ticket,
            delivery_profile=_ticket_delivery_profile(ticket),
            agent_profile=agent_profile,
            gate_findings=[],
        )
        coder_user = (
            f"{context_package.render()}\n\n"
            f"Delivery profile compact:\n{_delivery_profile_context(ticket)}\n\n"
            f"Project context:\n{_project_context(workdir)}\n\n"
            f"Delivery guidance:\n{_delivery_guidance_context(ticket)}\n\n"
            f"Previous reviewer feedback (empty on first round):\n{reviewer_feedback}"
        )
        plan = _llm_call(ctx, CODER_SYS, coder_user)
        paths = _write_files(workdir, plan.get("files_written", []))
        all_paths.update(paths)
        all_paths.update(_stabilize_react_vite_scaffold(workdir, ticket))

        passed, test_output = False, ""
        for heal_round in range(self_heal_max + 1):
            passed, delivery_output, gate_findings = _run_delivery_profile_gate(workdir, ticket)
            if passed:
                passed, test_output = _run_tests(workdir, _ticket_delivery_profile(ticket))
                if delivery_output and delivery_output != "Delivery profile check passed.":
                    test_output = f"{delivery_output}\n\n{test_output}" if test_output else delivery_output
                if not passed:
                    gate_findings.extend(_runtime_gate_findings(test_output, ticket))
            else:
                test_output = delivery_output
            latest_gate_findings = gate_findings
            _record_gate_findings(ctx, gate_findings)
            if passed:
                break
            if heal_round >= self_heal_max:
                break
            repair_context = build_implementation_context(
                workdir=workdir,
                ticket=ticket,
                delivery_profile=_ticket_delivery_profile(ticket),
                agent_profile=agent_profile,
                gate_findings=gate_findings,
            )
            plan = _llm_call(
                ctx, CODER_SYS,
                f"{repair_context.render()}\n\n"
                f"Tests failed. Output:\n{test_output}\n\n"
                f"{_failure_playbook_context(test_output)}\n\n"
                "Fix the implementation or tests. "
                f"Delivery profile:\n{_delivery_profile_context(ticket)}\n\n"
                f"Delivery guidance:\n{_delivery_guidance_context(ticket)}\n\n"
                f"Current build/test configuration:\n{_build_file_context(workdir)}\n\n"
                f"Ticket: {json.dumps(ticket)}",
            )
            paths = _write_files(workdir, plan.get("files_written", []))
            all_paths.update(paths)
            all_paths.update(_stabilize_react_vite_scaffold(workdir, ticket))

        if not passed:
            output = {
                "files_changed": sorted(all_paths),
                "test_result": {"passed": False, "output": test_output},
                "commit_sha": "",
                "review_report": {"approved": False, "issues": [], "summary": "aborted: tests never green"},
                "rounds_used": reviewer_round,
                "agent_profile": agent_profile.to_dict(),
                "gate_findings": findings_to_dicts(latest_gate_findings),
            }
            raise SkillExecutionError("tests never green", output=output)

        if reviewer_max <= 0:
            sha = _git_commit(
                workdir,
                f"feat({ticket.get('id','t')}): {ticket.get('title','')}",
                ignored_paths=_execution_profile_for_ticket(ticket).get("ignored_paths"),
            )
            return {
                "files_changed": sorted(all_paths),
                "test_result": {"passed": True, "output": test_output},
                "commit_sha": sha,
                "review_report": {"approved": True, "issues": [], "summary": "review skipped"},
                "rounds_used": reviewer_round,
                "agent_profile": agent_profile.to_dict(),
                "gate_findings": findings_to_dicts(latest_gate_findings),
            }

        diff = _git_diff(workdir)
        review = ctx.invoke_skill("ai-code-review", {
            "diff": diff, "ticket": ticket, "files_changed": sorted(all_paths),
        })

        blockers = [i for i in review.get("issues", []) if i.get("severity") in ("blocker", "major")]
        if not blockers:
            sha = _git_commit(
                workdir,
                f"feat({ticket.get('id','t')}): {ticket.get('title','')}",
                ignored_paths=_execution_profile_for_ticket(ticket).get("ignored_paths"),
            )
            return {
                "files_changed": sorted(all_paths),
                "test_result": {"passed": True, "output": test_output},
                "commit_sha": sha,
                "review_report": review,
                "rounds_used": reviewer_round,
                "agent_profile": agent_profile.to_dict(),
                "gate_findings": findings_to_dicts(latest_gate_findings),
            }

        reviewer_feedback = "\n".join(
            f"[{i['severity']}] {i.get('file','?')}:{i.get('line','?')}: {i['description']}"
            for i in blockers
        )

    sha = _git_commit(
        workdir,
        f"wip({ticket.get('id','t')}): {ticket.get('title','')} -- review unresolved",
        ignored_paths=_execution_profile_for_ticket(ticket).get("ignored_paths"),
    )
    return {
        "files_changed": sorted(all_paths),
        "test_result": {"passed": True, "output": test_output},
        "commit_sha": sha,
        "review_report": review,
        "rounds_used": reviewer_max,
        "agent_profile": agent_profile.to_dict(),
        "gate_findings": findings_to_dicts(latest_gate_findings),
    }
