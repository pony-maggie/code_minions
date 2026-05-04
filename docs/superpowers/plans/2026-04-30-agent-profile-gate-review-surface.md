# Agent Profile Gate Review Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a backward-compatible first phase of Warp-inspired agent profiles, structured implementation context, canonical gate findings, and visible CLI/Web review surface.

**Architecture:** Keep the existing workflow DAG, delivery profile validators, failure playbook, run events, and `implement-with-tdd` skill. Add small focused modules for profile resolution, gate finding normalization, and implementation context assembly, then adapt the implementation skill and status views to consume those structures.

**Tech Stack:** Python 3.11, Typer/Rich CLI, FastAPI/Jinja Web dashboard, pytest, existing code_minions skill runtime.

---

## File Structure

- Create `src/code_minions/agent_profiles.py`
  - Owns built-in role/stack profile resolution.
  - Exposes `AgentProfile`, `resolve_agent_profile()`, and JSON helpers.
- Create `src/code_minions/gates.py`
  - Owns canonical `GateFinding` objects and adapters.
  - Converts current delivery profile issues and failure playbook hints into structured findings.
- Create `src/code_minions/implementation_context.py`
  - Owns assembly/rendering of implementation prompt context.
  - Keeps prompt data testable before it becomes text.
- Modify `src/code_minions/failure_playbook.py`
  - Keep `failure_hints_for_output()` for compatibility.
  - Add structured runtime finding function that delegates to `gates.py`.
- Modify `src/code_minions/builtin/skills/implement-with-tdd/scripts/run.py`
  - Resolve implementer profile.
  - Build and render implementation context package.
  - Convert delivery/runtime failures into gate findings.
  - Emit findings in step output and run events.
- Modify `src/code_minions/cli/main.py`
  - Print compact grouped findings after step errors/outputs.
- Modify Web templates:
  - `src/code_minions/web/templates/run_detail.html`
  - Add `src/code_minions/web/templates/partials/gate_findings.html`
  - Render findings from step output JSON.
- Add/modify tests:
  - `tests/unit/test_agent_profiles.py`
  - `tests/unit/test_gates.py`
  - `tests/unit/test_implementation_context.py`
  - `tests/unit/test_implement_with_tdd_handler.py`
  - `tests/unit/test_cli.py`
  - `tests/unit/test_web_routes.py`

---

### Task 1: Built-In Agent Profiles

**Files:**
- Create: `src/code_minions/agent_profiles.py`
- Test: `tests/unit/test_agent_profiles.py`

- [ ] **Step 1: Write failing tests for default and stack-specific profile resolution**

Create `tests/unit/test_agent_profiles.py`:

```python
from code_minions.agent_profiles import resolve_agent_profile


def test_default_implementer_profile_has_safe_loop_defaults() -> None:
    profile = resolve_agent_profile(role="implementer", delivery_profile={})

    assert profile.profile_id == "default/implementer"
    assert profile.role == "implementer"
    assert profile.self_heal_max_rounds == 3
    assert profile.reviewer_max_rounds == 0
    assert profile.gate_strictness == "balanced"
    assert profile.temperature == 0.2


def test_react_vite_implementer_profile_inherits_delivery_strictness() -> None:
    profile = resolve_agent_profile(
        role="implementer",
        delivery_profile={
            "stack_id": "react-vite",
            "gate_strictness": "relaxed",
        },
    )

    assert profile.profile_id == "react-vite/implementer"
    assert profile.stack_id == "react-vite"
    assert profile.gate_strictness == "relaxed"
    assert "React/Vite" in "\n".join(profile.guidance)


def test_unknown_requested_profile_falls_back_with_warning() -> None:
    profile = resolve_agent_profile(
        role="implementer",
        delivery_profile={"stack_id": "react-vite"},
        requested_profile_id="missing/profile",
    )

    assert profile.profile_id == "react-vite/implementer"
    assert profile.warnings == [
        "Unknown agent profile `missing/profile`; using `react-vite/implementer`."
    ]
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
pytest tests/unit/test_agent_profiles.py -q
```

Expected: fail with `ModuleNotFoundError: No module named 'code_minions.agent_profiles'`.

- [ ] **Step 3: Implement the profile module**

Create `src/code_minions/agent_profiles.py`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from code_minions.stacks import stack_id_for_delivery

VALID_GATE_STRICTNESS = {"relaxed", "balanced", "strict"}


@dataclass(frozen=True)
class AgentProfile:
    profile_id: str
    role: str
    stack_id: str
    temperature: float = 0.2
    max_tokens: int = 16000
    self_heal_max_rounds: int = 3
    reviewer_max_rounds: int = 0
    gate_strictness: str = "balanced"
    guidance: tuple[str, ...] = ()
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["guidance"] = list(self.guidance)
        return data


