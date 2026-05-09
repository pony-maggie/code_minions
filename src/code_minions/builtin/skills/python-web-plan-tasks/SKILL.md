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
- Respect dependencies and implementation order.
- Do NOT invent features not present in `structured_prd`.
- If `structured_prd.delivery_profile` is present, copy it unchanged into every
  task as `delivery_profile`.

## Outputs

- `tasks` (object[]): `{id, title, description, acceptance_criteria: string[], depends_on: string[], delivery_profile?: object}`
