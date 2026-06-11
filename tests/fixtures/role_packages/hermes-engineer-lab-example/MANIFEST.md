# Hermes Engineer Lab

Example role package demonstrating engineering workflow patterns and env consent.

## Purpose

This is a **reference implementation** for role package authors. It demonstrates:

- Complete manifest with `env_requires` and optional token consent
- `observe_warn` boundary mode calibration
- Engineering-family skill structure
- Trigger naming conventions

## Installation

```bash
hermes role install ./examples/role-packages/hermes-engineer-lab \
  --accept-env SAMPLE_FAKE_TOKEN
```

## Role Details

| Field | Value |
|---|---|
| ID | `hermes_engineer_lab` |
| Family | `engineering_lab` |
| Boundary Mode | `observe_warn` |

## Triggers

**English:** `hermes engineer lab package`, `role package engineering lab`, `internal engineer package test`  
**Russian:** `инженерный тест пакета роли`, `тест инженерного role package`

## Skills

- **engineering-implementation-brief** — converts a high-level engineering goal into scope, non-goals, file map, test plan, validation commands, and rollback plan

## Environment Variables

| Name | Required | Purpose |
|---|---|---|
| `SAMPLE_FAKE_TOKEN` | No | Demonstrates optional env consent handling |

## Routing Note

Package routing triggers are validated but **not active** in MVP. Built-in routing remains authoritative.
