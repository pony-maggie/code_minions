# Workflows

A workflow is a YAML file that ties skills together.

Each workflow declares a workspace mode. Code-changing workflows use isolated
git worktrees; lightweight workflows can run in a scratch directory or read the
project root directly.

## Minimal shape

```yaml
name: my-workflow
description: what it does
workspace:
  mode: git-worktree
inputs:
  foo: {type: string, required: true}
steps:
  - id: step-one
    skill: some-skill
    inputs:
      param: $inputs.foo
```

## Workflow Presets

Use `extends` and `preset_inputs` when a workflow should reuse an existing DAG
with a few fixed inputs. This is how stack-specific PRD workflows stay thin:

```yaml
name: react-vite-prd-to-commit
description: PRD -> local implementation commits for React + Vite projects.
extends: prd-to-commit.yaml
preset_inputs:
  delivery_stack_id: react-vite
```

The child workflow inherits `workspace`, `inputs`, and `steps` from the parent.
`preset_inputs` are merged into runtime inputs and win over caller-provided
values, so the preset workflow keeps its stack contract.

## Workspace Modes

`workspace.mode` controls where steps execute:

- `git-worktree` (default): creates `.devflow/runs/<run-id>/worktree` on a branch like `code-minions/<run-id>`. Use this for workflows that edit code, run tests, commit, push, or open PRs. The project must be a git repo with at least one commit.
- `project-readonly`: uses the project root as `ctx.workdir`. LLM local `Write`, `Edit`, and `Bash` tool calls are rejected. Use this for read-only file workflows such as `summarize-file`.
- `none`: creates `.devflow/runs/<run-id>/workspace` without git. Use this for smoke tests and workflows that only produce scratch artifacts.

Omitting `workspace` keeps the legacy default: `git-worktree`.

## Variables

References in `inputs:` can use:

- `$inputs.<name>` — values provided via `--input name=value` on the CLI
- `$steps.<step-id>.output` — the output dict of an upstream step
- `$steps.<step-id>.output.<key>.<sub-key>` — nested lookup
- `$<as-name>` — inside a `for_each` step, the current iteration item

Literals (strings, numbers, lists, dicts) are passed through unchanged.

## Dependencies

Use `depends_on: [step-a, step-b]` to declare explicit ordering. The runner does a topological sort; steps without `depends_on` run in declaration order.

## `for_each` fan-out

```yaml
- id: process-each
  for_each: $steps.list.output.items
  as: item
  skill: handle-one
  inputs:
    item: $item
  depends_on: [list]
```

Each iteration runs the skill with `$item` bound to the current list element. Observer events fire per iteration with ids like `process-each[0]`, `process-each[1]`, etc. Parent step output is `{"items": [<each-iteration-output>, ...]}`.

v1 runs iterations serially.

## Hooks

Skills declare their own `post_run` hooks in `SKILL.md` frontmatter. Runtime-wide hooks are invoked after skill success; they receive a `HookContext` with `workdir`, `skill_name`, `step_id`, `outputs`.

Built-in: `lint` (runs `ruff check` in the run workspace if `ruff` is on PATH).

Custom: drop `hooks/my_hook.py` in project root with a `def run(ctx): ...`; it's auto-registered as `my-hook`.

## Discovery order

Workflows are searched in:
1. each path in `devflow.yaml -> workflow.search_paths`
2. `<package>/builtin/workflows/*.yaml`

Relative paths are resolved from the project root. If `workflow.search_paths` is
omitted, `./workflows` is used.

`code-minions run <workflow>` uses the explicit workflow name. `code-minions run`
without a workflow argument uses `devflow.yaml -> workflow.default`. Built-in
skills and configured `skills.search_paths` follow the same project-first,
built-in-fallback shape.

## Built-in workflows

| Workflow | Use it when | Command |
|---|---|---|
| `hello-world` | Verify installation, run storage, and scratch workspace creation without AI, git, or external services. | `code-minions run hello-world --input name=world` |
| `summarize-file` | Run a small AI smoke test: deterministic file read, then one LLM call for a summary. | `code-minions run summarize-file --input file=./README.md` |
| `prd-to-commit` | Run PRD → planned tasks → implementation commits → report, without Jira tickets or a GitHub PR. | `code-minions run prd-to-commit --input prd=./my-prd.md` |
| `react-vite-prd-to-commit` | Same DAG as `prd-to-commit`, with `delivery_stack_id=react-vite` preset. | `code-minions run react-vite-prd-to-commit --input prd=./my-prd.md` |
| `swift-xcodegen-prd-to-commit` | Same DAG as `prd-to-commit`, with `delivery_stack_id=swift-xcodegen` preset. | `code-minions run swift-xcodegen-prd-to-commit --input prd=./my-prd.md` |
| `go-service-prd-to-commit` | Same DAG as `prd-to-commit`, with `delivery_stack_id=go-service` preset. | `code-minions run go-service-prd-to-commit --input prd=./my-prd.md` |
| `python-cli-prd-to-commit` | Same DAG as `prd-to-commit`, with `delivery_stack_id=python-cli` preset. | `code-minions run python-cli-prd-to-commit --input prd=./my-prd.md` |
| `react-vite-prd-to-pr` | Same PR flow as `prd-to-pr`, with `delivery_stack_id=react-vite` preset. | See below. |
| `python-cli-prd-to-pr` | Same PR flow as `prd-to-pr`, with `delivery_stack_id=python-cli` preset. | See below. |
| `prd-to-pr` | Base PR flow for custom stacks or PRDs that already include a delivery contract. Prefer a stack-specific wrapper when available. | See below. |

For `summarize-file`, the `file` input is relative to the project root visible
from your shell. It uses `workspace.mode: project-readonly`, so the file does
not need to be committed.

## `prd-to-pr` prerequisites

The built-in PRD-to-PR workflows are the most opinionated workflows in the repo.
Prefer `react-vite-prd-to-pr` or `python-cli-prd-to-pr` when one matches your
project. Use the generic `prd-to-pr` as a base workflow for custom stacks or
PRDs that already include a complete delivery contract.

They assume all of the following are already true before you run one:

- you are inside a local git repository with at least one commit
- that repository has an `origin` remote
- `origin` points to the target GitHub repository
- your local clone can push to `origin`
- `.mcp.json` defines the Jira and GitHub integrations used by this workflow
- your Jira credentials can create issues in the target `project_key`
- your GitHub credentials can create pull requests in the target repository

Inputs:

- `prd` — path to the PRD file relative to the project root; it must be committed so the git worktree can read it
- `delivery_stack_id` — optional on `prd-to-pr`; preset automatically by stack-specific PR workflows
- `project_key` — Jira project key, for example `ABC`
- `epic_title` — Jira Epic title and the basis for the PR title

What it does:

1. parse the PRD
2. create Jira tickets
3. implement each ticket in a run-scoped worktree branch
4. write `report.md`
5. push the branch to `origin`
6. create a GitHub pull request through the `github` MCP server

## `prd-to-commit`

The built-in `prd-to-commit` workflow stops before Jira and GitHub:

1. parse the PRD
2. plan implementation tasks
3. implement each task in a run-scoped worktree branch
4. write `report.md`

Run it with:

```bash
code-minions run prd-to-commit --input prd=./my-prd.md
```

It uses `workspace.mode: git-worktree`, so the project must be a git repo with
at least one commit, and `my-prd.md` must be committed.

`parse-prd` and `plan-tasks` are cached in `.devflow/skill_cache.db` when their
inputs, prompts, and configured LLM provider/model are unchanged. Implementation
steps are intentionally not cached because they write files and create commits.
