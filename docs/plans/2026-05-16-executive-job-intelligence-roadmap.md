# Executive Job Intelligence System Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build a persistent autonomous executive job-search system for Denis Vanyushkin that searches daily, deduplicates results, scores opportunities, persists intelligence, and delivers Slack digests.

**Architecture:** A lightweight Python package will own normalized source configs, SQLite persistence, vacancy ingestion, heuristic evaluation, deduplication, candidate-memory enrichment, and digest generation. Hermes cron jobs will run the system on a daily cadence and deliver reports to Slack, while the SQLite store preserves history across restarts.

**Tech Stack:** Python 3.11, SQLite, YAML configs, Hermes cron scheduler, Slack delivery via cron output routing, requests/httpx, HTML parsing, standard library dataclasses.

---

## Stage Status

- [ ] Preflight / worktree hygiene
- [ ] MVP storage + configs
- [ ] Source ingestion + deduplication
- [ ] Scoring + filtering
- [ ] Slack digest + enrichment prompts
- [ ] Scheduling + operational wiring
- [ ] Verification + documentation

## How to use this file

Update checklist items as work lands. Keep the stage summary current so future runs know whether the system is ready, partially wired, or fully operational.

---

## Stage 0: Preflight / Worktree Hygiene

**Success criteria:** We know which untracked or unrelated edits exist and whether they interfere with the implementation.

- [ ] Inspect the existing worktree for unrelated changes.
- [ ] Classify any unrelated files as keep / isolate / discard.
- [ ] Confirm the job-intelligence implementation will not overwrite unrelated work.

## Stage 1: MVP Storage + Config Loading

**Success criteria:** The package can load the archive-derived configs and persist data in SQLite.

- [ ] Create the `job_intel` package.
- [ ] Add config loaders for candidate profile, search criteria, scoring, exclusions, and targets.
- [ ] Implement SQLite schema and migration bootstrap.
- [ ] Add tests for config loading and schema creation.

## Stage 2: Source Ingestion + Deduplication

**Success criteria:** The system can ingest vacancy records from multiple sources and suppress duplicates.

- [ ] Implement source adapters for HeadHunter and search-based LinkedIn/company-board discovery.
- [ ] Normalize vacancy records into one canonical model.
- [ ] Implement duplicate detection using company/title/location/description similarity and repost timing.
- [ ] Persist vacancy snapshots and duplicate links.
- [ ] Add tests for dedup matching and repost suppression.

## Stage 3: Scoring + Filtering

**Success criteria:** Each vacancy receives a stable score and low-quality matches are rejected automatically.

- [ ] Implement positive/negative scoring signals from the archive.
- [ ] Add geography, company-type, and title exclusions.
- [ ] Produce a human-readable evaluation record with concerns and recommendation.
- [ ] Add tests for score thresholds and rejection behavior.

## Stage 4: Slack Digest + Candidate Enrichment

**Success criteria:** Daily digests and high-value clarification questions can be produced cleanly.

- [ ] Format digest output for Slack delivery.
- [ ] Trigger immediate alerts for exceptional matches.
- [ ] Implement enrichment-gap detection for only high-signal missing fields.
- [ ] Persist candidate answers and use them in future scoring.
- [ ] Add tests for digest formatting and enrichment question selection.

## Stage 5: Scheduling + Operational Wiring

**Success criteria:** The system runs automatically on schedule and survives restarts.

- [ ] Add a CLI entrypoint for daily search/digest and enrichment review.
- [ ] Wire Hermes cron jobs for daily digest and periodic enrichment review.
- [ ] Confirm output delivery to the Slack channel.
- [ ] Add run logs / audit trail into SQLite.

## Stage 6: Verification + Documentation

**Success criteria:** The implementation is tested, documented, and ready for ongoing use.

- [ ] Run the test suite.
- [ ] Perform a sample run and inspect stored rows.
- [ ] Summarize architecture, storage, scheduling, and operating model.
- [ ] Leave the worktree clean or explicitly note any unrelated pre-existing files.

---

## Work log

- 2026-05-16: Created roadmap and began archive inspection.
