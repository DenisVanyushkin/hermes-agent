# Job Intel query coverage (M2-Q)

Status: implementation and fixture evidence only; live experiment not run in
this slice (`runtime not verified`). This document does not claim market recall
and does not change selection, scoring, boundaries, or text enrichment.

## Authorities and scope

The role vocabulary below is copied from Product SoT §5.4 and aligned to the
eight `mandate_role_families` in `config/product_search/search_contract.v1.yaml`.
Titles are discovery vocabulary only. Mandate evaluation remains a separate
consumer and is not performed by query generation.

Implementation references at this revision: `job_intel/sources.py:788-797`
defines the role/context vocabularies; `job_intel/sources.py:876-934` parses
the opt-in experiment axes; `job_intel/sources.py:1005-1022` resolves
LinkedIn geography; `job_intel/sources.py:1026-1137` builds the LinkedIn plan;
and `job_intel/sources.py:1140-1189` builds the HeadHunter plan. The contract
declares the eight families at
`config/product_search/search_contract.v1.yaml:10-18`; its attempt rule
requires query, cell, family, timestamp, and result state at `:49-53`.
The source capability explicitly marks LinkedIn and HeadHunter geography as
`no_structured_country_cell` at
`config/product_search/source_capabilities.v1.yaml:48-72`.
The collector materializes planned and executed events at
`job_intel/cli.py:174-211`, adds experiment metadata at `:214-261`, merges
per-query trace data at `:264-302`, copies coverage into performance spans at
`:303-333`, and records LinkedIn and HeadHunter events at `:679-888`.

| Contract family | Product SoT §5.4 representative vocabulary | Query status |
| --- | --- | --- |
| `executive_product` | CPO; VP Product; Head of Product; VP Consumer Product | represented |
| `digital_business` | Chief Digital Officer; VP/Director Digital Business; Digital Business Director | represented |
| `customer_growth_commercial_hybrid` | Chief Growth Officer; Chief Customer Officer; Chief Commercial Officer; Consumer Business Director | represented |
| `product_business_unit` | Product Director; Digital Products Director; Business Unit Director; Platform Director | represented |
| `general_management` | GM Digital; GM Product; GM Market; Regional GM | represented |
| `growth_monetization` | VP/Head of Growth Product; Monetization Director; Lifecycle Director | represented |
| `transformation_builder` | Digital Transformation and Growth Director; Product Transformation Director; Product Organization Lead | represented |
| `hybrid_executive_exploration` | COO-adjacent digital role; Strategy and Product leader; Chief Commercial/Product hybrid | represented |

Before this slice, `sources.py` had five legacy role groups and four context
groups. They were not an eight-family mapping: some business, GM, platform, and
specialist terms were mixed together, and the live LinkedIn path emitted a
role group **and** a context group for every query. The after-plan has all eight
contract family identifiers explicitly; the role vocabulary itself does not add
an industry condition.

## Before and after query behavior

| Dimension | Before (`b15b50b46b`) | After (this slice) | Evidence class |
| --- | --- | --- | --- |
| LinkedIn mode | role + mandatory context | `role_only` by default; `role_plus_context` is explicit | static code + fixtures |
| LinkedIn role rotation | `step // len(cells)`; with 18 queries and 24 eligible cells the role/context pair stayed constant | role advances per query, with a full-cell pass offset; 18 queries represent all eight families | fixture test |
| Same-date runs | date-only seed, so two calls on one date repeat | UTC half-day slots; same-date role/cell pairs are distinct | fixture test |
| LinkedIn geography | verified target is passed beside query; unsupported cells skipped but not recorded in the run trace | requested `cell_id`, resolved location/geoId, and unsupported cells are recorded | config + composition test |
| HeadHunter mode | role + context + legacy geography keyword | `role_only` by default, geography keyword retained; context variant explicit | static code + fixtures |
| HeadHunter geography | mixed keyword family, no structured Search Contract cell | requested keyword family is recorded; resolved geography remains `unknown` | source capability + trace contract |
| Request volume | LinkedIn 18; HeadHunter `JOB_INTEL_HEADHUNTER_QUERY_LIMIT` (default 6) | unchanged | source code |
| Empty LinkedIn terms | the measured-empty guard exists | context variant continues to use the measured safe substitution; role-only emits no context term | fixture test |
| Bounded AB mode | no runnable in-process comparison | opt-in `role_context_ab`: 9 role-only and 9 role-context LinkedIn items, and 3+3 HeadHunter items, interleaved on identical pairs | fixture + composition tests |

