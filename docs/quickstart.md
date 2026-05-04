# Quickstart

## Install

`code-minions` is not published on PyPI yet. Install it from the source checkout:

```bash
git clone https://github.com/malu/code-minions.git
cd code-minions
pip install -e .
```

If you already cloned the repo and want the latest local code:

```bash
cd code-minions
git pull
pip install -e .
```

If your Python environment still resolves an old installed copy, uninstall it
and reinstall from this checkout:

```bash
pip uninstall code-minions
pip install -e .
```

Python 3.11+.

## Initialize a project

`hello-world` and `summarize-file` do not require a git repo. Workflows that
modify code, such as `prd-to-commit` and `prd-to-pr`, create a git worktree and
therefore require a local git repo with at least one commit.

```bash
cd your-project
code-minions init .
```

This creates:
- `devflow.yaml` — platform config (LLM provider, search paths)
- `AGENTS.md` — project conventions (fill this in!)
- `.mcp.json` — MCP server registry
- `.devflow/` — added to `.gitignore`; holds run state

In `devflow.yaml`, `workflow.default` is used when you run
`code-minions run` without a workflow argument. Passing an explicit workflow on
the command line always wins, for example `code-minions run hello-world`.
`workflow.search_paths` and `skills.search_paths` are searched before the
built-in workflows and skills.

## Configure the LLM

Configure one of them:
```bash
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-proj-...
export GEMINI_API_KEY=...
export MINIMAX_API_KEY=...
```

Edit `devflow.yaml` to pick the provider. Provider names follow LiteLLM prefixes,
so you can keep multiple providers in the same file and switch `llm.default`.
Only the selected `llm.default` provider needs its API key to be set.
The examples below prefer coding-oriented models over the cheapest chat models:

```yaml
llm:
  default: anthropic
  providers:
    anthropic:
      model: claude-sonnet-4-6
      api_key_env: ANTHROPIC_API_KEY
    openai:
      model: gpt-5.5
      api_key_env: OPENAI_API_KEY
    gemini:
      model: gemini-3.1-pro-preview
      api_key_env: GEMINI_API_KEY
    minimax:
      model: MiniMax-M2.7
      api_key_env: MINIMAX_API_KEY
      api_base: https://api.minimaxi.com/v1
```

Model selection notes:
- Anthropic: `claude-sonnet-4-6` is the balanced daily coding default; use `claude-opus-4-6` for the hardest refactors and planning-heavy work.
- OpenAI: `gpt-5.5` is the current strong default for complex coding; use `gpt-5.4-mini` when latency and cost matter more than maximum capability.
- Gemini: `gemini-3.1-pro-preview` is the coding/agentic preview model; use `gemini-2.5-pro` if you want a stable model string for production-style runs.
- MiniMax: `MiniMax-M2.7` is the current coding-tool default; use `MiniMax-M2.7-highspeed` when you have access and want lower latency. China-region Token Plan keys use `https://api.minimaxi.com/v1`; international keys can switch `api_base` to `https://api.minimax.io/v1`.

## Configure external integrations

Edit `.mcp.json` when a workflow needs to talk to an external product. The file
uses the same local stdio MCP format as Claude Code / Cursor. For the built-in
`prd-to-pr` workflow, configure Jira for issue creation and GitHub for the final
pull request.

```json
{
  "mcpServers": {}
}
```

Example `.mcp.json`:

```json
{
  "mcpServers": {
    "jira": {
      "command": "answerai-jira-mcp",
      "env": {
        "JIRA_BASE_URL": "https://your-domain.atlassian.net",
        "JIRA_USER_EMAIL": "you@example.com",
        "JIRA_API_TOKEN": "..."
      }
    },
    "github": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "-e",
        "GITHUB_PERSONAL_ACCESS_TOKEN",
        "-e",
        "GITHUB_TOOLSETS",
        "ghcr.io/github/github-mcp-server"
      ],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "github_pat_...",
        "GITHUB_TOOLSETS": "repos,pull_requests"
      }
    }
  }
}
```

