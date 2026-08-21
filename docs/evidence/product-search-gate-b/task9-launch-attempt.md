# Task 9 one-shot launch evidence

Exactly one new systemd start was issued for the fresh Task 8 checkpoint at
`2026-08-21T13:38:18Z`. It failed closed with `226/NAMESPACE` before the
`ExecStartPre` command executed because
`/run/job-intel/gate-b-at-most-once`, declared in `ReadWritePaths`, did not
exist.

The pending receipt was not consumed, `ExecStart` was not reached, and provider
transport was not called. Provider outcome is `0/48` calls and `USD 0.00`
spend. There is no ledger, recording, summary, run process, or attempt lock.

The deployment unit, unconsumed receipt, and proven-empty `runs` parent were
removed. Package, immutable runtime, checkpoint, provider env, recovery keys,
and journal evidence were preserved. Retry and manual redispatch are forbidden
for this attempt.

Gate B recommendation: `request_revision`. The deliverable threshold was not
met because execution never began; terminal-unknown quality, manual-triage
accuracy, replay, Decision v2, and human-audit metrics are not computable.
Task 13, Gate C, Slack delivery, production persistence, and legacy restoration
remain unauthorized.
