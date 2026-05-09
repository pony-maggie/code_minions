# PRD Template

Use this shape for `prd-to-commit` and `prd-to-pr`. The key section is
`Delivery Contract`: it tells code-minions what kind of project must be
delivered, not just what features it should contain.

````markdown
# <Product / Feature Name> PRD

## Goal

One sentence describing the user or business outcome.

## Delivery Contract

This section is required for reliable implementation runs.

```yaml
delivery_profile:
  stack_id: swift-xcodegen
  kind: native-macos-app
  language: swift
  framework: swiftui
  build_system: xcodegen
  test_command: xcodegen generate && xcodebuild test -scheme MacCalc
  gate_strictness: balanced  # relaxed | balanced | strict
  required_files:
    - project.yml
    - "**/*.swift"
    - "**/*App.swift"
  forbidden_product_languages:
    - python
    - javascript
    - typescript
```

Rules:
- Product code must follow the delivery profile.
- Prefer `stack_id` when the technology stack is known. Built-in stack IDs
  include `react-vite`, `swift-xcodegen`, `go-service`, `python-cli`, and
  `python-web`.
  Stack packs let code-minions apply focused install commands, validators,
  test harness rules, and repair hints without making one generic workflow
  handle every technology stack the same way.
- Tests must run with the declared test command or the closest built-in runner
  for the build system.
- `gate_strictness: balanced` is the recommended default. Use `relaxed` for
  exploratory demos where stack-specific hygiene checks should be warnings, or
  `strict` for production-style runs where those checks should block earlier.
- Do not use another language or framework for product code just to make tests
  easier.
- Do not add unverified third-party package URLs. Prefer standard-library or
  local implementation for the MVP.

## Users

- Primary user role
- Secondary user role

## Functional Requirements

### Feature 1: <Name>

Description of the feature.

Acceptance criteria:
- Given <state>, When <action>, Then <observable result>
- Given <state>, When <action>, Then <observable result>

### Feature 2: <Name>

Description of the feature.

Acceptance criteria:
- Given <state>, When <action>, Then <observable result>

## Non-Functional Requirements

- Performance:
- Security:
- Privacy:
- Compatibility:

## Constraints

- Required runtime or OS versions
- Required build tools
- Third-party dependency policy
- Out-of-scope items

## Open Questions

- Question 1
````

## Delivery Profile Examples

Swift macOS app:

```yaml
delivery_profile:
  stack_id: swift-xcodegen
  kind: native-macos-app
  language: swift
  framework: swiftui
  build_system: xcodegen
  test_command: xcodegen generate && xcodebuild test -scheme MacCalc
  required_files:
    - project.yml
    - "**/*.swift"
    - "**/*App.swift"
  forbidden_product_languages:
    - python
    - javascript
    - typescript
```

Go web service:

```yaml
delivery_profile:
  stack_id: go-service
  kind: web-service
  language: go
  framework: net/http
  build_system: go-mod
  test_command: go test ./...
  required_files:
    - go.mod
    - "**/*.go"
  forbidden_product_languages:
    - python
    - javascript
    - typescript
```

Python CLI:

```yaml
delivery_profile:
  stack_id: python-cli
  kind: cli
  language: python
  framework: typer
  build_system: python
  test_command: python -m pytest -q
  required_files:
    - pyproject.toml
    - "**/*.py"
  forbidden_product_languages: []
```

Python Web API:

```yaml
delivery_profile:
  stack_id: python-web
  kind: web-service
  language: python
  framework: fastapi
  build_system: python
  test_command: python -m pytest -q
  required_files:
    - pyproject.toml
    - src
    - tests
  forbidden_product_languages:
    - javascript
    - typescript
    - swift
    - go
```

React/Vite web app:

```yaml
delivery_profile:
  stack_id: react-vite
  kind: web-app
  language: typescript
  framework: react
  build_system: vite
  test_command: npm test
  gate_strictness: balanced
  required_files:
    - package.json
    - index.html
    - src
  forbidden_product_languages:
    - python
    - go
    - swift
```
