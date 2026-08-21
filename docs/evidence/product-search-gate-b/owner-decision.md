---
schema_version: 1.0.0
gate: gate-b
owner_decision: pending
recommendation: request_revision
task_13_authorized: false
record_run_authorized: false
corpus_manifest_sha256: b1db802dbb3d0e2a18771f32da12b901b3bb9e941ae71b785a3c71142abf2d69
---

# Product Search Gate B owner decision

No live benchmark has run and the frontmatter remains deliberately pending.
The reviewed Task 8 v3 request is a pending checkpoint, not a launch-ready
approval request. It records the unchanged candidate identities and the new
prospective window described in the README and `benchmark-summary.json`:

- code candidate `21938df34b6a9976fddc27a80d008d4f60e76c6d`;
- runtime manifest `03278316997f454b722ef5a8a9f52ca752f88a8687eaa63fa3840931beaf4cee`;
- package manifest `6c3cbd6318e8e03ec58118103fd64ec7829fe5ed763837174546a08729f4953e`;
- launch identity `7dc08dc11e93fe97ad0f682da51a198f327c20691f2b8774cc0ef1c8351471b3`;
- prospective checkpoint manifest `964d29683b4e19c04b4b005ef04236bd3134933038b887d730deeca4ede9f53b`;
- prospective receipt content `899c2308a15a21305cca483e9ba71674ad2643cd0bf43cc2c76581c1f772724c`;
- unit `95ef9ce28e32dc96a469ff539c2dd897d75ccd921ce3837982b8f7599109272c`;
- provider-env contract file `b9d924123c8d5a75c1a4df048db1ef4b0c9e4da549ae6e86c562789deb20a6d2`;
- provider-env placeholder template `92d46371aa237956959b9a9b2657ca274bac09986556000fa76457e98695e856`;
- `openai/gpt-5-mini`, 48 calls, USD 0.01 per call and USD 0.48 total.

The prospective approval time is `2026-08-22T08:00:00Z`. The receipt model
permits only `08:00:00Z` through `08:30:00Z` (13:00–13:30 Asia/Almaty), but
this window is informational until the provider authority below is closed.
A late approval, late launch, missing provider-env hash, or any hash mismatch
voids the checkpoint.

The exact missing owner input is: authorize root to read only the existing
`OPENROUTER_API_KEY` value from `/home/hermes/.hermes/.env`, without printing
or logging it, and install it in `/etc/job-intel/gate-b-provider.env` under the
tracked Slack-blind contract. The file must contain only
`HERMES_HOME=/var/empty` followed by that one provider assignment, be a
root-owned single-link regular file with `root:root 0400`, and have its raw
live SHA-256 computed locally. No alternate value source is approved by this
checkpoint. Until that owner input exists and a launch-ready checkpoint binds
the resulting raw hash, neither this document nor the prospective hashes
authorize Task 9, env installation, materialization, receipt/unit installation,
provider/network calls, or spend.

Even after that gap is closed, approval would not authorize a different
candidate, a refreshed receipt, automatic recovery/retry, Slack, production
DB/outbox/profile writes, Task 13, persistence, shadow mode, delivery, or
restoration of legacy collectors.

The four legacy daily/weekly service/timer units remain masked and inactive.
Post-dispatch ambiguity is terminal, is charged conservatively at USD 0.01,
and cannot retry.  Recovery from `reserved` or `dispatched` state requires a
new exact owner-signed recovery request and a separate receipt; this initial
approval does not pre-approve recovery.

Until the owner answers explicitly, `record_run_authorized=false` remains the
source of truth.  Passing preflight and tests is not an owner decision, and the
overall Gate B recommendation remains `request_revision` until real benchmark,
offline replay, Decision v2 comparison, and human-audit evidence exist.
