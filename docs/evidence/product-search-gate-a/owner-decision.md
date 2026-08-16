# Gate A owner decision

- Decision time: 2026-08-15 (Asia/Almaty)
- Decision: `bounded_additive_source`
- Evidence commit: `49070955c7d73a485e2d4bbc3acb2197e12a0f5f`
- Evidence manifest SHA-256: `a79f836d8d7fb34478ab3e537195ba66e2c625458fdcd52d4e54c1eedab1c458`
- Completed scheduled/manual runs: 4
- Recorded acquisition rows: 84
- Unique raw evidence artifacts: 39
- Observed productive families: DuckDuckGo and RemoteOK
- Observed capability gap: LinkedIn and HeadHunter were blocked because the dedicated experiment environment did not contain the Python Playwright runtime.

The owner explicitly ended the original observation window before seven full days and authorized Task 7 immediately. This decision does not claim that the original Gate A met its planned 7-day acceptance window and does not authorize Task 8.

The approved bounded capability is browser-native acquisition through the existing LinkedIn and HeadHunter public source interfaces, using cloned experiment browser profiles and the isolated experiment database/evidence/cache/temp paths. The capability may add a pinned dependency to the dedicated Product Search experiment environment. It must not modify protected scraper implementations or production source configuration.

Success requires both sources to execute with the pinned runtime and to record an explicit source/cell state plus evidence or a source-specific access failure. Slack credentials/calls, production Job Intel database/profile/cache writes, legacy Job Intel activation, and transition beyond repeated Gate A remain prohibited.

- `canonical_hold`: `keep_dormant_candidate`
- `runtime_hold`: `remain_masked_and_stop_program`
- Continuation permission: Task 7 and repeated Tasks 5-6 only
- Legacy state: masked and paused
- Temporary runner state at decision: timer disabled and service stopped before candidate replacement
- Original evidence retention: preserve the complete immutable experiment root at `/home/hermes/.hermes/job_intel/experiments/gate-a/49070955c7d73a485e2d4bbc3acb2197e12a0f5f`

## Snapshot-first and shared-profile override (2026-08-16)

The owner authorizes LinkedIn and HeadHunter acquisition to use the existing authenticated browser profiles at `/var/lib/browser-desktop/profiles/linkedin` and `/var/lib/browser-desktop/profiles/hh`. Each profile must have an experiment-local backup, legacy Job Intel and overlapping profile users must remain stopped, and the Gate A runner must hold exclusive execution ownership. Use of these recorded shared profiles is not contamination.

The owner also replaces the calendar-duration requirement for the current Gate A decision with a snapshot-first evaluation. One complete broad-source run plus a manual quality audit may close Gate A. Repeat attempts are required only for technically failed or materially ambiguous sources; the experiment does not wait seven days merely to repeat an already interpretable market snapshot.

The approved source inventory now includes every existing bounded public acquisition interface: LinkedIn, HeadHunter, DuckDuckGo, RemoteOK, Remotive, Greenhouse, Lever, Ashby, SmartRecruiters, Teamtailor, Personio, and Recruitee. ATS sources run as a global tenant snapshot, not as fabricated country-level independent coverage. Existing protected scraper implementations and the Product Search SoT remain unchanged.

## LinkedIn and HeadHunter runtime-repair exception (2026-08-16)

The owner explicitly directed the current execution to begin by fixing LinkedIn and HeadHunter. This separately authorizes the minimum protected runtime change required to make the already-approved browser-native source interfaces usable with the recorded working profiles. It supersedes the protected-path freeze only for the reviewed `job_intel/browser_worker.py` repair in canonical commit `65d60daae16093a9a7e34a11a159e2f789dd14dd` and its browser desktop/network bootstrap scripts and regression tests. It does not authorize changes to `job_intel/sources.py`, `job_intel/ats_sources.py`, `job_intel/browser_sourcing.py`, production source configuration, Slack access, the production Job Intel database, legacy activation, or a transition beyond Gate A.

The repair evidence is source-specific:

- HeadHunter completed a live isolated worker search as `healthy`: 18 extracted cards, one returned vacancy after filtering, with zero login-wall, auth-redirect, and anti-bot events.
- LinkedIn initially failed because Chromium CDP listened on loopback inside `ln-eg`, the experiment bootstrap opened `Default` instead of the authenticated `Profile 1`, and the existing WireGuard interface retained the obsolete numeric Firewalla endpoint `213.211.83.79` after DDNS moved to `178.89.248.242`.
- The bounded repair adds a management-veth-only CDP relay, passes the pinned experiment Python to profile resolution, makes browser recycling independent of Chromium argument order, and refreshes the WireGuard peer endpoint from host DNS on every existing-interface bootstrap.
- After repair, the WireGuard handshake refreshed, namespace DNS and LinkedIn HTTPS returned successfully, and a live isolated LinkedIn search completed as `healthy` with `session_ok`, six returned vacancies, and zero login-wall, auth-redirect, anti-bot, or attach-retry events.

The canonical repair commit is the new reviewed Product Search base. The feature scope baseline is repinned to it so the guard continues to reject any later unrecorded protected-path mutation.

## Superseding snapshot decision package (2026-08-16)

- Runtime commit: `65d60daae16093a9a7e34a11a159e2f789dd14dd`
- Manifest SHA-256: `6ecc500c291061a34c4482edb5c2a0d6c547993bea0d346ad306041dfa81df3d`
- Run ID: `gate-a-20260816T141344Z`
- Runner result: exit `0`, latency `3139.615234` seconds
- Source result: all 12 approved families executed; LinkedIn and HeadHunter are `observed`; DuckDuckGo is `observed_with_failures`; the other families are `observed`
- Corrected stages 1-3: 2,414 raw observations, 1,814 canonical current vacancies, and 1,314 with minimum evidence
- Corrected duplicates: 600
- Manual audit: 53 records; clearly noncanonical likely stage-4 range 15-35
- New-company candidates: 15 raw names, at least 14 after obvious spelling normalization

The immutable runner summary originally reported 2,401 canonical records and 13 duplicates. The audit found that LinkedIn `refId`/`trackingId` parameters and HeadHunter search parameters incorrectly participated in vacancy identity. Canonical commit `f46d2559d6e26c7981f351dc7be5574aad6ab6b8` fixes those URL rules. The corrected decision counts are a deterministic replay of the unchanged content-addressed raw evidence; the original summary and database remain preserved without alteration.

The audit also records non-blocking downstream gaps: cross-source identity reconciliation, weak DuckDuckGo result-page quality, missing SmartRecruiters descriptions, five observed zero-result interfaces, and a 52-minute sequential query plan. None is a remaining source-access failure, and none creates stage 4 or authorizes delivery.

Recommended superseding owner decision: `proceed`.

## Recorded superseding owner decision (2026-08-16)

- Exact owner approval: `Одобряю Gate A: proceed`
- Decision: `proceed`
- Gate A state: closed
- Continuation permission: Task 8 is authorized to begin.
- Product Search runtime: remains dormant.
- Legacy Job Intel state: remains masked.

This approval closes Gate A only. It does not enable Product Search runtime, restore legacy Job Intel, modify the immutable raw evidence package, or authorize any production delivery.
