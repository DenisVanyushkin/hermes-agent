# Decisions

## 2026-05-17 — Canonical project cognition lives under `.hermes/projects/live-hermes/`

- **Decision:** Use `.hermes/projects/live-hermes/` as the authoritative documentation set for this repo-level project.
- **Rationale:** The user explicitly requires durable markdown documentation on disk so work survives resets, and `.hermes/projects/` cleanly separates persistent project cognition from ad hoc chat context.
- **Alternatives considered:**
  - Rely on chat history or model memory.
  - Store notes only in root-level `docs/` files.
- **Tradeoffs:**
  - Adds a maintenance obligation to keep docs current.
  - Requires discipline to update docs during work, not only at the end.
- **Consequences:**
  - Future sessions should read these files first to reconstruct state.
  - Implementation work in the repo should mirror important discoveries into the docs immediately.