Notes:
- The GitHub example uses GitHub's official `github/github-mcp-server` local server configuration and enables the toolsets needed for PR creation.
- The Jira example uses `answerai-jira-mcp` as one concrete local stdio option. `code_minions` only requires a server named `jira` that can create and query Jira issues.
- Atlassian's remote Rovo MCP is a different integration style; `code_minions` currently expects local stdio MCP servers in `.mcp.json`.

## End-to-end prerequisites for `prd-to-pr`

Before you try the full PRD-to-PR workflow in a real project, make sure all of
these are in place:

- local tools:
  - Python 3.11+
  - `git`
  - the local executables referenced by your Jira and GitHub MCP server config
- accounts and permissions:
  - one working LLM provider API key
  - Jira credentials that can create issues in the target `project_key`
  - GitHub credentials that can push to the target repo and create pull requests
- project state:
  - the current directory is already a git repo with at least one commit
  - the repo has an `origin` remote pointing at the target GitHub repository
  - `AGENTS.md` is filled in with your project conventions
  - you have a committed PRD file ready to pass via `--input prd=...`

## PRD delivery stack packs

For reliable `prd-to-commit` runs, include a `delivery_profile.stack_id` in your
PRD when the technology stack is known. Stack packs let code-minions apply
focused install commands, validation gates, test harness guidance, and failure
repair hints for a specific stack instead of treating every PRD as a generic
software project.

Built-in stack IDs:

- `react-vite` — React + TypeScript + Vite web app with Vitest.
- `swift-xcodegen` — native macOS Swift/SwiftUI app generated by XcodeGen.
- `go-service` — Go web service using Go modules.
- `python-cli` — Python CLI.

Example:

```yaml
delivery_profile:
  stack_id: react-vite
  kind: web-app
  language: typescript
  framework: react
  build_system: vite
  test_command: npm test
  gate_strictness: relaxed
```

## Run a workflow

Run the default workflow from `devflow.yaml`:
```bash
code-minions run \
  --input prd=./my-prd.md \
  --input project_key=ABC \
  --input epic_title="Q2 feature pack"
```

Or pass a workflow name explicitly to override `workflow.default`:

Smoke test (no LLM/MCP needed):
```bash
code-minions run hello-world --input name=world
```

`code-minions run` stays attached and prints live step progress in the current
terminal. For long LLM/code workflows, you can also inspect the same run from
another terminal with `code-minions status <run-id>` or
`code-minions list-runs`.

This workflow uses `workspace.mode: none`; it writes its output under
`.devflow/runs/<run-id>/workspace` and does not require git.

Small AI workflow:
```bash
code-minions run summarize-file --input file=./README.md
```

`summarize-file` reads the file deterministically from the project root, then
makes one LLM call to summarize the content. It is the smallest workflow for
checking that the selected `llm.default` provider works. The `file` input is
relative to your project root, for example `./README.md`; it does not need to be
committed.

PRD-to-local-commits flow:
```bash
code-minions run prd-to-commit --input prd=./my-prd.md
```

Stack-specific PRD-to-local-commits presets use the same workflow DAG but pin
the delivery stack up front:

```bash
code-minions run react-vite-prd-to-commit --input prd=./my-prd.md
code-minions run swift-xcodegen-prd-to-commit --input prd=./my-prd.md
code-minions run go-service-prd-to-commit --input prd=./my-prd.md
code-minions run python-cli-prd-to-commit --input prd=./my-prd.md
```

Full PRD-to-GitHub-PR flow:
```bash
code-minions run prd-to-pr \
  --input prd=./my-prd.md \
  --input project_key=ABC \
  --input epic_title="Q2 feature pack"
```

In this workflow:
- `project_key` is the Jira project key where tickets will be created
- `epic_title` is the Jira Epic title and the basis for the final PR title
- the current directory must already be a git repo with at least one commit
- that repo must have an `origin` remote pointing at the target GitHub repository
- `prd` is relative to the project root and must be committed so the run worktree can read it

What happens:
- `code_minions` creates a local worktree and branch like `code-minions/<run-id>`
- implements the PRD ticket by ticket
- writes a final `report.md`
- pushes the branch to `origin`
- opens a GitHub pull request automatically

Current v1 limits:
- GitHub only for the final PR step
- `origin` is assumed to be the target remote
- no automatic reviewers, labels, or draft state
- the workflow opens the PR, but does not merge it

