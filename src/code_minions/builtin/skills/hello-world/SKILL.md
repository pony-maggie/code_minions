---
name: hello-world
description: Walking-skeleton smoke test skill. Writes a greeting file.
allowed-tools: []
required-mcps: []
entrypoint-script: scripts/run.py
inputs:
  name: {type: string, required: true}
outputs:
  file_path: {type: string}
  greeting: {type: string}
---

# hello-world

Writes a greeting file inside the run workspace. Used as a walking-skeleton
smoke test for the platform; no LLM or MCP involvement.

## Inputs
- `name` (string, required): who to greet.

## Outputs
- `file_path` (string): path to the greeting file.
- `greeting` (string): the greeting text written.
