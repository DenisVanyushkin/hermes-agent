# Engineering Implementation Brief

Turns a high-level engineering goal into a structured implementation plan.

## Usage

`/engineering-implementation-brief <goal>`

## What It Produces

- **Scope** — what will be built or changed
- **Non-goals** — what is explicitly excluded
- **File map** — key files and modules involved
- **Test plan** — how to validate the work
- **Validation commands** — specific commands to run
- **Rollback plan** — how to undo if needed

## Examples

- `/engineering-implementation-brief add user authentication to API`
- `/engineering-implementation-brief refactor database connection pool`

## Environment Variables

Uses (optional): `SAMPLE_FAKE_TOKEN` — a test token for env consent demonstration only.

## Related Skills

- research-brief-builder
