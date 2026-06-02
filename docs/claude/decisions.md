# Decision Logging

Use this when a choice will matter beyond the current edit.

## When To Log

Create a decision note when choosing or changing:

- architecture pattern
- API contract
- DB schema
- executor routing behavior
- security policy
- long-lived config
- tool/plugin/MCP setup

Do not log tiny tactical edits.

## Where

Use:

`docs/decisions/YYYY-MM-DD-topic.md`

Create the folder if it does not exist.

## Format

```md
# Decision: <short title>

## Context
Why this came up.

## Decision
What we chose.

## Alternatives
What else was considered.

## Reasoning
Why this option wins for this project.

## Trade-offs
What we accept by choosing it.

## Follow-up
Checks or cleanup to revisit later.
```

Before making a similar decision, search `docs/decisions/` first.