def _normalized_strictness(value: Any) -> str:
    strictness = str(value or "balanced").strip().lower()
    if strictness in VALID_GATE_STRICTNESS:
        return strictness
    return "balanced"


def _default_profile(role: str, strictness: str) -> AgentProfile:
    return AgentProfile(
        profile_id=f"default/{role}",
        role=role,
        stack_id="",
        gate_strictness=strictness,
    )


def _react_vite_profile(role: str, strictness: str) -> AgentProfile:
    return AgentProfile(
        profile_id=f"react-vite/{role}",
        role=role,
        stack_id="react-vite",
        gate_strictness=strictness,
        guidance=(
            "React/Vite implementers must preserve root project layout, Vitest jsdom setup, "
            "consistent TypeScript contracts, and stable Testing Library selectors.",
        ),
    )


def resolve_agent_profile(
    *,
    role: str,
    delivery_profile: dict[str, Any] | None,
    requested_profile_id: str | None = None,
) -> AgentProfile:
    profile = delivery_profile or {}
    stack_id = stack_id_for_delivery(profile)
    strictness = _normalized_strictness(profile.get("gate_strictness"))

    resolved = (
        _react_vite_profile(role, strictness)
        if stack_id == "react-vite"
        else _default_profile(role, strictness)
    )

    if requested_profile_id and requested_profile_id != resolved.profile_id:
        return AgentProfile(
            **{
                **resolved.to_dict(),
                "warnings": [
                    f"Unknown agent profile `{requested_profile_id}`; using `{resolved.profile_id}`."
                ],
            }
        )
    return resolved
```

- [ ] **Step 4: Run profile tests**

Run:

```bash
pytest tests/unit/test_agent_profiles.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/code_minions/agent_profiles.py tests/unit/test_agent_profiles.py
git commit -m "feat: add agent profile resolution"
```

---

### Task 2: Canonical Gate Findings

**Files:**
- Create: `src/code_minions/gates.py`
- Modify: `src/code_minions/failure_playbook.py`
- Test: `tests/unit/test_gates.py`
- Test: `tests/unit/test_failure_playbook.py`

- [ ] **Step 1: Write failing tests for gate finding adapters**

Create `tests/unit/test_gates.py`:

```python
from code_minions.gates import (
    GateFinding,
    delivery_issues_to_findings,
    findings_to_text,
    runtime_findings_for_output,
)


def test_delivery_issues_convert_to_preflight_findings() -> None:
    findings = delivery_issues_to_findings(
        [
            {
                "code": "missing-required-file",
                "severity": "error",
                "message": "Delivery profile requires `package.json`.",
            }
        ],
        source="react-vite",
    )

    assert findings == [
        GateFinding(
            code="missing-required-file",
            severity="error",
            stage="preflight",
            message="Delivery profile requires `package.json`.",
            repair_hint="",
            source="react-vite",
            paths=[],
        )
    ]


def test_runtime_findings_include_failure_playbook_hint() -> None:
    findings = runtime_findings_for_output(
        "ReferenceError: document is not defined",
        source="react-vite",
    )

    assert findings[0].stage == "runtime"
    assert findings[0].severity == "error"
    assert findings[0].code == "referenceerror-document-is-not-defined"
    assert "jsdom" in findings[0].repair_hint.lower()


def test_findings_to_text_groups_by_stage_and_severity() -> None:
    text = findings_to_text([
        GateFinding(
            code="missing-test-file",
            severity="warning",
            stage="preflight",
            message="No test file found.",
            repair_hint="Add a test.",
            source="react-vite",
            paths=[],
        )
    ])

    assert "Gate findings:" in text
    assert "- warning preflight/missing-test-file: No test file found." in text
    assert "repair: Add a test." in text
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
pytest tests/unit/test_gates.py -q
```

Expected: fail because `code_minions.gates` does not exist.

- [ ] **Step 3: Implement gate findings**

Create `src/code_minions/gates.py`:

```python
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class GateFinding:
    code: str
    severity: str
    stage: str
    message: str
    repair_hint: str = ""
    source: str = ""
    paths: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["paths"] = list(self.paths or [])
        return data


def finding_from_dict(data: dict[str, Any]) -> GateFinding:
    return GateFinding(
        code=str(data.get("code") or "unknown"),
        severity=str(data.get("severity") or "error"),
        stage=str(data.get("stage") or "runtime"),
        message=str(data.get("message") or ""),
        repair_hint=str(data.get("repair_hint") or ""),
        source=str(data.get("source") or ""),
        paths=[str(path) for path in data.get("paths") or []],
    )


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or "runtime-failure"


