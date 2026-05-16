---
name: product-acceptance-review
description: Deterministic product acceptance review against parsed PRD constraints.
allowed-tools: []
required-mcps: []
entrypoint-script: scripts/run.py
inputs:
  structured_prd: {type: object, required: true}
  tasks: {type: array, required: true}
  implement_results: {type: array, required: true}
  browser_acceptance_output: {type: object, required: false}
outputs:
  accepted: {type: boolean}
  artifact_level: {type: string}
  coverage: {type: array}
  acceptance_items: {type: array}
  verifier_rounds: {type: array}
  blockers: {type: array}
  warnings: {type: array}
  evidence: {type: object}
---

# product-acceptance-review

Review the completed worktree against the parsed PRD product contract.

This skill is deterministic. It distinguishes pipeline success from product
acceptance by checking delivery profile, platform/language/build evidence, task
coverage, acceptance criteria test evidence, and obvious prototype/stub
mismatches.

It emits qcloop-style structured acceptance evidence:
- `acceptance_items`: deterministic pass/warn/fail rows for tasks, task-level
  acceptance criteria, delivery profile checks, platform checks, and artifact
  warnings.
- Criteria items use `criterion:<trace_id>:<index>` ids and attach the task's
  changed test files plus test pass/fail status as evidence.
- Commitment items use `commitment:<trace_id>` ids and compare actual changed
  files against `implement-with-tdd`'s `plan_commitment.will_change_paths`.
- `verifier_rounds`: an independent deterministic verifier pass over those
  items. Blocking failed items make the verifier fail; warnings are reported but
  do not fail acceptance.
