[![CI](https://github.com/malu/code-minions/actions/workflows/ci.yml/badge.svg)](https://github.com/malu/code-minions/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

# code_minions

> AI dev workflow engine. Open-source. Configurable. Plug-in skills.

📚 **Docs:** [quickstart](docs/quickstart.md) · [skills](docs/skills.md) · [workflows](docs/workflows.md)

🇨🇳 **中文**：[README_zh.md](README_zh.md)

## What It Is

`code_minions` turns AI-assisted development workflows into repeatable CLI runs.
It executes YAML workflows in a declared workspace mode, persists state to
SQLite, and lets you resume failed long-running work. Code-changing workflows
use isolated git worktrees; lightweight smoke tests can run without git.

The core product model:

- **External systems via MCP:** Jira, GitHub, and similar products are connected through `.mcp.json`.
- **Composable skills:** each workflow step is a skill. Skills use Claude-style `SKILL.md` frontmatter plus optional deterministic `entrypoint-script` code.
- **Project-aware prompts:** `AGENTS.md` is injected into LLM-driven skill prompts so runs follow your repo conventions.

## Install

`code-minions` is not published on PyPI yet. Install from source:

```bash
git clone https://github.com/malu/code-minions.git
cd code-minions
pip install -e .
```

Requires Python 3.11+.

## Two-Minute Smoke Test

```bash
cd your-project
code-minions init .
code-minions run hello-world --input name=world
code-minions list-runs
code-minions status <run-id>
```

`hello-world` needs no LLM key, no MCP server, and no git repo. It verifies
project initialization, run storage, scratch workspace creation, and
deterministic skill execution. Workflows that modify code, such as
`prd-to-commit` and `prd-to-pr`, still require a local git repo with at least
one commit.

For PRD workflows, include a `Delivery Contract` / `delivery_profile` in the
PRD so the run knows the required product shape, language, build system, test
command, required files, and forbidden product languages. See
[PRD template](docs/prd-template.md) for Swift macOS, Go service, Python CLI,
and React examples.

For LLM provider setup, Jira/GitHub MCP examples, and full PRD-to-PR
prerequisites, follow the [quickstart](docs/quickstart.md).

## Built-In Workflows

| Workflow | Use It When | Minimal Command |
|---|---|---|
| `hello-world` | You want to verify installation and runtime basics without AI or external services. | `code-minions run hello-world --input name=world` |
| `summarize-file` | You want a small AI smoke test: deterministic file read, then one LLM call for a summary. | `code-minions run summarize-file --input file=./README.md` |
| `prd-to-commit` | You want PRD -> planned tasks -> implementation commits -> report, without Jira or GitHub. | `code-minions run prd-to-commit --input prd=./my-prd.md` |
| `react-vite-prd-to-commit` | You want the same local commit flow, but with React + TypeScript + Vite rules pinned up front. | `code-minions run react-vite-prd-to-commit --input prd=./my-prd.md` |
| `swift-xcodegen-prd-to-commit` | You want the same local commit flow, but with Swift + SwiftUI + XcodeGen rules pinned up front. | `code-minions run swift-xcodegen-prd-to-commit --input prd=./my-prd.md` |
| `go-service-prd-to-commit` | You want the same local commit flow, but with Go service rules pinned up front. | `code-minions run go-service-prd-to-commit --input prd=./my-prd.md` |
| `python-cli-prd-to-commit` | You want the same local commit flow, but with Python CLI rules pinned up front. | `code-minions run python-cli-prd-to-commit --input prd=./my-prd.md` |
| `prd-to-pr` | You want the full path: PRD -> Jira issues -> implementation commits -> report -> GitHub PR. | See [quickstart](docs/quickstart.md#run-a-workflow). |

`prd-to-commit` is the generic entry point. It does not default to React/Vite;
it uses the PRD's `delivery_profile` when present and otherwise relies on stack
inference. Use a stack-specific workflow, such as `react-vite-prd-to-commit`,
when the product stack is already known and the harness should enforce that
stack from the start. The generic workflow can also be pinned explicitly:

```bash
code-minions run prd-to-commit \
  --input prd=./my-prd.md \
  --input delivery_stack_id=react-vite
```

After a run:

```bash
code-minions status <run-id>
ls .devflow/runs/<run-id>/
code-minions resume <run-id>
```

For code-changing runs, implementation commits live on the run branch
`code-minions/<run-id>` inside `.devflow/runs/<run-id>/worktree`. After
reviewing the worktree and `report.md`, merge the branch back into your project
branch:

```bash
git switch main
git merge --no-ff code-minions/<run-id>
git worktree remove .devflow/runs/<run-id>/worktree
git branch -d code-minions/<run-id>
```

See [quickstart](docs/quickstart.md#land-worktree-results) for review,
conflict, and cleanup notes.

`code-minions run` prints live step progress while it is attached. For long
workflows, open another terminal and use `status` or `list-runs` to inspect the
same run.

See [workflows](docs/workflows.md) for workflow YAML details and [skills](docs/skills.md)
for custom skill authoring.

## Web Dashboard

```bash
code-minions web
```

Open `http://127.0.0.1:8080/` to view runs, inspect step status, start workflows
from a form, and receive live SSE updates for Web-started runs.

Current limits:

- localhost-only, no authentication
- CLI-started runs appear in the Web UI but do not stream live across processes
- Web `Cancel` is advisory and Web `Resume` is currently synchronous

## How It Works

1. `code-minions run [workflow]` loads workflow YAML from the configured `workflow.search_paths` or built-ins.
2. If `[workflow]` is omitted, `devflow.yaml -> workflow.default` is used; an explicit CLI workflow always overrides it.
3. The engine creates the workflow's workspace: a scratch directory, the project root in read-only mode, or `.devflow/runs/<run-id>/worktree` on a branch like `code-minions/<run-id>`.
4. DAGRunner executes each step in dependency order.
5. Deterministic skills run their declared `entrypoint-script`; LLM skills read `SKILL.md` + `AGENTS.md` and call allowed tools.
6. Run state is stored in `.devflow/runs.db`, enabling status inspection and resume.

## Built-In Skills

`hello-world`, `summarize-file`, `parse-prd`, `plan-tasks`,
`create-jira-tickets`, `implement-with-tdd`, `ai-code-review`,
`compile-report`, and `open-github-pr`.

Inspect them locally:

```bash
code-minions skill list
code-minions skill info parse-prd
code-minions skill test
```

## License

MIT.
