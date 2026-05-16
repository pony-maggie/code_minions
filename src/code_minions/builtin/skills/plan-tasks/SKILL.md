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
- Each task must include `expected_paths`: a conservative list of file globs the
  task is expected to own, such as `["src/**", "tests/**"]`. Keep it narrow
  enough to catch unrelated edits, but broad enough for normal implementation
  and tests.
- For bootstrap tasks that create a Web/Vite app, include scaffold and config
  paths such as `package.json`, `index.html`, `vite.config.*`, and
  `tsconfig*.json` along with `src/**` and test paths.
- Respect dependencies: output tickets in implementation order.
- Do NOT invent features not present in `structured_prd`.
- Preserve negative constraints and rule exceptions exactly. Do not turn a
  forbidden behavior into a positive acceptance criterion.
- For grid movement apps, if the PRD forbids 180-degree reverse turns,
  every keyboard and mobile-control task criterion must respect that rule:
  `up <-> down` and `left <-> right` are illegal immediate reversals. Test
  legal mobile turns from a non-opposite current direction instead of expecting
  an immediate opposite turn to succeed.
- Do not write `delivery_profile` yourself. The runtime injects the
  authoritative `structured_prd.delivery_profile` into every task after
  planning.
- The runtime injects `trace_id` for tasks that omit it. If you provide a
  `trace_id`, keep it stable and unique within this plan.

Reply with a single JSON object.

## Outputs
- `tasks` (object[]): `{id, trace_id?: string, title, description, acceptance_criteria: string[], expected_paths: string[], depends_on: string[], delivery_profile?: object}`
