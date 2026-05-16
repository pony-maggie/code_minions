---
name: compile-report
description: Write a final report.md summarizing run results.
allowed-tools:
  - Write
required-mcps: []
entrypoint-script: scripts/run.py
inputs:
  implement_results: {type: array, required: true}
  acceptance_output: {type: object, required: false}
  browser_acceptance_output: {type: object, required: false}
  tickets_output: {type: object, required: true}
  output_path: {type: string, required: true}
outputs:
  report_path: {type: string}
  evidence_paths: {type: array}
llm:
  max_iterations: 5
  temperature: 0.0
---

# compile-report

Compose a final `report.md` summarizing all implemented tickets + their reviews.

## Inputs
- `implement_results` (array, required): outputs of each implement-with-tdd iteration.
- `acceptance_output` (object, optional): product acceptance summary.
- `browser_acceptance_output` (object, optional): browser/e2e visual acceptance summary.
- `tickets_output` (object, required): outputs of create-jira-tickets (for Jira links).
- `output_path` (string, required): where to write the report (relative to worktree).

## Outputs
- `report_path` (string)
- `evidence_paths` (string[]): machine-readable evidence artifacts written under `.devflow/evidence/`
