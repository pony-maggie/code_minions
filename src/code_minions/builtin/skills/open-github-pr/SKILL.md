---
name: open-github-pr
description: Push the current worktree branch and create a GitHub pull request.
allowed-tools: []
required-mcps:
  - github
entrypoint-script: scripts/run.py
inputs:
  prd: {type: string, required: true}
  epic_title: {type: string, required: true}
  tickets_output: {type: object, required: true}
  implement_results: {type: array, required: true}
  report_path: {type: string, required: true}
outputs:
  branch: {type: string}
  base_branch: {type: string}
  origin_url: {type: string}
  repo_owner: {type: string}
  repo_name: {type: string}
  pushed: {type: boolean}
  pr_url: {type: string}
  pr_number: {type: integer}
  title: {type: string}
  body_preview: {type: string}
  error: {type: string}
---

# open-github-pr

Push the current worktree branch to `origin` and create a GitHub pull request.

## Inputs
- `prd` (string, required): path to the PRD file used for this run.
- `epic_title` (string, required): Jira Epic title; used to build the PR title.
- `tickets_output` (object, required): output of `create-jira-tickets`.
- `implement_results` (array, required): output items from `implement-with-tdd`.
- `report_path` (string, required): relative path to the final report file in the worktree.

## Outputs
- `branch` (string)
- `base_branch` (string)
- `origin_url` (string)
- `repo_owner` (string)
- `repo_name` (string)
- `pushed` (boolean)
- `pr_url` (string)
- `pr_number` (integer)
- `title` (string)
- `body_preview` (string)
- `error` (string)

## Instructions
- This skill is deterministic and handled by `entrypoint-script`.
- Use local git state from the worktree.
- Use the `github` MCP server only for PR creation.
- Fail clearly if `origin` is missing, not GitHub, or if the GitHub MCP server does not expose a PR creation tool.
