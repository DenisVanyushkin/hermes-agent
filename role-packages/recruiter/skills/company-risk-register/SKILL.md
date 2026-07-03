---
name: company-risk-register
description: Use when Hermes Recruiter must list explicit reasons not to engage with a company or role.
version: 1.0.0
author: Hermes Project
license: MIT
metadata:
  hermes:
    tags: [recruiter, company, risk, read-only]
    related_skills: [company-research, company-assessment, questions-to-ask]
---

# Company Risk Register

## Overview

Provide explicit, evidence-backed reasons not to engage with a company and role, so the decision is made with open eyes.

## When to Use

- When the `company_risk_register` module is requested.
- Alongside any company assessment for a high-priority opportunity.

## Required Inputs

- Company research claims with sources and dates.
- Optional: vacancy text for role-scope and relocation risks.

## Boundaries

- Every risk needs an evidence/signal reference; no speculative fear items without sources.
- Candidate-specific risks (e.g. relocation) require candidate context; otherwise phrase them as questions.

## Risk Categories

business, funding, reputation, culture, role-scope, manager/org, relocation, regulatory, compensation, career-narrative.

## Required Entry Shape

Each risk must include:

- `risk` — one-sentence statement
- `severity` — low / medium / high
- `confidence` — low / medium / high
- `evidence` — the signal and its source
- `mitigation` — mitigation or the question to ask before investing further

Example:

```json
{
  "risk": "Relocation to Singapore may be required",
  "severity": "high",
  "confidence": "high",
  "evidence": "Vacancy title and body mention relocation to Singapore",
  "mitigation": "Confirm relocation package, visa support, family logistics, and expected timeline before investing heavily."
}
```

## Expected Outputs

- `company_risk_register` module payload: risks list where each entry has risk, severity, confidence, evidence, mitigation.

## Failure Behavior

- If no evidence-backed risks can be identified, say so explicitly; do not pad the register with speculative items.
