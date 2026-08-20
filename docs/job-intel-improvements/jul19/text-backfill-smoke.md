# Vacancy text backfill — live smoke, 2026-08-08

Plan: `docs/superpowers/plans/2026-08-08-vacancy-text-backfill.md` (local repo).
Branch `feat/vacancy-text-backfill`, merged fast-forward into `local/customizations`
at `41f01c1dd7`. All numbers below were measured, not estimated.

## What was broken

`smartrecruiters`, `headhunter` and `teamtailor` read a listing endpoint that
carries no job description and never made a second request for details. The hole
stayed invisible because `_vacancy()` did `description=(description or title).strip() or title`
— a connector that never fetched text was indistinguishable from one that did.

## Title-only share per source

Threshold: `length(trim(description)) < 200 OR trim(description) = trim(title)`.

| source | total | title-only before | title-only after smoke |
|---|---|---|---|
| smartrecruiters | 5267 | 5267 (100.0%) | 5231 (99.3%) |
| headhunter | 215 | 214 (99.5%) | 214 (99.5%) |
| teamtailor | 67 | 67 (100.0%) | 67 (100.0%) |
| greenhouse | 4942 | 1 (0.0%) | — |
| ashby | 1080 | 1 (0.1%) | — |
| lever | 183 | 0 (0.0%) | — |

## Sweep runs

    budget 5   -> attempted=4   filled=0  failed=4  unavailable=0  more_eligible=True
    budget 120 -> attempted=105 filled=36 failed=67 unavailable=2  more_eligible=True

Persisted state after the runs:

| state | source | rows | description length |
|---|---|---|---|
| ok | smartrecruiters | 36 | 2241 – 9128 |
| failed | headhunter | 67 | 12 – 96 (unchanged) |
| unavailable | smartrecruiters | 2 | 29 – 32 (unchanged) |

`filled >= 1` — the plan's acceptance condition for this step — is met with 36.

## Historical finding superseded: anonymous HH API requests returned 403

This smoke report predates the official application-token migration. The
anonymous `403` was an authentication-policy response, not a DDoS-Guard IP
block: the same VPS could read public reference endpoints, and authenticated
`GET /vacancies/{id}` succeeded. The old browser workaround therefore carried
cookies for an auth problem rather than bypassing an IP restriction.

Current HeadHunter acquisition uses `job_intel.hh_api`, a cached application
token, the exact `hermes-job-intel/1.0 (denis@vanyushk.in)` User-Agent, bounded
429 retry/backoff, and explicit transient/permanent detail signals. The counts
below remain a historical snapshot of the pre-migration backfill and must not
be used as current API health evidence.

## Finding: the backlog is recoverable, but the named exemplar is not

Sample of 20 eligible smartrecruiters URLs spread across the table by rowid:

    HTTP 200: 19 (95%)   — all four jobAd sections present
    HTTP 404:  1 (5%)

So ~95% of the 5267-row backlog is still live. The plan's named exemplar,
`Wise Product Lead - Pricing` (22 characters), is **not** among them: its posting
`7440001249203` now returns 404. It closed between the spec's 15/15 liveness check
and today. It is one of the 5%, correctly recorded `unavailable`.

## Finding: `_priority` is defeated by the SQL window

`rows_needing_text` has no `ORDER BY` and applies `LIMIT budget + 1`, so SQLite
returns the lowest rowids and `select()` can only reorder *within* that window.
The first 78 eligible rows by rowid are all headhunter, which is why the budget-5
run never reached smartrecruiters at all.

The consequence that matters: Task 2's tested property — executive titles served
first under a constrained budget — does not hold end-to-end. The unit test passes
because it hands `select()` the whole candidate list; the sweep hands it an
arbitrary rowid-ordered slice.

## Target metric: `why_attractive` on shown roles

The criterion Stage 2 is gated on. Measured with the plan's own script.

| | shown roles | title-only (<100 ch) | why_attractive |
|---|---|---|---|
| baseline | 120 | 35 (29.2%) | 24/120 = 20.0% |
| after the sweep runs | 120 | 35 (29.2%) | 24/120 = 20.0% |
| after a targeted fill of the shown rows | 120 | 18 (15.0%) | 27/120 = 22.5% |