def delivery_issues_to_findings(
    issues: list[dict[str, str]],
    *,
    source: str,
) -> list[GateFinding]:
    findings: list[GateFinding] = []
    for issue in issues:
        findings.append(GateFinding(
            code=str(issue.get("code") or "delivery-profile-issue"),
            severity=str(issue.get("severity") or "error"),
            stage="preflight",
            message=str(issue.get("message") or ""),
            repair_hint=str(issue.get("repair_hint") or ""),
            source=source,
            paths=[],
        ))
    return findings


def runtime_findings_for_output(output: str, *, source: str) -> list[GateFinding]:
    from code_minions.failure_playbook import failure_hints_for_output

    findings: list[GateFinding] = []
    for hint in failure_hints_for_output(output):
        code = _slug(hint.split(".", 1)[0])
        findings.append(GateFinding(
            code=code,
            severity="error",
            stage="runtime",
            message="Runtime failure matched the failure playbook.",
            repair_hint=hint,
            source=source,
            paths=[],
        ))
    return findings


def findings_to_text(findings: list[GateFinding]) -> str:
    if not findings:
        return ""
    lines = ["Gate findings:"]
    for finding in findings:
        lines.append(
            f"- {finding.severity} {finding.stage}/{finding.code}: {finding.message}"
        )
        if finding.repair_hint:
            lines.append(f"  repair: {finding.repair_hint}")
    return "\n".join(lines)


def findings_to_dicts(findings: list[GateFinding]) -> list[dict[str, Any]]:
    return [finding.to_dict() for finding in findings]
```

- [ ] **Step 4: Add failure playbook structured compatibility wrapper**

Append to `src/code_minions/failure_playbook.py`:

```python
from typing import Any


def failure_findings_for_output(output: str, *, source: str = "") -> list[dict[str, Any]]:
    from code_minions.gates import findings_to_dicts, runtime_findings_for_output

    return findings_to_dicts(runtime_findings_for_output(output, source=source))
```

- [ ] **Step 5: Add compatibility test**

Add to `tests/unit/test_failure_playbook.py`:

```python
from code_minions.failure_playbook import failure_findings_for_output


def test_failure_findings_for_output_preserves_structured_runtime_hint() -> None:
    findings = failure_findings_for_output(
        "ReferenceError: describe is not defined",
        source="react-vite",
    )

    assert findings[0]["stage"] == "runtime"
    assert findings[0]["severity"] == "error"
    assert findings[0]["source"] == "react-vite"
    assert "Vitest" in findings[0]["repair_hint"]
```

- [ ] **Step 6: Run targeted tests**

Run:

```bash
pytest tests/unit/test_gates.py tests/unit/test_failure_playbook.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/code_minions/gates.py src/code_minions/failure_playbook.py tests/unit/test_gates.py tests/unit/test_failure_playbook.py
git commit -m "feat: normalize gate findings"
```

---

### Task 3: Structured Implementation Context Package

**Files:**
- Create: `src/code_minions/implementation_context.py`
- Modify: `src/code_minions/builtin/skills/implement-with-tdd/scripts/run.py`
- Test: `tests/unit/test_implementation_context.py`
- Test: `tests/unit/test_implement_with_tdd_handler.py`

- [ ] **Step 1: Write tests for context package assembly and rendering**

Create `tests/unit/test_implementation_context.py`:

```python
from pathlib import Path

from code_minions.agent_profiles import resolve_agent_profile
from code_minions.gates import GateFinding
from code_minions.implementation_context import build_implementation_context


def test_context_package_includes_profile_stack_agents_and_ticket(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Use strict TypeScript.")
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}')
    ticket = {
        "id": "task-1",
        "title": "Board",
        "description": "Render a board.",
        "acceptance_criteria": ["shows board"],
        "delivery_profile": {"stack_id": "react-vite", "gate_strictness": "relaxed"},
    }
    profile = resolve_agent_profile(
        role="implementer",
        delivery_profile=ticket["delivery_profile"],
    )

    package = build_implementation_context(
        workdir=tmp_path,
        ticket=ticket,
        delivery_profile=ticket["delivery_profile"],
        agent_profile=profile,
        gate_findings=[
            GateFinding(
                code="missing-test-file",
                severity="warning",
                stage="preflight",
                message="No test file found.",
                repair_hint="Add a test.",
                source="react-vite",
                paths=[],
            )
        ],
    )

    rendered = package.render()
    assert package.stack_id == "react-vite"
    assert "task-1" in rendered
    assert "Use strict TypeScript." in rendered
    assert "react-vite/implementer" in rendered
    assert "missing-test-file" in rendered
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
pytest tests/unit/test_implementation_context.py -q
```

Expected: fail because `code_minions.implementation_context` does not exist.

- [ ] **Step 3: Implement context package**

Create `src/code_minions/implementation_context.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_minions.agent_profiles import AgentProfile
from code_minions.gates import GateFinding, findings_to_text
from code_minions.stacks import stack_id_for_delivery


