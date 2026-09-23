# Weekly shortlist issue #23: deployment and acceptance

Status: local implementation only. The VPS has not received these files, the
legacy seed marker has not been written there, no shortlist cron job has been
installed, and no live Slack or model call has been made by this implementation.

## Runtime contract

- The scheduler timezone must be `Europe/Berlin`. The logical release is the
  preceding completed Berlin ISO week. The three no-agent jobs are Monday
  09:10 (`job_intel_shortlist_weekly.py`), every half-hour
  (`job_intel_shortlist_poll_cron.py`), and at :05/:35
  (`job_intel_shortlist_report_cron.py`). The publisher, poller, and reporter
  therefore retain separate scheduler job IDs and run receipts.
- Install the `job-intel-shortlist` (`openpyxl`) and messaging (`slack-sdk`)
  extras in the interpreter used by the Hermes scheduler. Sync repo scripts
  into `~/.hermes/scripts/` using the repository's runtime sync script before
  registering jobs.
- The weekly output channel is `C0B4MM6D52A`. Only
  `JOB_INTEL_SHORTLIST_DELIVERY_DISABLED=0` enables outbound weekly delivery.
  This flag does not affect the daily Job Intel pipeline. Separately,
  `JOB_INTEL_SHORTLIST_SUMMARIES_ENABLED=1` enables paid model calls before
  the first freeze of a week. Without that flag, full vacancy descriptions
  remain in the XLSX and `summary_status=disabled` is explicit.
- The 20 audited legacy keys must be resolved and recorded by
  `job_intel_shortlist_backfill.py` before the first weekly build. The builder
  fails closed if the marker or a delivered release manifest is missing or
  ambiguous. The rejected sample uses a separate eight-week cooldown derived
  from delivered manifests.
- A retry of a week reads its original source artifact; it does not query the
  changing vacancy database again. The workbook, manifest, and receipt are
  SHA-256 bound. A returned workbook can change only `owner_decision` and
  `owner_note`.

## Deployment gate

Before any production mutation, inspect the exact repo commit, runtime
interpreter and installed extras, scheduler timezone, legacy 20-key audit,
channel membership and Slack scopes, and the separate delivery/model flags.
Then install the code and sync scripts, run the legacy seed, inspect
`python scripts/job_intel_shortlist_cron_install.py` (read-only), and only then
run it with `--apply`. The seed, cron registration, model calls, and Slack
publication are separate live gates; local tests authorize none of them.

## Acceptance evidence

The issue requires two consecutive **nonempty** releases from different
neighboring weeks, with a restart between them. Preserve the scheduler
receipts for all three stages; frozen census, partition, and projection
digests; root thread and both file IDs; verified owner UID, file hash, import
delta and no-op repoll; frozen ruleset and argument hash; report timestamp;
and adoption after an interrupted `prepared` or `anchored` operation. At least
one cycle must have a nonempty summary on every shortlist row and zero model
failures. Empty releases do not count toward these two cycles.