## PRD-to-commit workflow

Use the built-in `prd-to-commit` workflow when you want PRD -> implementation
commits without Jira or GitHub. It parses the PRD, plans tasks, implements each
task, and writes `report.md` in the run worktree.

```bash
code-minions run prd-to-commit --input prd=./my-prd.md
```

This workflow uses `workspace.mode: git-worktree`, so the project must be a git
repo with at least one commit, and `my-prd.md` must be committed.

In the Web dashboard, the new-run form offers project-file suggestions for PRD
and file-path inputs. You can choose a local project file from the field's
dropdown suggestions or type a project-relative path manually.

### PRD format and delivery profiles

PRD workflows work best when the PRD includes a `Delivery Contract` section with
a `delivery_profile`. This tells code-minions what the final artifact must be,
instead of letting the model choose the easiest stack to make tests pass.

```yaml
delivery_profile:
  kind: web-service
  language: go
  framework: net/http
  build_system: go-mod
  test_command: go test ./...
  gate_strictness: balanced  # relaxed | balanced | strict
  required_files:
    - go.mod
    - "**/*.go"
  forbidden_product_languages:
    - python
    - javascript
    - typescript
```

The profile is carried from PRD parsing into task planning, implementation, and
the final product acceptance review. If a task produces files that violate the
profile, `implement-with-tdd` feeds blocking failures back into the repair loop.
Use `gate_strictness: balanced` for most runs. `relaxed` downgrades some
stack-specific hygiene checks to warnings while still running the real test
command; `strict` keeps those checks blocking. The final `report.md` also shows
the profile, warnings, and any blockers.

See [PRD template](prd-template.md) for full examples covering Swift macOS apps,
Go web services, Python CLIs, and React/Vite apps.

To reduce repeated LLM cost, `parse-prd` and `plan-tasks` cache successful
outputs in `.devflow/skill_cache.db` when the PRD content, prompts, and
configured provider/model are unchanged. The implementation step is not cached
because it writes code and commits into the run worktree.

## Land worktree results

Code-changing workflows do not modify your checked-out project directory
directly. They create an isolated git worktree at:

```text
.devflow/runs/<run-id>/worktree
```

That worktree is on a branch named:

```text
code-minions/<run-id>
```

Review the result first:

```bash
code-minions status <run-id>
cd .devflow/runs/<run-id>/worktree
git log --oneline --decorate --max-count=10
git diff main...HEAD --stat
sed -n '1,220p' report.md
```

Run the project's verification command from the worktree before merging. Use
the command from the delivery profile or the project conventions in
`AGENTS.md`, for example:

```bash
python -m pytest -q
go test ./...
xcodegen generate && xcodebuild test -scheme MacCalc
npm test
```

When the result is acceptable, merge the branch from your normal project
checkout. Start from a clean working tree so conflicts are easier to reason
about:

```bash
cd /path/to/your-project
git status --short
git switch main
git merge --no-ff code-minions/<run-id>
```

If the merge conflicts, resolve conflicts in the project checkout, then run the
same verification command again and commit the merge.

`report.md` is written in the run worktree for review. It may be untracked if it
was generated after the implementation commits. If you want to keep it in the
project history, commit it on the run branch before merging:

```bash
cd .devflow/runs/<run-id>/worktree
git add report.md
git commit -m "docs: add implementation report"
```

After the merge is complete and you no longer need the isolated worktree, clean
it up:

```bash
git worktree remove .devflow/runs/<run-id>/worktree
git branch -d code-minions/<run-id>
```

Avoid manually copying files from `.devflow/runs/<run-id>/worktree` over your
project root. Merging the branch preserves commit history, makes conflicts
explicit, and keeps `resume` / `status` behavior understandable.

## Inspect / recover

```bash
code-minions list-runs
code-minions status <run-id>
code-minions resume <run-id>     # pick up from a failed step
code-minions cancel <run-id>     # mark a pending/running run cancelled
```

`status` and `list-runs` show the LLM provider/model recorded when the run was
created. Older runs created before this field existed display `not recorded`.

See `docs/workflows.md` for the workflow YAML reference and `docs/skills.md` for writing custom skills.
