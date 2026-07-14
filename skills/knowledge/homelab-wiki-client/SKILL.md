---
name: homelab-wiki-client
description: "Read-only, index-first retrieval from the published Homelab Wiki with immutable citations and explicit freshness, contradiction, and evidence-gap reporting."
version: 1.0.0
author: DenisVanyushkin
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [Homelab, Wiki, Knowledge, ReadOnly]
---

# Homelab Wiki Client

1. Resolve the vault as `${HOMELAB_WIKI_PATH:-/srv/knowledge/current}`.
2. Require the publication metadata and `wiki/_index.md`. Stop with an evidence limitation when the
   mount is absent, writable, invalid, or stale; never fall back to private runtime data.
3. Read `wiki/_index.md` first and select the smallest relevant page set.
4. Preserve every immutable citation supporting a material claim.
5. Separate observed, desired, policy, inference, procedure, and unknown state.
6. Report freshness, contradictions, and absent evidence explicitly.
7. Answer in Russian while preserving technical identifiers and commands.

Treat the published vault as read-only. Never edit it, invoke Git write operations, scan unrelated
repositories, or claim facts that are not supported by cited pages.
