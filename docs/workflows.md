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

- `git-worktree` (default): creates `.devflow/runs/<run-id>/worktree` on a new branch like `code-minions/<run-id>`, based on the project repository's current `HEAD` when the run starts. Use this for workflows that edit code, run tests, commit, push, or open PRs. The project must be a git repo with at least one commit. Commit workflows leave results on the run branch for you to review and merge manually; PR workflows push that same run branch and open a PR, but do not merge it.
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

## Conditional Steps

Use `when:` to make a step run only when an upstream output is truthy:

```yaml
- id: open_pr
  skill: open-github-pr
  depends_on: [report]
  when: $steps.acceptance.output.accepted
```

When the condition evaluates false, the step is recorded as `skipped` and its
skill is not invoked. A run with skipped conditional steps finishes as
`completed_with_issues` rather than `success`. Built-in PR workflows use this
for the final PR creation step, so failed product acceptance still produces a
report but does not open a pull request.

PRD planning has one additional built-in gate: if `parse-prd` returns
clarification questions, `plan-tasks` stops before LLM task generation and the
run is marked `needs_clarification`.

Implementation failures that preserve useful artifacts and need a human
decision, such as unresolved review blockers, scope drift, test-quality
regressions, or a test suite that never turns green, mark the run
`needs_human` instead of generic `failed`.

Provider and runtime failures are classified separately from product failures.
LLM timeout, overload, rate-limit, or connection errors are recorded as
provider availability failures; provider schema/bad-request failures are
recorded as workflow-systemic failures; build/test failures remain
implementation-fixable; browser/product mismatches remain acceptance failures.
The classification is stored as a `workflow_failure_classified` run event and,
when a report can be compiled, appears in `report.md`.

Task planning also uses `needs_human` when a large PRD appears to hit a
planner's `policies.max_tasks` ceiling. If the PRD has more features than the
configured maximum and the planner returns exactly that maximum number of
tasks, the runtime treats the output as likely compressed instead of silently
accepting it.

Task planning post-processing also attaches stable `trace_id` values to every
task that omits one, and injects the authoritative PRD `delivery_profile` into
planned tasks. Downstream implementation outputs and generated evidence reports
carry the same `trace_id`, so `.devflow/evidence/traceability.json` can map
task, commit, changed files, and test status without relying on LLM narrative.

Product acceptance expands each task's `acceptance_criteria` into deterministic
`criterion:<trace_id>:<index>` acceptance items. A criterion item passes only
when its task has passing test evidence and at least one changed test file;
otherwise it blocks acceptance as missing criteria evidence.

`implement-with-tdd` also emits a deterministic `plan_commitment` for each
ticket before implementation starts. Product acceptance compares actual
`files_changed` against that commitment's `will_change_paths`; drift creates a
blocking `commitment:<trace_id>` acceptance item.

PRD workflows that deliver a Web UI run `web-ui-acceptance-review` before
product acceptance. Supported browser checks produce `.devflow/browser-evidence/`
artifacts, including screenshots, console/page/request diagnostics, layout
metrics, and browser scenario verdicts. Browser acceptance output is fed into
product acceptance as browser-scoped acceptance items, so a unit-test-green but
visually broken UI can still block delivery.

Successful and `completed_with_issues` runs append deterministic local facts to
project-level `.devflow/memory.md`. Future LLM prompts and implementation
context include that memory alongside `AGENTS.md`; the file is local state and
should stay ignored with the rest of `.devflow/`.

Self-heal prompts receive structured failure playbook matches rather than a
single free-form hint block. Each match carries `name`, `category`, `severity`,
`fix_hint`, `auto_fixable`, and `deterministic_fix` fields so known failure
patterns can evolve toward deterministic fixes without changing the prompt
contract.

## Runtime Events

Workflow runs store durable runtime observations in `.devflow/runs.db` table
`run_events`. The main event families are:

- `llm_call_started`, `llm_call_finished`, `llm_call_failed`
- `tool_call_started`, `tool_call_finished`, `tool_call_failed`
- `command_started`, `command_finished`, `command_failed`
- `context_compacted`
- `workflow_failure_classified`

LLM events include provider/model, skill, role, step id, attempt, timeout,
message count, tool count, prompt size, duration, usage, stop reason, and
failure classification. Tool and command events include timeout, duration,
exit code or error, and compact output metadata. Full prompts, API keys, and
large raw outputs are intentionally not persisted in event payloads.

Environment knobs:

- `CODE_MINIONS_LLM_TIMEOUT_SECONDS` controls provider request timeout.
- `CODE_MINIONS_CONTEXT_BUDGET_CHARS` controls when long agent conversations
  are compacted before the next model call.

## Sensors

Workflow sensors are deterministic checks attached to steps. The first supported
sensor type is `command`, which runs after the skill returns and before the step
is recorded as successful:

```yaml
sensors:
  typecheck:
    type: command
    command: npm run typecheck
    severity: blocker
    timeout_seconds: 120

steps:
  - id: implement
    skill: implement-with-tdd
    sensors: [typecheck]
```

`severity: blocker` and `severity: error` fail the step and write a
`gate_findings` entry with command output. Lower severities, such as `warning`,
are recorded in the successful step output without blocking the workflow. Use
command sensors for project-specific typecheck, secret scan, security audit, and
regression-suite gates that should be auditable outside LLM narrative.

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
| `python-web-prd-to-commit` | Python web preset with `delivery_stack_id=python-web` and a dedicated planner that keeps small FastAPI services in one canonical app task. | `code-minions run python-web-prd-to-commit --input prd=./my-prd.md` |
| `react-vite-prd-to-pr` | Same PR flow as `prd-to-pr`, with `delivery_stack_id=react-vite` preset. | See below. |
| `python-cli-prd-to-pr` | Same PR flow as `prd-to-pr`, with `delivery_stack_id=python-cli` preset. | See below. |
| `python-web-prd-to-pr` | Python web PR flow with `delivery_stack_id=python-web` and the dedicated FastAPI planner. | See below. |
| `prd-to-pr` | Base PR flow for custom stacks or PRDs that already include a delivery contract. Prefer a stack-specific wrapper when available. | See below. |

For `summarize-file`, the `file` input is relative to the project root visible
from your shell. It uses `workspace.mode: project-readonly`, so the file does
not need to be committed.

## `prd-to-pr` prerequisites

The built-in PRD-to-PR workflows are the most opinionated workflows in the repo.
Prefer `react-vite-prd-to-pr`, `python-cli-prd-to-pr`, or
`python-web-prd-to-pr` when one matches your project. Use the generic
`prd-to-pr` as a base workflow for custom stacks or PRDs that already include a
complete delivery contract.

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

The run branch is created from the current local `HEAD` when the workflow
starts. It is not automatically created from the repository's default branch,
and the workflow does not merge the PR.

## `prd-to-commit`

The built-in `prd-to-commit` workflow stops before Jira and GitHub:

1. parse the PRD
2. plan implementation tasks
3. implement each task in a run-scoped worktree branch
4. write `report.md`

The run branch is created from the current local `HEAD` when the workflow
starts. The workflow does not merge those commits back into your current
checkout; review and merge `code-minions/<run-id>` manually when the result is
acceptable.

Run it with:

```bash
code-minions run prd-to-commit --input prd=./my-prd.md
```

It uses `workspace.mode: git-worktree`, so the project must be a git repo with
at least one commit, and `my-prd.md` must be committed.

`parse-prd` and `plan-tasks` are cached in `.devflow/skill_cache.db` when their
inputs, prompts, and configured LLM provider/model are unchanged. Implementation
steps are intentionally not cached because they write files and create commits.
