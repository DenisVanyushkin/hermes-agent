---
name: recruiter-message
description: Use when Hermes Recruiter must draft a short channel-fitted message to a recruiter or hiring contact about a specific vacancy.
version: 1.0.0
author: Hermes Project
license: MIT
metadata:
  hermes:
    tags: [recruiter, outreach-draft, message, draft-only, read-only]
    related_skills: [positioning-and-evidence, package-reviewer, application-package-orchestrator]
---

# Recruiter Message

## Overview

Draft a short, high-signal conversation opener to a recruiter — not a mini cover letter. It must show why the candidate is relevant, why the role is interesting, what conversation would be useful, and that the candidate is easy to engage with. Draft only: the user sends it.

## When to Use

- When the user or orchestrator requests a recruiter message, LinkedIn note/DM, outreach email, referral message, or follow-up for a vacancy.
- Only after a `role_thesis_packet_v1` exists for this vacancy.

## Boundaries

- Draft only. Never send. No outbound messages of any kind.
- Do not: send a cover letter as a message; ask to "please consider me" without showing relevance; mix unrelated roles in one message; overstate connection to the company; say "I am the perfect fit"; raise compensation early unless the user wants that; mention attachments unless the channel supports them and the user intends to attach; assume recruiter gender, title, or relationship; fabricate recruiter names or claim prior contact that did not happen.
- Respect `forbidden_claims` from the thesis packet.

## Career Facts Contract

- Canonical candidate facts live in `~/.hermes/job_intel/career_facts/` (`career_facts.json`, `preferences.yaml`, gated by `manifest.yaml`).
- Verify the manifest gate (`approved: true` + sha256 match) before use; on failure return `FACTS_UNVERIFIED` and stop. Skip re-verification if the orchestrator passed a fresh result in the same run.
- Fit thesis and proof points come from the evidence bank only. Recruiter name only if provided; otherwise `[Name]` placeholder.

## Procedure

1. **Channel and length.** Adapt strictly:
   - LinkedIn connection note: 250–300 characters.
   - LinkedIn DM after connection: 700–1,200 characters.
   - Email: 100–180 words (plus a clear, non-gimmicky subject line, e.g. "Application for [Role] — [Candidate Name]").
   - Slack/internal referral: 80–140 words.
   - Follow-up (7–10 days after contact): 50–100 words, no guilt-tripping.
2. **Required elements.** Greeting (name if known); exact role reference; one-sentence fit thesis; one or two proof points from the evidence bank; simple ask; CV attachment mention only if actually intended.
3. **Tone.** Natural when read aloud; senior and calm; no hype.
4. **Definition of Done.** Fits the channel length; references the exact role; one clear fit thesis; ≥1 concrete proof point; simple ask; does not repeat the cover letter; sendable without editing except the optional recruiter name.

## Required Inputs

- `role_thesis_packet_v1` for this vacancy.
- Channel (LinkedIn note / LinkedIn DM / email / Slack referral / follow-up).
- Optional: recruiter name and context, language constraint.

## Expected Outputs

- Message draft (plus subject line for email), named `denis_vanyushkin_recruiter_message_[company]_[role]` (lowercase snake_case).
- Character/word count vs channel limit.
- Note on placeholders left for the user.

## Failure Behavior

- No thesis packet → `THESIS_REQUIRED`.
- Manifest gate fails → `FACTS_UNVERIFIED`.
- Channel unspecified → default to LinkedIn DM and report `CHANNEL_REQUIRED` as a note so the user can request another variant.
