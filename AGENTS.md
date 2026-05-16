# AGENTS.md — code_minions development harness

Instructions for anyone (human or agent) about to work on this repository.
If you are starting a new session, read this file first, then read
`PROGRESS.md` for the latest session handoff.

> Note: this is the **dev-side** AGENTS.md (how we build code_minions). It is
> different from the user-facing AGENTS.md that `code-minions init` generates
> inside a user's own project — that one lives in `examples/greeter/AGENTS.md`
> as a template sample.

## Product contract

`code_minions` is an AI-native software delivery workflow engine. Its job is to
turn a PRD into merge-ready code changes by modeling the full engineering loop:
requirements parsing, task breakdown, Jira-style coordination, AI implementation,
TDD verification, code review, acceptance reporting, and GitHub PR / GitLab MR
creation.

It is not a one-shot AI chat coding tool. Every run must behave like a real,
auditable engineering workflow with explicit inputs, an isolated workspace,
structured state, resumability, quality gates, traceable artifacts, and a path
into the normal git delivery chain.

When a run fails while exercising the product, fix `code_minions` only for
general workflow defects: language semantics, framework conventions, stack-pack
rules, reusable quality gates, deterministic testing, recovery behavior, or
delivery/reporting contracts. Do not add PRD-specific hacks, fixture-specific
patches, or product-code special cases that only satisfy one generated app.

Before changing shipped runtime to unblock a PRD run, state the non-business
Root Cause Class in system terms. If the explanation requires product-domain
words (game rules, colors, coordinates, business labels, PRD fixtures, or
feature-specific statuses), do not encode it as a builtin stabilizer or runtime
finding. Route it to the generated project, project-local memory, or the LLM
self-heal prompt instead. New runtime tests should be named after the system
class they protect, not after the product that exposed the failure.

## Tech stack

- **Language:** Python 3.11+ (also tested on 3.12 in CI)
- **CLI:** typer + rich
- **Web:** FastAPI + HTMX (vendored 2.0.2) + sse-starlette, Jinja2 templates
- **Storage:** SQLite via SQLAlchemy 2.x (`.devflow/runs.db`)
- **LLM:** LiteLLM (multi-provider: Anthropic / OpenAI / Gemini / DeepSeek / Ollama / ...)
- **MCP:** Anthropic official `mcp` SDK, stdio transport, pooled per run
- **Packaging:** hatchling, src-layout

## Repo layout

```
src/code_minions/
  cli/         typer entry points  → `code-minions <cmd>`
  engine/      orchestration core: Engine, DAGRunner, SkillRuntime,
               Workflow + Skill loaders, ContextAssembler, EventBus, hooks
  store/       SQLAlchemy schema + RunStore (SQLite)
  git/         WorktreeManager (one worktree per run)
  llm/         LiteLLM backend + provider config
  mcp/         MCP client pool + config loader
  web/         FastAPI app, routes, templates, static, SSE, BackgroundTasks
  builtin/     skills/ (8 built-ins) + workflows/ (defaults)
  logging.py   secret redaction util
  types.py     RunStatus / StepStatus enums

tests/           unit + e2e (pytest)
docs/            user docs + superpowers/ (specs, plans)
examples/greeter A minimal user-side sample project
```

## Dev commands

```bash
pip install -e '.[dev]'             # editable install with test deps
pytest                               # full test suite
pytest tests/unit/test_engine.py    # single file
ruff check .                         # lint
ruff check . --fix                   # autofix
pre-commit run -a                    # run all pre-commit hooks locally
```

## Verification gate (before committing / opening a PR)

All three MUST pass — no exceptions:

1. `pytest` is green (current coverage gate: **70%**, configured in `pyproject.toml`)
2. `ruff check .` is clean (selected rules: E, F, W, I, B, UP, SIM)
3. Relevant acceptance doc runs through manually when applicable:
   - Web changes → `docs/maintainers/phase-c-acceptance.md`
   - Core engine changes → `docs/maintainers/acceptance.md`

Don't claim "done" from confidence. Only passing output counts as evidence.

## How we plan work

We use the **superpowers** workflow: `brainstorming` → `writing-plans` →
`executing-plans`. Artifacts land under `docs/superpowers/`:

- `docs/superpowers/specs/` — design docs (why + what, long-lived)
- `docs/superpowers/plans/` — milestone execution plans (step-by-step, one per milestone)

This directory is local development memory and is ignored by Git; keep it if it
exists locally, but don't rely on it being present in a fresh public clone.

Milestones shipped so far: **M1 → M5** (core platform) and **Phase C1 → C3**
(Web dashboard). The plan with `status: active` in frontmatter tells you what's
currently active. Finish one milestone before starting the next — scope discipline
matters more than velocity.

## Session lifecycle (read / update these every session)

**At session start:**
1. Read this file (`AGENTS.md`)
2. Read `PROGRESS.md` — last session's handoff notes
3. If local `docs/superpowers/plans/` exists, read the plan whose frontmatter
   has `status: active` — current milestone
4. `git log --oneline -20` — recent commits

**At session end:**
1. Append an entry to `PROGRESS.md` (date, shipped, next, blockers)
2. Update `CHANGELOG.md` under `[Unreleased]` if anything user-visible changed
3. Commit with a message referencing the milestone (e.g. `feat(C2): ...`)

## Conventions

- **No new top-level deps without justification.** If adding one to
  `pyproject.toml`, note why in the PR / commit.
- **Secrets never logged.** Use `code_minions.logging.redact_secrets` on
  anything that might carry a key.
- **Skills under `builtin/`** are shipped; put experiments in the user's own
  project skill path, not here.
- **Web dashboard is localhost-only, no auth.** Never add features that assume
  multi-user without changing that contract first (future Phase C-B).
- **ContextAssembler injects the user's `AGENTS.md` into LLM skill prompts**
  (`engine/context.py`). When changing prompt structure, keep that behavior.
