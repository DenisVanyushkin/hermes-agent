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
The reviewed Task 8 v3 request asks the owner to approve only the exact
materialization and one initial at-most-once launch described in the README and
`benchmark-summary.json`:

- code candidate `21938df34b6a9976fddc27a80d008d4f60e76c6d`;
- runtime manifest `03278316997f454b722ef5a8a9f52ca752f88a8687eaa63fa3840931beaf4cee`;
- package manifest `6c3cbd6318e8e03ec58118103fd64ec7829fe5ed763837174546a08729f4953e`;
- launch identity `7dc08dc11e93fe97ad0f682da51a198f327c20691f2b8774cc0ef1c8351471b3`;
- checkpoint manifest `80590c6457b74316107aa4f694816cef327f0e9478eb8175b0a1bd3f58de6e5d`;
- receipt content `f0080a3cdda9b35d36639a2d37883ee4ed44f890e6e8f3822a25c28f3081a35c`;
- unit `95ef9ce28e32dc96a469ff539c2dd897d75ccd921ce3837982b8f7599109272c`;
- `openai/gpt-5-mini`, 48 calls, USD 0.01 per call and USD 0.48 total.

The approval is effective at `2026-08-21T07:50:00Z` only if explicitly given
before then.  The receipt is valid only from `07:50:00Z` through `08:20:00Z`
(12:50–13:20 Asia/Almaty).  A late approval, late launch, or any hash mismatch
voids the checkpoint.  Approval authorizes Task 9 to reproduce these exact
prospective bytes, install the one root-owned receipt and temporary one-shot
unit, and make only the governed benchmark calls.  It does not authorize a
different candidate, a refreshed receipt, automatic recovery/retry, Slack,
production DB/outbox/profile writes, Task 13, persistence, shadow mode,
delivery, or restoration of legacy collectors.

The four legacy daily/weekly service/timer units remain masked and inactive.
Post-dispatch ambiguity is terminal, is charged conservatively at USD 0.01,
and cannot retry.  Recovery from `reserved` or `dispatched` state requires a
new exact owner-signed recovery request and a separate receipt; this initial
approval does not pre-approve recovery.

Until the owner answers explicitly, `record_run_authorized=false` remains the
source of truth.  Passing preflight and tests is not an owner decision, and the
overall Gate B recommendation remains `request_revision` until real benchmark,
offline replay, Decision v2 comparison, and human-audit evidence exist.
