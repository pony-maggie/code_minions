# Skills

A skill is a directory containing one required file:

- `SKILL.md` — Markdown instructions plus YAML frontmatter metadata

The frontmatter is the source of truth for discovery, inputs, outputs, tools,
MCP dependencies, hooks, policies, and optional deterministic entrypoints.

## SKILL.md frontmatter

```md
---
name: parse-prd
description: Parse a PRD file into structured JSON.
allowed-tools:
  - Read
required-mcps: []
inputs:
  prd_file: {type: string, required: true}
outputs:
  goal: {type: string}
llm:
  max_iterations: 5
  temperature: 0.1
  max_tokens: 4096
policies:
  cache: true
---

# parse-prd

Read the requested file and return structured JSON.
```

Use Claude-style kebab-case keys in frontmatter:

- `allowed-tools` declares built-in local tools available to the LLM path: `Read`, `Write`, `Edit`, and `Bash`.
- `required-mcps` declares external systems such as `jira` or `github`.
- `entrypoint-script` declares a deterministic Python entrypoint for skills that should not run through the LLM loop.
- `inputs`, `outputs`, `hooks`, `policies`, `invokes-skills`, and `llm` are `code_minions` extensions that continue to be supported in frontmatter.
- `llm.max_tokens` controls the per-call output budget for LLM-path skills. Use a larger value for skills that must emit large JSON objects.
- `policies.cache: true` opts a skill into persistent LLM-output caching when it is safe to do so.

## LLM output caching

`policies.cache: true` enables a project-local cache under
`.devflow/skill_cache.db` for LLM-path skills that have no side effects.

The runtime only uses the cache when all of these are true:

- the skill has no `entrypoint-script`
- the skill has no `required-mcps`
- the skill exposes no mutating local tools; `Read` is allowed
- the skill frontmatter explicitly sets `policies.cache: true`

The cache key includes the skill name, frontmatter, instructions, inputs, LLM
provider/model identity, and file content hashes for file-like inputs such as
`file`, `path`, `prd`, and `prd_file`. This means editing `./my-prd.md`
invalidates cached `parse-prd` output even if the input path stays the same.

The built-in `parse-prd` and `plan-tasks` skills are cacheable. Code-changing
skills such as `implement-with-tdd`, Jira creation, GitHub PR creation, and
deterministic entrypoints are not cached.

To force a cold run, delete `.devflow/skill_cache.db`.

## Deterministic entrypoints

Use `entrypoint-script` when a skill needs deterministic orchestration, strict
subprocess control, git discipline, or nested skill calls.

```md
---
name: hello-world
description: Write a greeting file.
entrypoint-script: scripts/run.py
inputs:
  name: {type: string, required: true}
outputs:
  greeting: {type: string}
---
```

Entrypoint v1 is an in-process Python script with a `run(ctx) -> dict`
function:

```python
def run(ctx):
    # ctx.inputs: dict of declared inputs
    # ctx.workdir: pathlib.Path of the run workspace
    # ctx.llm: LLMBackend if configured
    # ctx.mcp_pool: MCPClientPool if configured
    # ctx.invoke_skill(name, inputs) -> dict: call another skill
    return {"result": "..."}
```

Return a dict matching the skill's declared `outputs` schema.

## Migration from the old format

Old project skills that used `skill.yaml + handler.py` must migrate:

- Move `skill.yaml` metadata into `SKILL.md` frontmatter.
- Rename `required_mcps` to `required-mcps`.
- Rename `invokes_skills` to `invokes-skills`.
- Replace `handler.py` with `entrypoint-script: scripts/run.py` and move deterministic code to `scripts/run.py`.
- Declare only external product integrations in `required-mcps`.

There is no compatibility layer for old-format project skills after this
breaking change. `code-minions skill test` reports old `skill.yaml` directories
with a targeted migration error.

## Discovery order

Skills are searched in:

1. `<project>/skills/*`
2. `<package>/builtin/skills/*` (built-ins shipped with code-minions)

First match wins. Your project can override a built-in by creating a skill of
the same name under `./skills/`.

## Built-in skills

- `hello-world` — no-op smoke test
- `summarize-file` — deterministic file read plus one LLM call for a summary
- `parse-prd` — PRD file to structured JSON
- `plan-tasks` — structured PRD to atomic tickets
- `create-jira-tickets` — tickets to Jira Epic and Stories through the `jira` MCP
- `implement-with-tdd` — single-ticket TDD implementation with built-in local tools and AI review
- `ai-code-review` — diff to structured severity-tagged issues
- `compile-report` — aggregate ticket results into `report.md`
- `open-github-pr` — push current branch and create a GitHub pull request through the `github` MCP
