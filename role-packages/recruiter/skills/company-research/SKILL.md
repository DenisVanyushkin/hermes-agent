---
name: company-research
description: Use when Hermes Recruiter needs sourced public company research claims for a decision-support run.
version: 1.0.0
author: Hermes Project
license: MIT
metadata:
  hermes:
    tags: [recruiter, company, research, read-only]
    related_skills: [company-assessment, company-risk-register, vacancy-evaluation]
---

# Company Research

## Overview

Collect public, source-backed research claims about a target company so downstream company assessment and risk modules can rely on verifiable evidence instead of assumptions.

## When to Use

- Before producing a company assessment or company risk register.
- When Denis asks whether a company is worth engaging with.

## Required Inputs

- Company identity (name and, when available, website) or an approved vacancy source that names the company.
- Optional: prior research claims to refresh or extend.

## Boundaries

- Public sources only: company website, official blog / press releases, funding announcements, annual reports or regulatory filings, credible news, employee review sites, public interviews / podcasts / founder materials, product and developer documentation, customer case studies, layoff trackers or hiring signals.
- No unsourced rumors, no single-source reputation conclusions, no invented compensation data.
- No outbound messages, no CRM/job-intel writes, no form submissions.
- Browser automation only with explicit separate approval.

## Required Output

Produce `recruiter_company_research_packet_v1` claims. Every claim must include:

- `claim` — one specific statement
- `category` — e.g. business, funding, momentum, reputation, culture, product, compensation
- `source` and `source_type` (approved public source types only)
- `date_or_access_timestamp`
- `confidence` — low / medium / high
- `fact_vs_inference` — fact / recent_public_signal / inference / unknown
- `stale: true` when the signal is old enough to be unreliable

## Quality Rules

- Separate verified facts, recent public signals, inference, and unknowns.
- Reputation and culture conclusions require more than one source; a single anecdote must be labeled as such.
- Stale or uncertain signals must be marked, never presented as current.
- If research is unavailable or too weak, say so explicitly instead of padding.

## Expected Outputs

- `recruiter_company_research_packet_v1` claims list; each claim has claim, category, source, source_type, date_or_access_timestamp, confidence, fact_vs_inference, and optional stale flag.

## Failure Behavior

- If no credible public sources are found, report COMPANY_RESEARCH_UNAVAILABLE instead of inventing claims.
- If claims cannot carry required source/date/confidence fields, report COMPANY_RESEARCH_TOO_WEAK.
