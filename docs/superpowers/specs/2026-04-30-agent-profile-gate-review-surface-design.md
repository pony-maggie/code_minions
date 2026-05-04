# Agent Profile, Context, Gate, And Review Surface Design

## Problem

`code_minions` has been improving React/Vite reliability by adding targeted
delivery-profile checks and failure-playbook hints. That helped individual
failures, but the system is still shaped like a growing list of stack-specific
patches. Users see many failures as the same high-level symptom:
`SkillExecutionError('tests never green')`.

Warp's design points at the next boundary: agent work should be controlled by
explicit profiles, receive structured context, pass through visible gates, and
leave a review surface that explains what happened and what to do next.

## Goals

- Add a first-class agent profile layer for workflow roles such as planning,
  implementation, repair, and review.
- Replace ad hoc implementation prompt assembly with a structured context
  package that carries stable task and stack contracts across steps.
- Normalize delivery/profile checks, runtime failure classifications, and
  review findings into one gate result shape.
- Surface gate findings and repair hints in CLI and Web run detail views so
  users see concrete failure causes instead of only raw test output.
- Keep the first implementation small enough to land safely on the current
  engine and store architecture.

## Non-Goals

- Do not build a full Warp-like terminal UI.
- Do not add cloud agents, multi-user auth, or remote run orchestration.
- Do not fork or embed Warp.
- Do not replace the existing workflow DAG model.
- Do not remove existing delivery profile validators until the new gate layer
  is proven.

## Recommended Approach

Use an incremental architecture that wraps current behavior instead of
rewriting it.

1. Add profile resolution for built-in workflow roles.
2. Add a structured `ImplementationContextPackage` for the implementation
   skill prompt.
3. Add a canonical `GateResult` shape and adapter functions around the current
   delivery profile checks and failure playbook.
4. Persist gate findings as run events and include them in step output JSON.
5. Update CLI and Web views to show the relevant profile, stack, gate findings,
   and repair hints.

This keeps the existing `delivery.py`, `failure_playbook.py`, `run_events`, and
`implement-with-tdd` boundaries useful while making the behavior easier to
reason about and extend.

## Alternatives Considered

### Keep Adding Preflight Checks

This is the smallest change, but it continues the current failure mode: every
new generated-code issue becomes another branch in `delivery.py` or another
runtime hint string. It does not give users a better mental model.

### Build A Full Agent Workspace UI First

This would mimic more of Warp's product surface, but it is too large for the
current problem. The CLI and Web dashboard already exist; the missing piece is
not a new UI shell, but better structured execution metadata.

### Add Profile, Context, Gate, And Surface Incrementally

This is the recommended path. It changes the conceptual model without forcing
a large rewrite. It also gives a direct answer to the repeated PRD-to-commit
failures: every failure should become a categorized gate finding with a repair
hint and a visible owner.

## Architecture

### Agent Profiles

Add a small profile registry for workflow roles:

- `planner`
- `implementer`
- `repair`
- `reviewer`
- stack-specific variants such as `react-vite/implementer`

A profile controls:

- LLM options: model override, temperature, max tokens.
- Loop limits: self-heal rounds and reviewer rounds.
- Gate strictness: `relaxed`, `balanced`, or `strict`.
- Tool policy hints for local tools and MCP usage.
- Prompt guidance blocks tied to a stack or role.

The first phase should resolve profiles from built-in defaults and workflow
inputs. Project-local profile files are intentionally out of scope for this
milestone.

### Context Package

Add a structured context object for implementation prompts. It should include:

- Ticket id, title, description, and acceptance criteria.
- Authoritative delivery profile.
- Resolved stack id and stack pack defaults.
- Resolved agent profile id and relevant settings.
- Existing project config excerpts.
- Source contract summary from previous successful tasks.
- Files changed by previous tasks when available.
- Current gate findings and failure-playbook hints.
- User `AGENTS.md` content, preserving the current ContextAssembler behavior.

The implementation skill can still render this object to text for the LLM, but
the data should be assembled and tested separately from the prompt string.

### Gate Results

Introduce a canonical gate result:

```json
{
  "code": "missing-postcss-plugin",
  "severity": "error",
  "stage": "preflight",
  "message": "PostCSS config references tailwindcss but package.json does not install it.",
  "repair_hint": "Add tailwindcss to devDependencies or remove the PostCSS/Tailwind config.",
  "source": "react-vite",
  "paths": ["postcss.config.js"]
}
```

Stages:

- `preflight`: static project checks before tests.
- `contract`: cross-task interface and layout invariants.
- `runtime`: test/build failures and classified command output.
- `review`: product acceptance and code-review findings.

Existing delivery-profile issues should be adapted into this shape first. The
runtime failure playbook should return `GateResult` objects rather than only
plain hint strings.

### Review Surface

The CLI status table should remain compact, but the detailed output should show
for each failed or warning step:

- delivery stack id
- agent profile id
- gate findings grouped by stage and severity
- repair hints
- failed command when known
- files changed and commit SHA when available

The Web run detail page should add a simple "Findings" section under the step
table. It can read findings from step output JSON and run events. This is a
read-only surface; no new interactive repair action is required in the first
phase.

## Data Flow

1. `parse-prd` produces a delivery profile with `stack_id`.
2. `plan-tasks` copies the authoritative delivery profile to each task.
3. `implement-with-tdd` resolves an agent profile for the task role and stack.
4. It builds an implementation context package.
5. It runs preflight/contract gates before tests.
6. If gates produce errors, the repair prompt receives structured findings.
7. If tests fail, runtime output is classified into gate findings.
8. Findings are persisted in step output JSON and appended to run events.
9. `status` and Web detail render findings and hints.

## Error Handling

- Unknown profile ids fall back to built-in role defaults and emit a warning
  gate finding.
- Invalid gate strictness falls back to `balanced`.
- Gate adapters must not crash workflow execution; malformed findings become
  a single `gate-adapter-error` warning with a redacted message.
- Existing raw command output remains available in step output JSON for
  debugging.

## Testing

Unit coverage should prove:

- Built-in profile resolution for default and React/Vite implementer profiles.
- Context package includes delivery profile, stack id, AGENTS.md, and prior task
  contract data.
- Existing delivery issues are converted to `GateResult`.
- Failure playbook output is converted to runtime gate findings.
- `implement-with-tdd` includes gate findings in self-heal prompts and outputs.
- CLI status detail prints findings without breaking existing output.
- Web run detail renders findings from stored step output.

Integration coverage should keep the current PRD-to-commit smoke tests green.

## Rollout

The first milestone should be internal and backward-compatible:

- Existing workflow YAML keeps working.
- Existing `gate_strictness` values keep their behavior.
- Existing `failure_hints_for_output()` can remain as a compatibility wrapper
  while new code uses gate findings.
- New status/Web fields should be additive.

After the first milestone lands, project-local profile configuration can be
designed separately.
