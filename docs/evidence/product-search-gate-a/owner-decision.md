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
