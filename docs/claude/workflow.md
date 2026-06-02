# Workflow

Use this file when implementing or debugging behavior.

## Before Editing

1. Identify the exact behavior and the files likely involved.
2. Read the smallest relevant code section.
3. Check existing tests for the behavior.
4. Decide whether the change is backend, frontend, config, or all three.

## Editing Rules

- Follow existing function names and data shapes.
- Keep backward compatibility for existing DB roles/data where possible.
- Do not delete old user/session data unless explicitly asked.
- For UI fixes, update cache-busting query strings in `index.html`.
- For backend Python changes, recreate the container after tests if the live app
  must use the new code.

## Verification Ladder

Use the smallest check that proves the change:

1. Syntax:
   - `node --check src\langgraph_manager\static\app.js`
   - `python -m compileall src/langgraph_manager` inside the app image
2. Targeted tests:
   - `python -m pytest tests/test_chat_mode.py::<test_name> -q`
3. Full tests:
   - `python -m pytest tests -q`
4. Runtime check:
   - health endpoint
   - direct API call reproducing the user scenario
   - inspect DB rows only when needed

## Reporting

When reporting to the user:

- Say what failed and why, using the actual error.
- Say what you changed.
- Say what command/test proves it.
- If something was not verified, say so plainly.

## Token-Saving Practice

- Do not ask LangGraph model to reason before Claude when the user mentions
  Claude directly.
- Do not call a second model to summarize Claude output when direct output is
  already clean.
- Keep logs in artifacts/panels; chat should contain a human report.
