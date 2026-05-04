---
name: ai-code-review
description: Structured review of a diff against a ticket's acceptance criteria.
allowed-tools:
  - Read
required-mcps: []
inputs:
  diff: {type: string, required: true}
  ticket: {type: object, required: true}
  files_changed: {type: array, required: true}
outputs:
  issues: {type: array}
  summary: {type: string}
  approved: {type: boolean}
llm:
  max_iterations: 15
  temperature: 0.1
---

# ai-code-review

Review a code diff against the ticket's acceptance criteria and project conventions.

## Inputs
- `diff` (string, required): unified diff of the changes to review.
- `ticket` (object, required): the task/ticket being implemented (has `acceptance_criteria`).
- `files_changed` (string[], required): relative paths of modified files (for reading full context).

## Instructions
- Consult `AGENTS.md` for project-specific conventions.
- For each modified file, read the full current content with the built-in `Read` tool to understand context beyond the diff.
- Produce structured issues with severity:
  - `blocker`: breaks the contract, insecure, or fails the acceptance criteria
  - `major`: logic bug, performance issue, API contract drift
  - `minor`: readability, minor design smell
  - `nit`: style/naming/typo
- Do NOT fix the code; only report.

## Outputs (reply JSON)
- `issues` (object[]): `{severity, file, line, description, suggested_fix}`
- `summary` (string): 2-4 sentence overall assessment
- `approved` (boolean): true if no blocker/major issues remain