@dataclass(frozen=True)
class ImplementationContextPackage:
    ticket: dict[str, Any]
    delivery_profile: dict[str, Any]
    agent_profile: AgentProfile
    stack_id: str
    project_markers: list[str]
    agents_md: str
    build_config: str
    gate_findings: list[GateFinding]

    def render(self) -> str:
        return "\n\n".join([
            f"Agent profile:\n{json.dumps(self.agent_profile.to_dict(), ensure_ascii=False, indent=2)}",
            f"Delivery profile:\n{json.dumps(self.delivery_profile, ensure_ascii=False, indent=2, sort_keys=True)}",
            f"Ticket:\n{json.dumps(self.ticket, ensure_ascii=False, indent=2)}",
            f"Project markers:\n{json.dumps(self.project_markers, ensure_ascii=False)}",
            f"AGENTS.md excerpt:\n{self.agents_md}",
            f"Authoritative build/test configuration:\n{self.build_config}",
            findings_to_text(self.gate_findings) or "Gate findings: none",
        ])


def _read_optional(path: Path, limit: int = 4000) -> str:
    if not path.is_file():
        return ""
    return path.read_text(errors="ignore")[:limit]


def _build_config(workdir: Path) -> str:
    parts: list[str] = []
    for name in ("package.json", "vite.config.ts", "vitest.config.ts", "project.yml", "pyproject.toml"):
        path = workdir / name
        if path.is_file():
            parts.append(f"--- {name} ---\n{_read_optional(path)}")
    return "\n\n".join(parts) or "No recognized build/test configuration files found."


def build_implementation_context(
    *,
    workdir: Path,
    ticket: dict[str, Any],
    delivery_profile: dict[str, Any],
    agent_profile: AgentProfile,
    gate_findings: list[GateFinding] | None = None,
) -> ImplementationContextPackage:
    markers = [
        name
        for name in ("AGENTS.md", "package.json", "vite.config.ts", "vitest.config.ts", "project.yml", "pyproject.toml")
        if (workdir / name).exists()
    ]
    return ImplementationContextPackage(
        ticket=ticket,
        delivery_profile=delivery_profile,
        agent_profile=agent_profile,
        stack_id=stack_id_for_delivery(delivery_profile),
        project_markers=markers,
        agents_md=_read_optional(workdir / "AGENTS.md"),
        build_config=_build_config(workdir),
        gate_findings=gate_findings or [],
    )
```

- [ ] **Step 4: Add import smoke coverage in implement handler tests**

Add to `tests/unit/test_implement_with_tdd_handler.py`:

```python
def test_coder_prompt_uses_structured_implementation_context(tmp_git_repo: Path, monkeypatch):
    from code_minions.builtin.skills.implement_with_tdd.scripts import run as entrypoint

    calls: list[str] = []

    def fake_llm_call(ctx, system, user, **kwargs):
        calls.append(user)
        return {"files_written": [{"path": "src/App.tsx", "content": "export default function App(){return null}"}]}

    monkeypatch.setattr(entrypoint, "_llm_call", fake_llm_call)
    monkeypatch.setattr(entrypoint, "_run_tests", lambda workdir, profile: (True, "ok"))
    monkeypatch.setattr(entrypoint, "_git_commit", lambda workdir, msg, ignored_paths=None: "abc123")

    ctx = DummyCtx(tmp_git_repo)
    ctx.inputs = {
        "ticket": {
            "id": "task-1",
            "title": "Board",
            "delivery_profile": {"stack_id": "react-vite"},
        }
    }
    ctx.skill.meta.policies = {"reviewer_max_rounds": 0}

    entrypoint.run(ctx)

    assert "Agent profile:" in calls[0]
    assert "Delivery profile:" in calls[0]
    assert "react-vite/implementer" in calls[0]
