---
name: homelab-wiki-enrichment-proposal
description: "Prepare a bounded, citation-preserving Homelab Wiki enrichment proposal after explicit user authorization or an approved schedule; never invent facts or bypass human review."
version: 1.0.0
author: DenisVanyushkin
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [Homelab, Wiki, Knowledge, Enrichment, DraftOnly]
---

# Homelab Wiki Enrichment Proposal

1. Require explicit user authorization or an approved schedule. Otherwise stop after read-only
   retrieval from `${HOMELAB_WIKI_PATH:-/srv/knowledge/current}`.
2. Use the client skill index-first and preserve immutable citations, freshness, contradictions, and
   evidence limitations.
3. Record only the knowledge domain, missing or stale fact classes, cited pages, cited source
   markers, suggested approved read-only collectors, and reason. Do not invent the answer.
4. Do not read private Hermes sessions, memories, messages, prompts, credentials, or user content.
5. Use a feature branch and existing repository validation gates for any authorized tracked change.
6. End tracked work in a draft PR plus its human-attention notification, then stop for human review.

Never merge, approve, mark ready, mutate a published vault, widen collector authority, or treat a
proposal as execution authority.
