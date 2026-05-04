---
name: create-jira-tickets
description: Create Jira Epic + Stories for a list of planned tasks.
allowed-tools: []
required-mcps:
  - jira
inputs:
  tasks: {type: array, required: true}
  project_key: {type: string, required: true}
  epic_title: {type: string, required: true}
outputs:
  tickets: {type: array}
  epic: {type: object}
  errors: {type: array}
llm:
  max_iterations: 30
  temperature: 0.1
---

# create-jira-tickets

Create a Jira Epic + Stories for each task, return the created ticket references.

## Inputs
- `tasks` (object[], required): output of plan-tasks.
- `project_key` (string, required): Jira project key (e.g. "ABC").
- `epic_title` (string, required): title for the umbrella epic.

## Instructions
- Use the `jira` MCP server's tools. Typical calls: `create_issue` with `issuetype`.
- Create ONE Epic first, capture its key, then create all stories with `customfield_*` = Epic link or
  parent reference (whichever the MCP supports).
- If a Jira API call returns an error, record it in `errors` rather than retrying blindly.
- Preserve the original planned task data in every returned ticket object. The
  downstream implementer consumes `tickets` directly, so each ticket must keep
  the task `id`, `title`, `description`, `acceptance_criteria`, `depends_on`,
  and any `delivery_profile` exactly as received.
- Add Jira metadata to the preserved task object instead of replacing the task
  with a thin reference. Use `task_id` for the original task id and include
  `ticket_key` plus `url` when available.

## Outputs
- `tickets` (object[]): original task objects enriched with `{task_id, ticket_key, url}`
- `epic` (object): `{key, url}`
- `errors` (string[])
