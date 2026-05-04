---
name: parse-prd
description: Parse a PRD file into a structured representation.
allowed-tools:
  - Read
required-mcps: []
inputs:
  prd_file: {type: string, required: true}
  delivery_stack_id: {type: string, required: false}
outputs:
  goal: {type: string}
  delivery_profile: {type: object}
  users: {type: array}
  features: {type: array}
  non_functional: {type: object}
  constraints: {type: array}
  questions: {type: array}
llm:
  max_iterations: 10
  temperature: 0.1
policies:
  cache: true
---

# parse-prd

Read a PRD file and produce a structured representation the rest of the workflow can consume.

## Inputs
- `prd_file` (string, required): path relative to the project root. In git-worktree workflows, the file must be committed so the same relative path exists in the run worktree.
- `delivery_stack_id` (string, optional): workflow-level stack preset such as
  `react-vite`. When present, the workflow will enforce this stack on the
  structured `delivery_profile`.

## Instructions
1. Use the built-in `Read` tool to read `prd_file`.
2. Extract the following sections (infer when sections are unnamed):
   - `goal`: one-sentence business objective
   - `delivery_profile`: the required deliverable contract. Prefer an explicit
     `Delivery Contract` / `Delivery Profile` section if present. If absent,
     infer conservatively from the PRD. Use this shape:
     `{stack_id, kind, language, framework, build_system, test_command, required_files,
     forbidden_product_languages, gate_strictness}`. `stack_id` is optional but
     preferred when the stack is clear. Built-in stack IDs include `react-vite`,
     `swift-xcodegen`, `go-service`, and `python-cli`. `gate_strictness` is
     optional and must be one of `relaxed`, `balanced`, or `strict`; default to
     `balanced` unless the PRD explicitly asks for a more permissive or stricter
     gate. Examples: native macOS Swift app, Go web service, Python CLI, React
     web app. If the PRD does not constrain delivery shape, return `{}` rather
     than guessing.
   - `users`: target user roles
   - `features`: list of functional requirements; each item `{name, description, acceptance_criteria: [..]}`
   - `non_functional`: perf/security/availability constraints
   - `constraints`: out-of-scope items, dependencies, etc.
3. If anything critical is missing, include a `questions` array with clarifications. Do NOT invent facts.
4. Reply with a single JSON object matching the output schema.

## Outputs (reply JSON)
- `goal` (string)
- `delivery_profile` (object)
- `users` (string[])
- `features` (object[])
- `non_functional` (object)
- `constraints` (string[])
- `questions` (string[])