The five unsupported LinkedIn cells in the pinned geography mapping are:
`cee`, `east_asia_other`, `genuinely_location_independent`,
`remaining_europe`, and `us_feasibility`. The 24 requested cells are the
remaining verified cells in `linkedin_geography.v1.yaml`. A skipped cell is a
coverage gap, not an empty market result.

For LinkedIn, each plan item has `requested_geography=cell_id` and
`resolved_geography=location or geoId`. For HeadHunter, the requested value is
one of the four legacy keyword families (`remote_europe`, `eu_gcc`,
`apac_core`, `apac_plus`); the source capability has no verified structured
country-cell mapping, so no resolved geography is claimed.

## Executed-cell trace contract

No database ledger table is introduced here. The daily collector records the
planned items and one event per attempted query in source trace and performance
span metadata. Planned items have `outcome=planned`; attempted items replace
that with the observed outcome:

`query_id`, query text, source, `cell_id` where available, role family, optional
context family, query mode, `experiment_branch`, requested geography, resolved
geography, `outcome` (`productive`, `empty`, or `error`), and `found_count`.

When the experiment is enabled, `experiment_branch` is `role_only` or
`role_context` on every planned and executed event. The query-coverage metadata
also records `name`, fixed `date`, fixed `rotation_slot`, `order`, and the
branch sequence. With the flag absent, events use `experiment_branch=default`
and the established plan builders are called unchanged.

LinkedIn writes this under `search_trace.executed_query_cells` and includes
`query_coverage` with requested, resolved, and unsupported cells. HeadHunter
writes the analogous list under `api_trace.executed_query_cells` and records the
explicit geography-resolution limitation. The same metadata is copied into
the source performance span. A `query_id` identifies an attempt; its count is
not a market-coverage measure.

A durable table remains a separate migration decision. If approved later, its
minimum key should be `(run_id, query_id)` with source, family, cell, requested
and resolved geography, mode, timestamp, outcome, result count, and error
fields. The current trace is intentionally additive and does not imply that a
planned event completed.

## Bounded live comparison for owner execution

This slice does not execute a provider call. The code now exposes the following
ready-to-run bounded experiment for a later owner-authorized run. The only
enabling flag is `JOB_INTEL_QUERY_EXPERIMENT`; the date and slot are required
fixed axes once it is enabled. All variables below are one-shot process
environment, not configuration-file changes.

```sh
export JOB_INTEL_QUERY_EXPERIMENT=role_context_ab
export JOB_INTEL_QUERY_EXPERIMENT_DATE=2026-09-15
export JOB_INTEL_QUERY_EXPERIMENT_ROTATION_SLOT=0
sudo -u hermes env \
  JOB_INTEL_QUERY_EXPERIMENT="$JOB_INTEL_QUERY_EXPERIMENT" \
  JOB_INTEL_QUERY_EXPERIMENT_DATE="$JOB_INTEL_QUERY_EXPERIMENT_DATE" \
  JOB_INTEL_QUERY_EXPERIMENT_ROTATION_SLOT="$JOB_INTEL_QUERY_EXPERIMENT_ROTATION_SLOT" \
  /home/hermes/.hermes/scripts/job_intel_daily.sh
```

The normal production invocation must omit all three experiment variables. In
that state the LinkedIn and HeadHunter plan sequences and request volume are
unchanged. `ROTATION_SLOT=0` and `ROTATION_SLOT=1` are the two explicit daily
slots; use the same frozen date and slot for both source plans in one run.

