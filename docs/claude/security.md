# Security And Secrets

Use this file when a task involves credentials, external services, account
actions, file upload, publishing, or destructive commands.

## Hard Rules

- Never print or expose secrets:
  - `.env`
  - API keys
  - tokens
  - cookies
  - SSH/private keys
  - passwords
  - credential stores
- If a secret is relevant, report only that it exists and where, not its value.
- Do not upload, publish, push, email, or send files/data externally without the
  user explicitly asking for that exact operation.
- Do not log in, change, send from, or modify personal accounts unless the user
  explicitly asks.
- Be extra careful with Gmail, payments, billing, production deploys, deletion,
  and security settings.

## Local Dev Permission

The user has said this is a local dev machine and broad local actions are okay.
That does not override the hard rules above.

Allowed when useful and scoped:

- Read repo files.
- Edit project files.
- Run local tests.
- Restart local Docker services.
- Inspect local logs and health endpoints.

Ask or clearly report before:

- irreversible deletion
- production deploy
- public sharing/upload
- git push/commit containing user data
- account actions

## Error Handling

If blocked by permission, missing secret, or tool failure:

1. Report the exact reason.
2. Avoid repeating the same failed command.
3. Try a safer inspection path if possible.
4. Ask the user only for the missing secret/permission needed for the next step.