```

If `DummyCtx` already differs in the file, adapt this snippet to the existing helper rather than creating a second incompatible helper.

- [ ] **Step 5: Run targeted tests**

Run:

```bash
pytest tests/unit/test_implementation_context.py tests/unit/test_implement_with_tdd_handler.py -q
```

Expected: implementation context tests pass, handler test fails until Task 4 wires the new package into the handler. If the handler test is too coupled for this task, mark it xfail locally only until Task 4 and remove xfail in Task 4.

- [ ] **Step 6: Commit context module and passing tests**

```bash
git add src/code_minions/implementation_context.py tests/unit/test_implementation_context.py
git commit -m "feat: add implementation context package"
```

---

### Task 4: Wire Profiles And Gates Into implement-with-tdd

**Files:**
- Modify: `src/code_minions/builtin/skills/implement-with-tdd/scripts/run.py`
- Test: `tests/unit/test_implement_with_tdd_handler.py`

- [ ] **Step 1: Write failing tests for output findings and repair prompt findings**

Add to `tests/unit/test_implement_with_tdd_handler.py`:

```python
def test_delivery_profile_failure_outputs_gate_findings(tmp_git_repo: Path, monkeypatch):
    from code_minions.builtin.skills.implement_with_tdd.scripts import run as entrypoint
    from code_minions.engine.skill_runtime import SkillExecutionError

    ctx = DummyCtx(tmp_git_repo)
    ctx.inputs = {
        "ticket": {
            "id": "task-1",
            "title": "Board",
            "delivery_profile": {
                "stack_id": "react-vite",
                "required_files": ["package.json"],
            },
        }
    }
    ctx.skill.meta.policies = {"self_heal_max_rounds": 0, "reviewer_max_rounds": 0}
    monkeypatch.setattr(entrypoint, "_llm_call", lambda *args, **kwargs: {"files_written": []})

    try:
        entrypoint.run(ctx)
    except SkillExecutionError as exc:
        output = exc.output
    else:
        raise AssertionError("expected SkillExecutionError")

    assert output["agent_profile"]["profile_id"] == "react-vite/implementer"
    assert output["gate_findings"][0]["code"] == "missing-required-file"
    assert output["gate_findings"][0]["stage"] == "preflight"


def test_runtime_failure_findings_are_sent_to_repair_prompt(tmp_git_repo: Path, monkeypatch):
    from code_minions.builtin.skills.implement_with_tdd.scripts import run as entrypoint

    calls: list[str] = []

    def fake_llm_call(ctx, system, user, **kwargs):
        calls.append(user)
        return {"files_written": [{"path": "package.json", "content": '{"scripts":{"test":"vitest run"}}'}]}

    attempts = iter([
        (False, "ReferenceError: document is not defined"),
        (True, "ok"),
    ])

    monkeypatch.setattr(entrypoint, "_llm_call", fake_llm_call)
    monkeypatch.setattr(entrypoint, "_run_delivery_profile_check", lambda workdir, ticket: (True, "Delivery profile check passed."))
    monkeypatch.setattr(entrypoint, "_run_tests", lambda workdir, profile: next(attempts))
    monkeypatch.setattr(entrypoint, "_git_commit", lambda workdir, msg, ignored_paths=None: "abc123")

    ctx = DummyCtx(tmp_git_repo)
    ctx.inputs = {
        "ticket": {
            "id": "task-1",
            "title": "Board",
            "delivery_profile": {"stack_id": "react-vite"},
        }
    }
    ctx.skill.meta.policies = {"self_heal_max_rounds": 1, "reviewer_max_rounds": 0}

    output = entrypoint.run(ctx)

    assert output["test_result"]["passed"] is True
    assert "Gate findings:" in calls[1]
    assert "jsdom" in calls[1].lower()
```

- [ ] **Step 2: Run targeted tests and verify failure**

Run:

```bash
pytest tests/unit/test_implement_with_tdd_handler.py -q
```

Expected: new tests fail because `implement-with-tdd` does not yet output `agent_profile` or `gate_findings`.

- [ ] **Step 3: Add helpers to implement-with-tdd**

Modify `src/code_minions/builtin/skills/implement-with-tdd/scripts/run.py` imports:

```python
from code_minions.agent_profiles import resolve_agent_profile
from code_minions.gates import (
    GateFinding,
    delivery_issues_to_findings,
    findings_to_dicts,
    findings_to_text,
    runtime_findings_for_output,
)
from code_minions.implementation_context import build_implementation_context
from code_minions.stacks import stack_id_for_delivery
```

Add helpers near `_ticket_delivery_profile`:

```python
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


def _run_delivery_profile_gate(workdir, ticket: dict[str, Any]) -> tuple[bool, str, list[GateFinding]]:
    profile = _ticket_delivery_profile(ticket)
    if not profile:
        return True, "", []
    issues = validate_delivery_profile(workdir, profile)
    findings = delivery_issues_to_findings(issues, source=_source_for_profile(profile))
    errors = [finding for finding in findings if finding.severity == "error"]
    text = findings_to_text(findings)
    if not findings:
        text = "Delivery profile check passed."
    return not errors, text, findings


