# Product Search Gate A snapshot report

## Outcome

The owner-authorized snapshot-first Gate A run completed successfully from the immutable runtime pinned to canonical commit `65d60daae16093a9a7e34a11a159e2f789dd14dd` and manifest SHA-256 `6ecc500c291061a34c4482edb5c2a0d6c547993bea0d346ad306041dfa81df3d`.

All 12 approved source families executed. LinkedIn and HeadHunter both completed as `observed` through the approved authenticated profiles. Every one of the 30 contract cells reached `qualified_results_found`. DuckDuckGo was `observed_with_failures`; the other 11 families were `observed`.

The run proves acquisition viability, but the raw runner summary is not the decision count. A manual quality audit found that LinkedIn and HeadHunter search-tracking parameters inflated URL identity. Canonical commit `f46d2559d6e26c7981f351dc7be5574aad6ab6b8` fixes that defect. Replaying the unchanged content-addressed raw evidence through the corrected canonicalizer produces the decision counts below.

## Corrected stages 1-3

| Measure | Count |
|---|---:|
| Raw observations | 2,414 |
| Canonical current vacancies | 1,814 |
| Minimum evidence sufficient | 1,314 |
| Unresolved for Decision v2 | 500 |
| Duplicate observations | 600 |

The immutable original summary remains preserved with its pre-fix counts (`2,414 / 2,401 / 1,901`, 13 duplicates). It is retained as run evidence but superseded for the Gate A decision by the deterministic corrected replay.

## Source results

| Source | State | Raw | Corrected unique | Duplicates | Note |
|---|---|---:|---:|---:|---|
| Ashby | `observed` | 436 | 436 | 0 | Global ATS snapshot |
| DuckDuckGo | `observed_with_failures` | 80 | 79 | 1 | Mostly result and aggregator pages; weak vacancy identity |
| Greenhouse | `observed` | 756 | 756 | 0 | Global ATS snapshot |
| HeadHunter | `observed` | 40 | 32 | 8 | Authenticated profile worked; search-query parameters removed |
| LinkedIn | `observed` | 586 | 7 | 579 | Authenticated profile worked; the same seven jobs repeated across the query matrix |
| RemoteOK | `observed` | 16 | 4 | 12 | Four identical snapshot results repeated across four cells |
| SmartRecruiters | `observed` | 500 | 500 | 0 | Titles and locations present; descriptions missing |
| Lever | `observed` | 0 | 0 | 0 | No rows from the configured tenant snapshot |
| Personio | `observed` | 0 | 0 | 0 | No rows from the configured tenant snapshot |
| Recruitee | `observed` | 0 | 0 | 0 | No rows from the configured tenant snapshot |
| Remotive | `observed` | 0 | 0 | 0 | Public interface returned no rows |
| Teamtailor | `observed` | 0 | 0 | 0 | No rows from the configured tenant snapshot |

The five observed zero-result sources are supply/registry outcomes, not access blocks. Gate A found no remaining LinkedIn or HeadHunter authentication/access gap.

## Manual quality audit

The named audit sample contained 53 deterministic, source-stratified records: every strong title signal from Ashby, Greenhouse, HeadHunter, LinkedIn, and RemoteOK; four deterministic expanded-title records from each productive structured source; five DuckDuckGo signal records; and one negative control from each major source.

The sample found:

- real executive-product candidates in LinkedIn, HeadHunter, Greenhouse, and Ashby;
- lower-seniority Product Owner, Senior Product Manager, and Principal Product Manager records that must not be promoted by title alone;
- product-design, product-marketing, and product-analytics false positives;
- DuckDuckGo result pages, person profiles, and service pages mixed with real vacancies;
- 500 SmartRecruiters records without descriptions;
- cross-source identity duplication, including the same DALEX role on LinkedIn and HeadHunter.

The clearly noncanonical likely stage-4 range is **15-35 vacancies**. The lower bound includes only high-confidence role-family candidates visible in the audited evidence; the upper bound includes plausible title-only and unresolved records that may survive Decision v2. This is not a verdict, delivery count, or persisted stage 4.

The run exposed 15 raw new-company names among title-signal records, with a lower normalized count of 14 after merging the obvious DALEX spelling duplicate. They remain company candidates only.

## Operational findings

- Runtime latency was 3,139.615 seconds (52 minutes 19.615 seconds) for 239 source queries. Acquisition works, but this sequential query plan is too slow for an interactive snapshot and should be parallelized, cached, or deduplicated before an operational daily/shadow runner.
- SQLite integrity and foreign-key checks passed; all 2,414 evidence payload hashes matched their content-addressed files.
- The production Job Intel database inode, size, and mtime were unchanged before and after the run.
- Slack credentials were absent and the runner's Slack-blind guard passed; no Slack code path was invoked.
- LinkedIn and HeadHunter working-profile writes were explicitly owner-authorized and backed up inside the experiment root. No other production profile use was observed.
- The host browser binary cache was reachable by the browser bootstrap, but no before-run mtime inventory was captured for every cache file. The report therefore does not claim a byte-for-byte zero-write cache proof.
- Legacy Job Intel services and timers remain masked and inactive. No temporary Gate A unit remains installed.

## Recommendation

The owner recorded the superseding Gate A decision as `proceed` with the exact approval: `Одобряю Gate A: proceed`.

Gate A is closed and Task 8 is authorized to begin. This authorization does not activate Product Search: its runtime remains dormant, and legacy Job Intel remains masked. Carry the following known gaps into later work without reopening acquisition viability: cross-source identity reconciliation, DuckDuckGo quality, SmartRecruiters detail enrichment, empty configured tenants, and query-plan latency.
