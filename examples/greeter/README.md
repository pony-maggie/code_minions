# Example: greeter

A minimal example showing how to run code-minions on a small project.

## Setup

1. Copy this directory somewhere and `cd` in.
2. Turn it into a git repo: `git init && git commit --allow-empty -m init`
3. Install code-minions: `pip install code-minions`
4. Set your LLM key: `export ANTHROPIC_API_KEY=sk-...`
5. No filesystem MCP is required; `summarize-file` reads local files through the built-in `Read` tool.

## Run

```bash
code-minions run summarize-file --input file=./prd.md
```

Expected: a `summary` output describing the PRD's intent.

This example uses the built-in `summarize-file` skill rather than the full `prd-to-pr` flow, because the latter needs a Jira MCP + real Jira instance.