def _runtime_gate_findings(output: str, ticket: dict[str, Any]) -> list[GateFinding]:
    profile = _ticket_delivery_profile(ticket)
    return runtime_findings_for_output(output, source=_source_for_profile(profile))


def _record_gate_findings(ctx, findings: list[GateFinding]) -> None:
    if not findings:
        return
    recorder = (getattr(ctx, "extras", {}) or {}).get("run_event_recorder")
    step_id = (getattr(ctx, "extras", {}) or {}).get("current_step_id")
    if recorder:
        recorder("gate.findings", {
            "step_id": step_id,
            "findings": findings_to_dicts(findings),
        })
```

- [ ] **Step 4: Render initial coder prompt from context package**

In `run(ctx)`, after `policies = _policies(ctx)` add:

```python
    agent_profile = _agent_profile_for_ticket(ticket, policies)
```

Replace the initial `coder_user = (...)` block with:

```python
        context_package = build_implementation_context(
            workdir=workdir,
            ticket=ticket,
            delivery_profile=_ticket_delivery_profile(ticket),
            agent_profile=agent_profile,
            gate_findings=[],
        )
        coder_user = (
            f"{context_package.render()}\n\n"
            f"Delivery guidance:\n{_delivery_guidance_context(ticket)}\n\n"
            f"Previous reviewer feedback (empty on first round):\n{reviewer_feedback}"
        )
```

- [ ] **Step 5: Use gate findings in self-heal loop**

Replace:

```python
            passed, delivery_output = _run_delivery_profile_check(workdir, ticket)
```

with:

```python
            passed, delivery_output, gate_findings = _run_delivery_profile_gate(workdir, ticket)
```

After `_run_tests(...)`, classify runtime findings on failure:

```python
                if not passed:
                    gate_findings.extend(_runtime_gate_findings(test_output, ticket))
```

Before repair `_llm_call`, build context with findings:

```python
            _record_gate_findings(ctx, gate_findings)
            repair_context = build_implementation_context(
                workdir=workdir,
                ticket=ticket,
                delivery_profile=_ticket_delivery_profile(ticket),
                agent_profile=agent_profile,
                gate_findings=gate_findings,
            )
```

Then include:

```python
                f"{repair_context.render()}\n\n"
                f"Tests failed. Output:\n{test_output}\n\n"
```

Remove or leave `_failure_playbook_context(test_output)` only as a compatibility fallback. If it remains, make sure it does not duplicate `findings_to_text`.

- [ ] **Step 6: Include findings and profile in outputs**

In every returned or raised output dict from `run(ctx)`, add:

```python
"agent_profile": agent_profile.to_dict(),
"gate_findings": findings_to_dicts(gate_findings if "gate_findings" in locals() else []),
```

For success after a repaired runtime failure, include the most recent findings even though `test_result.passed` is true. This keeps the review surface useful.

- [ ] **Step 7: Run targeted tests**

Run:

```bash
pytest tests/unit/test_implement_with_tdd_handler.py tests/unit/test_agent_profiles.py tests/unit/test_gates.py tests/unit/test_implementation_context.py -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/code_minions/builtin/skills/implement-with-tdd/scripts/run.py tests/unit/test_implement_with_tdd_handler.py
git commit -m "feat: surface gate findings in implementation loop"
```

---

### Task 5: CLI Status Review Surface

**Files:**
- Modify: `src/code_minions/cli/main.py`
- Test: `tests/unit/test_cli.py`

- [ ] **Step 1: Write failing CLI output test**

Add to `tests/unit/test_cli.py`:

```python
def test_status_prints_gate_findings(tmp_path: Path, runner: CliRunner) -> None:
    from code_minions.store.run_store import RunStore
    from code_minions.types import RunStatus, StepStatus

    store = RunStore(tmp_path / ".devflow" / "runs.db")
    run_id = store.create_run("react-vite-prd-to-commit", {}, llm="minimax/MiniMax-M2.7")
    store.upsert_step(
        run_id,
        "implement[0]",
        StepStatus.FAILED,
        output={
            "agent_profile": {"profile_id": "react-vite/implementer"},
            "gate_findings": [
                {
                    "code": "missing-test-file",
                    "severity": "warning",
                    "stage": "preflight",
                    "message": "No test file found.",
                    "repair_hint": "Add a real Vitest test.",
                    "source": "react-vite",
                    "paths": [],
                }
            ],
        },
        error="tests never green",
    )
    store.set_run_status(run_id, RunStatus.FAILED)

    result = runner.invoke(app, ["status", run_id, "--project-root", str(tmp_path)])

    assert result.exit_code == 0
    assert "findings:" in result.output
    assert "react-vite/implementer" in result.output
    assert "preflight/missing-test-file" in result.output
    assert "Add a real Vitest test." in result.output
