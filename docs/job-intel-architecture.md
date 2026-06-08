# Job Intel Architecture Notes

Last updated: 2026-06-06

## Purpose

This document captures the highest-signal architecture decisions recovered from prior Hermes and `job-intel` work.

## Status

These are mostly historical design decisions recovered from memory and rollout summaries. Treat them as strong prior decisions, not as current-live truth, until the code or host state is checked again.

## Core Direction

The approved strategic direction was:

- make the system company-first, not vacancy-first;
- keep strong executive-signal sources;
- demote weak generic boards;
- move ATS ingestion downstream behind company and hiring intelligence.

## Source Ranking

Historically approved core sources:

- `LinkedIn`
- `HeadHunter`

Historically demoted or removed from the core path:

- `RemoteOK`
- `Remotive`
- `DuckDuckGo` as a core source

Meaning:

- `LinkedIn` and `HeadHunter` were considered useful enough to remain part of the core path.
- `RemoteOK` and `Remotive` were considered weak-yield for this executive use case.
- `DuckDuckGo` was kept only as experimental discovery support, not as a primary engine.

## ATS Priority

Historically approved first ATS wave:

1. `Greenhouse`
2. `Lever`
3. `Ashby`
4. `Teamtailor`
5. `SmartRecruiters`
6. `Personio`

Interpretation:

- These ATSes were considered the most valuable first implementation wave.
- ATS is important, but not the top-of-funnel discovery layer in the approved direction.

## Company-First Flow

Historically intended system order:

1. Company Discovery
2. Hiring Signals
3. LinkedIn Company Intelligence
4. Product Growth Signals
5. ATS Ingestion
6. Vacancy Scoring and Notification

This order matters. It means ATS should validate and enrich demand after the system already has a company and signal view, not define the entire pipeline by itself.

## Company Discovery Backbone

Historically strongest backbone sources:

- `Crunchbase`
- `Dealroom`
- `CB Insights` as premium or conditional

Historically secondary or niche layers:

- `Wellfound`
- `TechCrunch`
- `Sifted`
- `YC Companies`
- `G2`
- `EU-Startups`
- `Product Hunt`

Interpretation:

- Backbone sources help maintain the tracked company universe.
- Secondary sources enrich or expand that universe, but should not dominate the architecture.

## Product Growth Signals

Historically useful growth indicators:

- `Similarweb`
- `Sensor Tower`
- `data.ai`
- `App Store`
- `Google Play`
- `BuiltWith`
- `Product Hunt` as a noisier early signal

Interpretation:

- These signals are most useful as prior evidence that a company may soon need senior product leadership.
- They should support prioritization, not replace direct hiring intelligence.

## LinkedIn Role In The Architecture

LinkedIn had two separate roles in the historical design:

- direct vacancy / executive opportunity source;
- company-intelligence source for hiring growth, headcount growth, leadership change, org change, and geographic expansion.

The second role became more important in the company-first design.

## Executive Search Firms

Historically judged poor direct extraction targets:

- `Spencer Stuart`
- `Russell Reynolds`
- `Korn Ferry`
- `Heidrick & Struggles`
- `Egon Zehnder`

Interpretation:

- These firms may still be useful for market context or indirect validation.
- They were not considered strong direct feeds for this extraction pipeline.

## Data Model Implications

The architecture assumes separation between:

- company-level intelligence;
- vacancy-level intelligence;
- product-growth priors;
- downstream notification logic.

This direction fits the already documented observability model where vacancy-level scoring, acceptance, rejection reasons, and source KPI reporting are explicit artifacts.

## Practical Consequences For Future Implementation

- Do not rebuild the system around generic job boards.
- Do not let ATS ingestion dominate discovery again.
- Prefer dynamic company discovery/watchlists over static target-company lists.
- Preserve a distinction between company score and vacancy score.
- Treat growth and hiring signals as upstream ranking evidence.

## Architecture Risks To Watch

- Regressing back to vacancy-first behavior because ATS data is easier to extract.
- Keeping weak boards in the core path out of habit rather than yield evidence.
- Overweighting interesting-but-noisy signals like `Product Hunt`.
- Treating executive search brands as direct extraction channels.

## Suggested Next Artifact

If this repo later grows beyond documentation, the next natural document would be an implementation plan that maps:

- source families;
- entity model;
- crawl cadence;
- scoring pipeline;
- delivery path;
- observability and KPI tables;
- host runtime requirements.

