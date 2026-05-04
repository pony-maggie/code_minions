---
name: plan-tasks
description: Decompose structured PRD into atomic implementation tickets.
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
  max_tasks: 8
---

# plan-tasks

Turn a structured PRD into a small list of implementation batches.

## Inputs
- `structured_prd` (object, required): output of parse-prd.

## Instructions
- Return at most 8 tasks. For a small or medium PRD, target 3-6 tasks.
- Each task should be an implementation batch that groups related small requirements, not a micro-ticket.
- Each task must be small enough to implement in one focused run.
- Each task must include explicit acceptance criteria (Gherkin-style preferred).
- Respect dependencies: output tickets in implementation order.
- Do NOT invent features not present in `structured_prd`.
- If `structured_prd.delivery_profile` is present, copy it unchanged into every
  task as `delivery_profile`. Each task must respect that delivery shape; do not
  switch languages, frameworks, build systems, or test commands to make the task
  easier.

Reply with a single JSON object.

## Outputs
- `tasks` (object[]): `{id, title, description, acceptance_criteria: string[], depends_on: string[], delivery_profile?: object}`