```

Adapt imports to the existing `test_cli.py` fixtures. If the test suite uses a different runner fixture name, reuse the existing one.

- [ ] **Step 2: Run and verify failure**

Run:

```bash
pytest tests/unit/test_cli.py::test_status_prints_gate_findings -q
```

Expected: fail because `status` does not print findings.

- [ ] **Step 3: Add CLI finding extraction and printer**

In `src/code_minions/cli/main.py`, add helpers near `_print_step_outputs`:

```python
def _step_output(step: dict[str, Any]) -> dict[str, Any]:
    raw = step.get("output_json")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _print_step_findings(steps: list[dict[str, Any]]) -> None:
    rows: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for step in steps:
        output = _step_output(step)
        for finding in output.get("gate_findings") or []:
            if isinstance(finding, dict):
                rows.append((step["step_id"], output.get("agent_profile") or {}, finding))
    if not rows:
        return

    typer.echo("findings:")
    for step_id, agent_profile, finding in rows:
        profile_id = agent_profile.get("profile_id") or "unknown-profile"
        typer.echo(
            f"{step_id} [{profile_id}] "
            f"{finding.get('severity', 'error')} "
            f"{finding.get('stage', 'runtime')}/{finding.get('code', 'unknown')}: "
            f"{finding.get('message', '')}"
        )
        repair = finding.get("repair_hint")
        if repair:
            typer.echo(f"  repair: {repair}")
```

In `status(...)`, after printing errors and before `_print_step_outputs(...)`, call:

```python
    _print_step_findings(state["steps"])
```

- [ ] **Step 4: Run CLI tests**

Run:

```bash
pytest tests/unit/test_cli.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/code_minions/cli/main.py tests/unit/test_cli.py
git commit -m "feat: show gate findings in status"
```

---

### Task 6: Web Run Detail Findings Surface

**Files:**
- Modify: `src/code_minions/web/routes/runs.py`
- Modify: `src/code_minions/web/templates/run_detail.html`
- Create: `src/code_minions/web/templates/partials/gate_findings.html`
- Test: `tests/unit/test_web_routes.py`

- [ ] **Step 1: Write failing Web route test**

Add to `tests/unit/test_web_routes.py`:

```python
def test_run_detail_shows_gate_findings(client, tmp_path: Path) -> None:
    from code_minions.store.run_store import RunStore
    from code_minions.types import RunStatus, StepStatus

    store = RunStore(tmp_path / ".devflow" / "runs.db")
    run_id = store.create_run("react-vite-prd-to-commit", {}, llm="minimax/MiniMax-M2.7")
    store.upsert_step(
        run_id,
        "implement[0]",
        StepStatus.FAILED,
        output={
            "agent_profile": {"profile_id": "react-vite/implementer"},
            "gate_findings": [
                {
                    "code": "missing-postcss-plugin-dependency",
                    "severity": "error",
                    "stage": "preflight",
                    "message": "PostCSS config references tailwindcss.",
                    "repair_hint": "Add tailwindcss or remove PostCSS config.",
                    "source": "react-vite",
                    "paths": ["postcss.config.js"],
                }
            ],
        },
        error="tests never green",
    )
    store.set_run_status(run_id, RunStatus.FAILED)

    resp = client.get(f"/runs/{run_id}")

    assert resp.status_code == 200
    assert "Findings" in resp.text
    assert "react-vite/implementer" in resp.text
    assert "missing-postcss-plugin-dependency" in resp.text
    assert "Add tailwindcss or remove PostCSS config." in resp.text
```

Adapt store/client setup to match the existing Web route fixtures.

- [ ] **Step 2: Run and verify failure**

Run:

```bash
pytest tests/unit/test_web_routes.py::test_run_detail_shows_gate_findings -q
```

Expected: fail because Web detail does not render findings.

- [ ] **Step 3: Add route extraction helper**

In `src/code_minions/web/routes/runs.py`, add:

```python
import json


def _step_findings(steps: list[dict]) -> list[dict]:
    findings: list[dict] = []
    for step in steps:
        raw = step.get("output_json")
        if not raw:
            continue
        try:
            output = json.loads(raw)
        except Exception:
            continue
        if not isinstance(output, dict):
            continue
        profile = output.get("agent_profile") or {}
        for finding in output.get("gate_findings") or []:
            if isinstance(finding, dict):
                findings.append({
                    "step_id": step["step_id"],
                    "profile_id": profile.get("profile_id") or "unknown-profile",
                    **finding,
                })
    return findings
