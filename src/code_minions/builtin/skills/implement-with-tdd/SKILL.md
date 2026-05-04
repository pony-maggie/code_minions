---
name: implement-with-tdd
description: TDD implementation with AI code-review loop for a single ticket.
allowed-tools: []
required-mcps: []
entrypoint-script: scripts/run.py
inputs:
  ticket: {type: object, required: true}
outputs:
  files_changed: {type: array}
  test_result: {type: object}
  commit_sha: {type: string}
  review_report: {type: object}
  rounds_used: {type: integer}
invokes-skills:
  - ai-code-review
llm:
  max_iterations: 30
  temperature: 0.2
  max_tokens: 16000
policies:
  self_heal_max_rounds: 3
  reviewer_max_rounds: 0
---

# implement-with-tdd

Implement a single ticket using a TDD cycle and iterate with AI code review until converged or max-rounds reached.

## Inputs
- `ticket` (object, required): one task from plan-tasks / create-jira-tickets.

## Outputs
- `files_changed` (string[])
- `test_result` (object): `{passed: boolean, output: string}`
- `commit_sha` (string): commit created at end of the loop
- `review_report` (object): final ai-code-review output
- `rounds_used` (integer)
