---
name: web-ui-acceptance-review
version: 0.1.0
entrypoint-script: scripts/run.py
---

# web-ui-acceptance-review

Runs deterministic browser acceptance checks for PRDs that deliver a Web UI.

Inputs:
- `structured_prd` (object, required): parsed PRD including or implying a delivery profile.
- `tasks` (array, optional): planned tasks.
- `implement_results` (array, optional): outputs from implement-with-tdd.

Output:
- `accepted` (boolean): false only when a supported browser acceptance scenario fails.
- `supported` (boolean): whether this skill can actively test the detected Web UI stack.
- `stack_id` (string): detected delivery stack.
- `scenarios` (array): browser/layout scenarios with `pass`, `fail`, `warn`, or `skip`.
- `artifacts` (object): generated screenshot/report paths.
