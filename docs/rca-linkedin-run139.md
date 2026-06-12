# RCA: LinkedIn Tier-1 Acquisition Failure (run_id=139)

Date: 2026-05-29

## Executive Summary

- **Impact:** Production Tier-1 acquisition effectively ran on **HeadHunter only**. LinkedIn produced **0 hits** and **no extracted vacancies**.
- **Failure mode (observed):** LinkedIn source marked `error` with repeated `browser worker timed out after 240 seconds`.
- **Root cause category:** **PLAYWRIGHT** (Playwright CDP-attached persistent context becomes non-functional for page creation; `BrowserContext.new_page()` hangs indefinitely).
- **Verifier gap:** Runtime verification was green, but it did **not** validate the critical operation `context.new_page()` + navigation for LinkedIn.

## Scope

- Investigated: the last **production** LinkedIn failure: **`run_id=139`**.
- No new sources / ATS / architecture changes.

## Evidence (Primary Artifacts)

### 1) DB: run metadata shows LinkedIn worker timeout loop

From `runs.metadata_json.source_statuses.linkedin` for `run_id=139`:

- `status = error`
- `acquisition = browser-native`
- `hits = 0`
- `errors[]` contains 6 entries of: `browser worker timed out after 240 seconds`

### 2) System journal: daily run timeline + failure note

`journalctl -u job-intel-daily.service --since "2026-05-29 07:10:00 UTC" --until "2026-05-29 07:45:00 UTC"`

Key lines:

- Scheduler starts `job-intel-daily.service` at **2026-05-29 07:11:54Z**.
- The produced digest includes:
  - `Operator note: source issues detected — linkedin=error: browser worker timed out after 240 seconds`
  - `- linkedin: acquisition=browser-native, status=error, ... pages_fetched=0 ...`

### 3) Browser diagnostics during the failing run: stuck at `new_page()`

Directory: `/var/lib/job-intel/state/browser-diagnostics`

During `run_id=139` timeframe there are multiple LinkedIn attempts, each reaching CDP attach successfully, then stopping at `linkedin-auth-new-page-start`.

Example (first attempt):

- `20260529T071206.111767Z-cdp-after-connect.json`
- `20260529T071206.113049Z-cdp-after-contexts.json` (`context_count: 1`)
- `20260529T071206.114038Z-cdp-after-context-selected.json`
- `20260529T071206.139553Z-linkedin-auth-new-page-start.json` (requested_url: `https://www.linkedin.com/feed/`)

Critically, there is **no** corresponding `...linkedin-auth-new-page-opened.json` / `...goto-start.json` / `...goto-done.json` for these May 29 attempts.

The instrumentation in `BrowserSourceClient.fetch_html()` writes:

- `*-new-page-start` **before** `self._context.new_page()`
- `*-new-page-opened` **after** `self._context.new_page()` returns

Therefore the hang is at:

- `BrowserSourceClient.fetch_html()` -> `self._context.new_page()`

### 4) Code path where the 240s timeout is raised

`job_intel/sources.py`:

- `fetch_linkedin_vacancies()` -> `_browser_worker_payload("linkedin", ...)`
- `_browser_worker_payload()` runs the browser worker as a subprocess with `timeout=240`.
- On `subprocess.TimeoutExpired`, it raises:

```
SourceFetchError("browser worker timed out after 240 seconds")
```

So the error for run 139 is **a hard wall-clock timeout** of the browser worker process.

## Execution Timeline (run_id=139)

### Scheduler

- systemd timer triggers `job-intel-daily.service`.

### Daily runner

- `run_id=139` created at: **2026-05-29T07:11:54.859552+00:00**
- `run_id=139` finished at: **2026-05-29T07:42:17.694411+00:00**

### Source worker

- HeadHunter completes `ok` (24 found / 14 executive_detected).
- LinkedIn loops through multiple worker attempts.

### Browser attach

- LinkedIn acquisition uses `job_intel.browser_worker` with CDP attach to `http://127.0.0.1:9222`.
- Diagnostics show attach + context selection succeed.

### Browser session

- A persistent context is obtained (`context_count=1`).

### Search / extraction / scoring / persist

- These stages **do not occur** for LinkedIn because the worker does not reach HTML fetch.
- In KPI: LinkedIn has `found=0, executive_detected=0, scored=0, accepted=0, notified=0`.

### Point of failure

- The LinkedIn worker hangs during `context.new_page()`; the parent process kills it at **240 seconds**.
- This repeats until the LinkedIn source is marked `error` for the run.

## Root Cause Classification

- **Category:** `PLAYWRIGHT`
- **Root cause:** When attaching to the existing Chromium instance over CDP for the LinkedIn profile, `BrowserContext.new_page()` can hang indefinitely (no exception), causing the worker process to exceed the 240s subprocess timeout and be killed.

## Why Runtime Verification Is Green While LinkedIn Acquisition Is Broken

Runtime verification currently validates mostly **static contract** and **endpoint existence** (profiles/paths/CDP reachability), but not the functional contract:

- attach over CDP + obtain context
- successfully create a new page (`context.new_page()`)
- navigate to a LinkedIn URL and read HTML

So verification can be green even if the CDP-attached context is in a state where `new_page()` hangs.

## Recovery Plan

### Variant A: Fix current browser-native LinkedIn acquisition

Goal: make LinkedIn `browser-native` reliable by detecting and recovering from a hung CDP context.

1) **Add a specific watchdog around `context.new_page()`**
- In `BrowserSourceClient.fetch_html()`:
  - guard `self._context.new_page()` with a hard timeout (do not rely on the outer 240s subprocess kill).
  - on timeout, raise an exception whose message includes `BrowserContext.new_page` so browser_worker retry logic triggers.

2) **On that exception, recycle browser and retry**
- `browser_worker._with_browser_source()` already retries once and will recycle if exception message matches markers.

3) **Harden browser recycle**
- `_recycle_browser_desktop()` currently does not verify the kill succeeded; add verification that CDP stops responding, otherwise escalate kill / broaden PID matching.

Complexity: medium.
Risks:
- Using signals/threads incorrectly can destabilize the worker.
- Recycling can drop auth if profile is not truly persistent.

Time estimate: 0.5–1.5 days to implement + validate.

Expected result:
- LinkedIn failures become **fast-fail** with automatic recycle.
- Target `ok_rate` after fix: **0.7–0.9** (assuming no additional anti-bot/login-wall issues).

### Variant B: Add a fallback acquisition path (do not replace Tier-1 yet)

Goal: preserve some LinkedIn coverage even when CDP session is unstable.

Approaches:

1) **SERP-first discovery + selective browser fetch**
- Use search engines to discover `linkedin.com/jobs/view/...` URLs.
- Only open a small number of top URLs in the browser for extraction.

2) **Disable CDP attach for LinkedIn and launch a fresh persistent context per run**
- More deterministic at the Playwright layer but increases bot risk.

Complexity: medium-high.
Risks:
- SERP yields can be noisy/rate-limited.
- Fresh browser runs can increase anti-bot probability.

Time estimate: 1–3 days.

Expected result:
- Better continuity but potentially worse yield/quality.

## Decision Recommendation

- **Variant A first.** It targets the proven failure stage (`new_page()` hang) and stays within the approved source architecture.

## Acceptance Criteria

- In production, LinkedIn worker must:
  - attach over CDP
  - return from `new_page()` within bounded time
  - navigate to a LinkedIn URL

- KPI: LinkedIn `ok_rate` over 14 days (production-only runs) should rise from **0.0** to **>= 0.7** after rollout.