```

In the run-detail route template context, pass:

```python
"gate_findings": _step_findings(steps),
```

- [ ] **Step 4: Add partial and include in run detail**

Create `src/code_minions/web/templates/partials/gate_findings.html`:

```html
{% if gate_findings %}
<h2 class="text-lg font-semibold mt-6 mb-3">Findings</h2>
<div class="bg-white rounded-lg shadow border border-gray-200 overflow-hidden">
  <table class="w-full text-sm">
    <thead class="bg-gray-100 border-b border-gray-200 text-left">
      <tr>
        <th class="px-4 py-2 font-medium">step</th>
        <th class="px-4 py-2 font-medium">profile</th>
        <th class="px-4 py-2 font-medium">finding</th>
        <th class="px-4 py-2 font-medium">repair</th>
      </tr>
    </thead>
    <tbody>
      {% for finding in gate_findings %}
      <tr class="border-b border-gray-100">
        <td class="px-4 py-2 font-mono">{{ finding.step_id }}</td>
        <td class="px-4 py-2 font-mono text-xs">{{ finding.profile_id }}</td>
        <td class="px-4 py-2">
          <div class="font-mono text-xs">{{ finding.severity }} {{ finding.stage }}/{{ finding.code }}</div>
          <div class="text-xs text-gray-700 mt-1">{{ finding.message }}</div>
          {% if finding.paths %}
          <div class="text-xs text-gray-500 mt-1">{{ finding.paths | join(', ') }}</div>
          {% endif %}
        </td>
        <td class="px-4 py-2 text-xs text-gray-700">{{ finding.repair_hint }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endif %}
```

In `src/code_minions/web/templates/run_detail.html`, include the partial below the step table:

```html
{% include "partials/gate_findings.html" %}
```

- [ ] **Step 5: Run Web tests**

Run:

```bash
pytest tests/unit/test_web_routes.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/code_minions/web/routes/runs.py src/code_minions/web/templates/run_detail.html src/code_minions/web/templates/partials/gate_findings.html tests/unit/test_web_routes.py
git commit -m "feat: show gate findings in web run detail"
```

---

### Task 7: End-To-End Verification And Documentation

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `PROGRESS.md`

- [ ] **Step 1: Add changelog entry**

Under `## [Unreleased] -> ### Added`, add:

```markdown
- Implementation runs now resolve an agent profile and expose structured gate
  findings in CLI/Web review surfaces, making delivery preflight and runtime
  repair hints visible without digging through raw test logs.
```

- [ ] **Step 2: Add progress handoff entry**

At the top of `PROGRESS.md`, add:

```markdown
## 2026-04-30 — Agent profile and gate review surface

- Shipped:
  - Added built-in agent profile resolution for implementation roles.
  - Added canonical gate findings and structured implementation context packages.
  - Wired `implement-with-tdd` to emit profile and gate finding metadata.
  - Surfaced gate findings in CLI `status` and Web run detail views.
- Next: use the new findings surface to classify the next React/Vite PRD run before adding more stack-specific rules.
```

- [ ] **Step 3: Run full verification**

Run:

```bash
pytest
uvx ruff check .
```

Expected:

- `pytest`: all tests pass.
- `uvx ruff check .`: `All checks passed!`

- [ ] **Step 4: Check git status**

Run:

```bash
git status --short
```

Expected: only intended tracked files are modified. If `README.md` is still dirty from earlier unrelated edits, leave it unstaged.

- [ ] **Step 5: Commit docs/handoff**

```bash
git add CHANGELOG.md PROGRESS.md
git commit -m "docs: document agent profile gate surface"
```

---

## Execution Notes

- Preserve the existing `failure_hints_for_output()` API until all callers move
  to structured findings.
- Do not move the current React/Vite validators out of `delivery.py` in this
  milestone. The new `gates.py` module adapts their output first.
- Keep status output additive. Existing users should still see raw `outputs:`
  after the new `findings:` section.
- Do not stage the pre-existing dirty `README.md` unless the user explicitly
  asks to include it.

## Self-Review

- Spec coverage: profile, context, gate findings, CLI review surface, and Web
  review surface are each covered by a task.
- Scope check: project-local profile config and a full Warp-like UI are
  explicitly deferred.
- Type consistency: the plan consistently uses `AgentProfile`,
  `ImplementationContextPackage`, and `GateFinding`.
- Placeholder scan: the plan contains no unresolved placeholders.