**The sweep runs did not move the metric at all — stated plainly, as the plan
requires.** The 36 rows they filled do not intersect the 120 shown roles, because
the sweep selects by rowid. This is the ordering finding above, with a price
attached: the rows that would move the gated metric are exactly the ones the
nightly sweep will not reach except by accident.

A targeted fill of the 33 eligible shown rows (30 attempted after the title
blocklist, 17 filled, 13 failed on the pre-migration hh 403) moved it: title-only among shown
roles 29.2% -> 15.0%, `why_attractive` 20.0% -> 22.5%. Conversion is about 18% —
17 rows of new text produced 3 new `why_attractive`.

Extrapolating honestly: the 18 remaining title-only shown roles are almost all
headhunter. Fixing the 403 would add perhaps 3 more, landing near 25% against a
criterion of 60%. **Text availability is not the binding constraint.** The
handover's estimated ceiling of ~40% is optimistic; `why_attractive` is fed only
by the 10 preference facts, and `strategy_ownership` and `team_build_mandate` —
the two most productive §7.2 facts — are not among them by construction.

## Scheduling

Job `06313170ebd3`, `job-intel-text-backfill-sweep`, cron `10 4 * * *`
(04:10 server time), script mode, no-agent, `deliver: local`.

Runs via `scripts/job_intel_text_backfill_sweep.sh`, which exists for two reasons
with production history:

- Script-mode cron sets `cwd` to `$HERMES_HOME/scripts`, whose parent holds a
  *data* directory named `job_intel`. Python treats it as a PEP 420 namespace
  package and shadows the real one. The wrapper probes for the marker
  `job_intel/__main__.py`. Three prior incidents.
- The sweep must run on the gateway venv, whose `pysqlite3` shim provides SQLite
  3.53.4. The system SQLite 3.45.1 carries the WAL-reset corruption bug, and the
  sweep performs up to 400 write transactions against a 647 MB WAL database with
  concurrent readers. The wrapper refuses to fall back to a bare `python3` and
  aborts if the interpreter reports < 3.51.3.

Verified from `~/.hermes/scripts` with `cwd` set there, as cron will invoke it:

    job_intel_text_backfill_sweep: sqlite 3.53.4 ok (interpreter=.../venv/bin/python)
    attempted=0 filled=0 failed=0 unavailable=0 more_eligible=True

Schedule headroom: `job-intel-daily.timer` is `OnCalendar=08:00 UTC` with
`RandomizedDelaySec=3600`, i.e. 10:00–11:00 server time — six hours after the
sweep. `nightly-gc` is 04:40; a 400-row sweep takes about 3.5 minutes.
`sqlite-autoupdate-weekly` is Sunday 03:00 and can run long; if it overlaps, the
sweep's per-row persistence isolation contains the locking and that night simply
fills little.

## Accepted, on the owner's ruling

Backfilled text changes a vacancy's `description_hash`, which makes
`_material_card_change` return True, which bypasses `already_sent_cooldown_active`
and re-sends the card with reason `description_changed_materially`. The owner
accepted this: those roles were shown with no text at all, so a re-scored card is
information rather than noise, and some will now fall below the notify threshold
and produce no card. Bound is roughly 35 roles, one-time. Pinned by
`test_backfilled_text_on_a_delivered_card_is_deliberately_renotified`.

## Follow-ups

1. Send hh.ru's required `HH-User-Agent` — recovers 214 rows currently `failed`.
2. Give `rows_needing_text` an `ORDER BY` and an attempt counter. That fixes the
   priority defeat, lets the sweep target the rows the gate is measured on, and
   removes the need for the smartrecruiters URL gate, which currently retires
   rows on a fetcher limitation rather than on evidence of absence.
3. Re-run the §7.2 measurements once the sweep has drained the backlog, and
   verify `assign_split` did not move rows between DEV and HOLDOUT rather than
   assuming its hashing held.
4. Decode HTML entities in the greenhouse/lever/ashby fetchers too — they carry
   their own duplicated tag-stripping regex and were not covered by the
   `_clean_html_text` fix.
5. Translate `jobs.smartrecruiters.com/{company}/{id}` to the API form. Zero rows
   affected today (all 5267 eligible rows carry API URLs, because the JSON-LD
   fallback that produces page URLs was dead until this branch fixed its regex);
   the exposure is future rows.