1. Freeze one UTC date, one `rotation_slot`, one source interface, and the
   current request budgets. The enabled plan itself produces 9 LinkedIn items
   in `role_only` and the same 9 `(cell_id, role_family)` items in
   `role_context`; for HeadHunter it produces 3 items per arm under the
   existing limit of 6. The order is
   `role_only, role_context, role_only, role_context, ...`. Do not execute
   unsupported LinkedIn cells and do not reintroduce `AI products` or another
   measured-empty term.
2. Use the generated item’s location/geoId for LinkedIn and the generated
   query text for HeadHunter. Keep the existing per-query page limits and
   public/auth policy unchanged. Record every trace event, including empty and
   error outcomes; a source-level `ok` must not replace per-query evidence.
3. Stop on preflight/CDP failure, exit-network failure, explicit provider
   challenge/rate limit, or any safety guard. Do not retry a blocked arm to
   manufacture a result. A zero result is an outcome to record unless a safety
   guard says the attempt was not productive.
4. Compare arms on unique canonical candidates, candidates with real usable
   text, M2-R reference controls matched by requisition/canonical URL, title-only
   noise, source outcome counts, and per-arm latency. Report counts separately
   for each geography, family, and `experiment_branch`. Do not run the
   evaluator as part of this comparison and do not infer that a larger raw
   count is a better result.

For the post-run read-only check, extract `search_trace` for LinkedIn and
`api_trace` for HeadHunter from the run record, then group
`executed_query_cells` by `experiment_branch`. Verify 9/9 and 3/3 planned
items per arm, identical pair keys, outcome counts, unique canonical URLs,
usable-text count, M2-R control matches, title-only count, and elapsed time.
Also record any unsupported or unresolved geography instead of treating it as
an empty result. A run with a source-level `ok` but a missing branch or a
partial plan is incomplete evidence.

The existing measured-empty LinkedIn terms are excluded from the context arm
by the already-proven substitution. Therefore this experiment compares
role-only against a safe role+context variant; it is not a new causal test of
the historical empty-term defect.

## Bounded daily plan

The production default keeps 18 LinkedIn attempts and the configured HeadHunter
budget (default 6). LinkedIn uses one eligible target cell per attempt and
advances role families per attempt, so a single 18-item plan includes all eight
families while moving through 18 of the 24 resolved cells. The two UTC
half-day slots produce different role/cell pairs on the same date. Consecutive
days move the geographic start by ordinal day. This is a plan, not proof that
all planned attempts ran.

HeadHunter receives six role-only queries per default run, retaining one of the
four legacy geography keyword families in each query. Its geography is not
resolved to a Search Contract cell; this limitation must remain visible in
reports. The two daily slots rotate role/keyword pairs without increasing the
budget.

## Baseline and limitations

The accepted run-489 baseline is retained for the live comparison: LinkedIn
planned search pages 23, pages fetched 43, found 560, 468 collected rows, and
368 companies; it was a public run with zero login-wall and anti-bot events.
Those are prior-run measurements, not a post-change runtime result. The
authoritative read-only source is
`/var/lib/job-intel/state/job_intel.sqlite3`, selected by
`JOB_INTEL_DB_PATH` in `/etc/job-intel/job-intel-shadow.env` (and the matching
`job-intel.env`). On that database, run 489 exists with status `ok`, its
`source_kpi_run` LinkedIn row reports `found_count=560`,
`executive_detected_count=278`, `accepted_count=25`, and the
`vacancy_observability` rows report 468 rows and 368 companies. The query-only
receipt used `PRAGMA query_only=1`.

There is a stale trap: the repository-local
`/home/hermes/.hermes/hermes-agent/.hermes/job_intel/job_intel.sqlite3` is a
separate old copy (six runs, latest run 6 in July, and no run 489). It must not
be used for baseline or post-run claims. The post-change query plan and trace
composition are verified by fixtures only; runtime remains `runtime not
verified`.

The source still has public-provider limitations: unsupported geography cells,
provider pagination/anti-bot behavior, and no structured HeadHunter geography
resolution. Query generation does not prove that a page was fetched, that a
candidate has a mandate, or that the market is fully covered.
