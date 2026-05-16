---
name: python-web-plan-tasks
description: Decompose a Python web PRD into implementation tickets that preserve one FastAPI app.
allowed-tools: []
required-mcps: []
inputs:
  structured_prd: {type: object, required: true}
outputs:
  tasks: {type: array}
llm:
  max_iterations: 5
  temperature: 0.2
  max_tokens: 12000
policies:
  cache: true
  max_tasks: 4
---

# python-web-plan-tasks

Turn a structured PRD into implementation batches for a Python FastAPI web
service.

## Instructions

- Return a single JSON object.
- If the PRD is small or medium, prefer exactly one implementation task that
  scaffolds the canonical package and implements all endpoints together. This is
  intentional: Python web services share one ASGI app, so splitting endpoint
  work too finely can create multiple app modules or route drift.
- Use multiple tasks only when the PRD clearly contains independent large
  subsystems. Even then, every task must explicitly preserve the same
  `src/<package>/app.py` FastAPI app and existing route paths.
- If the PRD names an app import such as `<package>.app:app`, every task must
  use that package. Tests must import `from <package>.app import app`.
- Each task must include explicit acceptance criteria.
- Each task must include `expected_paths` covering the canonical app package,
  tests, and build/dependency files it owns, for example
  `["src/<package>/**", "tests/**", "pyproject.toml"]`.
- Respect dependencies and implementation order.
- Do NOT invent features not present in `structured_prd`.
- Do not write `delivery_profile` yourself. The runtime injects the
  authoritative `structured_prd.delivery_profile` into every task after
  planning.
- The runtime injects `trace_id` for tasks that omit it. If you provide a
  `trace_id`, keep it stable and unique within this plan.

## Outputs

- `tasks` (object[]): `{id, trace_id?: string, title, description, acceptance_criteria: string[], expected_paths: string[], depends_on: string[], delivery_profile?: object}`
