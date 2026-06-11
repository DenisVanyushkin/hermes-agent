# Hermes Researcher Lab

Example role package demonstrating a clean no-env research workflow installation.

## Purpose

This is a **reference implementation** for role package authors. It demonstrates:

- Minimal manifest with no `env_requires`
- `observe_warn` boundary mode
- Research-family skill structure
- Clean install/remove with no consent prompts

## Installation

```bash
hermes role install ./examples/role-packages/hermes-researcher-lab
```

## Role Details

| Field | Value |
|---|---|
| ID | `hermes_researcher_lab` |
| Family | `research_lab` |
| Boundary Mode | `observe_warn` |

## Triggers

**English:** `hermes researcher lab package`, `role package research lab`, `internal researcher package test`  
**Russian:** `исследовательский тест пакета роли`, `тест исследовательского role package`

## Skills

- **research-brief-builder** — creates a structured research brief with question, assumptions, evidence, confidence, open questions, and recommended next step

## Environment Variables

None required.

## Routing Note

Package routing triggers are validated but **not active** in MVP. Built-in routing remains authoritative.
