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
outputs:
  accepted: {type: boolean}
  artifact_level: {type: string}
  coverage: {type: array}
  blockers: {type: array}
  warnings: {type: array}
  evidence: {type: object}
---

# product-acceptance-review

Review the completed worktree against the parsed PRD product contract.

This skill is deterministic. It distinguishes pipeline success from product
acceptance by checking delivery profile, platform/language/build evidence, task
coverage, and obvious prototype/stub mismatches.
