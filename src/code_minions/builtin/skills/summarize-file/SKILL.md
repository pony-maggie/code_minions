---
name: summarize-file
description: Read a project file and produce a paragraph summary.
allowed-tools: []
required-mcps: []
entrypoint-script: scripts/run.py
inputs:
  path: {type: string, required: true}
outputs:
  summary: {type: string}
  byte_count: {type: integer}
llm:
  max_iterations: 5
  temperature: 0.1
---

# summarize-file

Read a file from the project root and use the configured LLM to produce a
one-paragraph summary of its contents. File
reading is deterministic; the LLM is only responsible for the summary text.

## Inputs
- `path` (string, required): path relative to the project root.

## Outputs (reply with JSON matching this schema)
- `summary` (string): 1-paragraph summary.
- `byte_count` (integer): size of the file read.
