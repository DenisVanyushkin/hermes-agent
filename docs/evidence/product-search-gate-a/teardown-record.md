# Product Search Gate A teardown record

- Run ID: `gate-a-20260816T141344Z`
- Runtime commit: `65d60daae16093a9a7e34a11a159e2f789dd14dd`
- Manifest SHA-256: `6ecc500c291061a34c4482edb5c2a0d6c547993bea0d346ad306041dfa81df3d`
- Experiment root: `/home/hermes/.hermes/job_intel/experiments/gate-a/65d60daae16093a9a7e34a11a159e2f789dd14dd`
- Runner exit: `0`
- Runtime latency: `3139.615234` seconds

## Evidence retention

- `summary.json` SHA-256: `3b9add39efeee87b041c4383e137d60b23710bd43aecedec82b3f0e8ce44ea99`
- `experiment.sqlite3` SHA-256: `08fefb5a0fdcaee7c59b5921b1b74291471e58405fd3299e8834c5a5a6c0d8ff`
- Raw evidence files: `2414`
- Raw evidence filename-set SHA-256: `a89f0fac067a58e2302628612d5cbef05975089832b286dac4d06a7de7337adc`
- Evidence payload hash mismatches: `0`
- SQLite `integrity_check`: `ok`
- SQLite foreign-key violations: `0`
- Browser diagnostics retained: `2624` files
- Working-profile backups retained inside the experiment root: LinkedIn `511M`, HeadHunter `557M`

## Runtime shutdown

- The one-shot acquisition process exited normally.
- LinkedIn Chromium, CDP relay, noVNC, Xvfb, and x11vnc processes were stopped.
- HeadHunter Chromium, noVNC, Xvfb, and x11vnc processes were stopped.
- A stop-script regex initially missed the HeadHunter x11vnc process. The process then exited, and regression-tested cleanup commit `ec913089f9043d969bedf1f6b031394cc0704510` fixes both profile-specific and all-profile patterns.
- Post-teardown process check found no managed LinkedIn or HeadHunter browser, CDP relay, x11vnc, Xvfb, or websockify process.
- No temporary Gate A service or timer remains installed (`not-found` / `inactive`).

## Protected state

- Production DB before and after: inode `3185007`, size `1095458816`, mtime epoch `1786421731`; no change.
- `job-intel-daily.service`: `masked`, `inactive`.
- `job-intel-daily.timer`: `masked`, `inactive`.
- `job-intel-weekly-kpi.service`: `masked`, `inactive`.
- `job-intel-weekly-kpi.timer`: `masked`, `inactive`.
- Slack credentials were unset at runner entry and rejected by the wrapper if present. No Slack call path was invoked.
- The owner-authorized shared LinkedIn and HeadHunter profiles were the only production browser profiles used. Their experiment-local backups are retained.
- Legacy Job Intel was not restored.

## Hold state

- Canonical code contains the browser cleanup fix and corrected vacancy URL canonicalizer.
- Product Search runtime remains dormant.
- Legacy Job Intel remains masked.
- The authoritative Gate A closure and Task 8 authorization are recorded in [`gate-closure.json`](gate-closure.json), without activating Product Search or restoring legacy Job Intel.
