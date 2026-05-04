# AGENTS.md — code_minions development harness

Instructions for anyone (human or agent) about to work on this repository.
If you are starting a new session, read this file first, then read
`PROGRESS.md` for the latest session handoff.

> Note: this is the **dev-side** AGENTS.md (how we build code_minions). It is
> different from the user-facing AGENTS.md that `code-minions init` generates
> inside a user's own project — that one lives in `examples/greeter/AGENTS.md`
> as a template sample.

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

Milestones shipped so far: **M1 → M5** (core platform) and **Phase C1 → C3**
(Web dashboard). The newest plan file under `plans/` tells you what's currently
active. Finish one milestone before starting the next — scope discipline
matters more than velocity.

## Session lifecycle (read / update these every session)

**At session start:**
1. Read this file (`AGENTS.md`)
2. Read `PROGRESS.md` — last session's handoff notes
3. Read the newest file in `docs/superpowers/plans/` — current milestone
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
