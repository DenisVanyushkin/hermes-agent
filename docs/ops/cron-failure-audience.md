# Cron Failure-Delivery Audience

## Background

On 2026-07-28 and again on 2026-07-29, a cron job failed with

```
ImportError: cannot import name 'ReviewGateState' from 'hermes_cli.review_gate'
(/home/denis/.hermes/hermes-agent/hermes_cli/review_gate.py)
```

and the raw traceback text was delivered straight to a non-technical
recipient's WhatsApp — twice. The `audience` field and the policy in
`cron/scheduler.py` exist so a technical failure like this can never reach a
chat that isn't equipped to read it.

## What `audience` means

Every cron job has an effective audience of `"operator"` or `"end_user"`,
resolved by `resolve_cron_audience(job, cfg)` in `cron/scheduler.py`. The
valid values are `CRON_AUDIENCES = ("operator", "end_user")`
(`cron/jobs.py`).

- **`operator`** (the default — see below) — on failure, the job's own
  delivery target gets a short, redacted one-line summary of what went
  wrong (`_summarize_cron_failure_for_delivery`). No filesystem paths,
  no tracebacks, no exception-class names — but still a technical
  summary, meant for someone who can act on it.
- **`end_user`** — on failure, the job's own delivery target gets
  **nothing at all**. `plan_cron_failure_delivery()` returns `chat_text =
  None` for that branch; the pre-existing `should_deliver` guard in
  `run_one_job` already treats an empty `deliver_content` as "do not
  deliver". Instead, a withheld-failure alert is sent to the operator
  (see "Where withheld failures go" below).

**Absent `audience` means `operator`.** `create_job()` only writes the
`"audience"` key into `jobs.json` when it was explicitly set
(`cron/jobs.py`, around `create_job`'s "Only persist audience when
explicitly set" comment), specifically so every job that existed before
this feature shipped round-trips unchanged and keeps behaving exactly as
it always did.

Successful runs are unaffected either way — this policy only changes what
happens when a job's run fails.

## How to set `audience` on a job

### Check first: the CLI does not expose an `--audience` flag

`hermes_cli/subcommands/cron.py` defines the argument parsers for `cron
create` (alias `add`) and `cron edit`. Neither parser declares an
`--audience` option, and `cron_create()` / `cron_edit()` in
`hermes_cli/cron.py` don't pass one through to the `cronjob` tool either.
`create_job()` in `cron/jobs.py` *does* accept an `audience` keyword
argument (added for this feature), but nothing in the CLI or the
`cronjob` model tool surfaces it — so **there is currently no way to set
`audience` from `hermes cron create`, `hermes cron edit`, or the agent's
`cronjob` tool.**

The only ways to set it today are:

1. **The API** — call `cron.jobs.create_job(..., audience="end_user")`
   directly (e.g. from a script or a Python-level integration), or
2. **Editing `jobs.json` directly** — add or change the `"audience"` key
   on the job's record.

If you edit `jobs.json` directly, be precise: **the update path does not
normalize the value.** `update_job()` in `cron/jobs.py` merges your
update dict straight into the stored job (`{**job, **updates}`) without
ever calling `normalize_audience()`. `normalize_audience()` — which
lowercases, strips, and validates against `CRON_AUDIENCES` — is only
invoked by `create_job()`. So a value written by hand-editing
`jobs.json`, or via any tool that ends up calling `update_job` instead of
`create_job`, must be written **exactly** as `end_user` (lowercase, this
spelling). `resolve_cron_audience()` does tolerate a stored typo — it
degrades to `"operator"` rather than raising — but that means a typo
silently loses end-user protection rather than erroring loudly. Write it
correctly the first time.

Example direct edit of an existing job record in `~/.hermes/cron/jobs.json`:

```json
{
  "id": "150d115fe905",
  "...": "...",
  "audience": "end_user"
}
```

### An operator guide should not describe a flag that doesn't exist

If a future change adds `--audience` to `cron create`/`cron edit`, update
this section. Until then, do not tell anyone to run `hermes cron create
--audience end_user ...` — that flag is not there.

## The `cron.end_user_targets` safety net

Because most jobs will never have `audience` set explicitly, there's a
config-level safety net: `cron.end_user_targets` in `config.yaml`. Any
job whose *resolved delivery target* matches an entry in this list is
treated as `end_user`-facing even with no `audience` field at all.

```yaml
cron:
  end_user_targets:
    - "whatsapp:+77011102626"
```

Matching (`resolve_cron_audience()` in `cron/scheduler.py`) checks each of
the job's resolved delivery targets as both `"{platform}:{chat_id}"` and
bare `chat_id`, case-insensitively, against the configured list. A bare
string works exactly like a one-element list — `end_user_targets:
"whatsapp:+77011102626"` (no `-` list marker) is normalized internally to
a single-element list rather than being silently iterated character by
character, which would otherwise disable the net without any error.

**Precedence:** the explicit `audience` field on the job always wins. If
`audience` is set to `"operator"` or `"end_user"`, `resolve_cron_audience`
returns that value immediately and never even reads
`cron.end_user_targets`. Only when the job carries no valid explicit
`audience` does the function fall through to the config check.

This net exists so a *future* job pointed at Amina's WhatsApp — created
without anyone remembering to set `audience: end_user` — is still
protected, not just the two jobs flagged today.

## Where withheld failures go

When a job resolves to `end_user` and its run fails, nothing is sent to
that job's own delivery target. Instead, `_send_cron_operator_alert()` in
`cron/scheduler.py` sends an alert to `gateway.error_alerts.channel`
(already configured on hermes-home as `telegram:79564752`,
`config.yaml:307-310`). The alert is delivered with `wrap=False`, so it
is a plain message — not dressed up with the normal cron "Cronjob
Response: <job name>" header/footer, which would otherwise misleadingly
suggest replying "stop reminder <job name>" could stop a job that, from
the alert's synthetic identity (`cron-operator-alert`), doesn't exist.

The alert text has the shape:

```
⚠️ Cron '<job name>' failed and the failure was WITHHELD from the
end-user target (<platform>:<chat_id>) — nothing was delivered there.
Reason: <redacted, truncated detail>
Job id: <job id>. Full details saved in cron output.
```

`<redacted, truncated detail>` has gone through `_redact_technical_detail()`
— exception-class wrappers, traceback frames (including the indented
source-context line under each `File "..."` line), and filesystem paths
are stripped before this text is composed, so even the operator alert
doesn't leak a raw stack trace or install path. Full untouched output is
still available in the job's cron output directory and the gateway logs
for anyone who needs to actually debug it.

This alert delivery, and the `resolve_cron_audience`/
`plan_cron_failure_delivery` policy machinery generally, is best-effort
and never raises out into the run loop — a failure to send the alert
itself is logged (`logger.warning("cron operator alert failed: %s", exc)`)
but does not fail the cron run a second time over.

## Standing rule

**Any new cron job whose delivery target is Amina's WhatsApp
(`whatsapp:+77011102626`) must be created with `audience: end_user`.**
The `cron.end_user_targets` net covers that number too, so an unflagged
job pointed at her chat is still protected — but don't rely on the net
alone. Set the field explicitly; see the residual risk below for why.

## Known residual: malformed `cron:` config

If `~/.hermes/config.yaml` is edited such that the top-level `cron` key
stops being a mapping (for example, someone accidentally overwrites it
with a scalar or a list), then for a job that relies **solely** on the
`cron.end_user_targets` net — i.e. it has no explicit `audience` field —
`resolve_cron_audience()` cannot read `end_user_targets` at all,
`configured` becomes `[]`, and the function falls through to
`"operator"`. That job then delivers its redacted one-line failure
summary to its own (end-user) chat target on the next failure — the
pre-incident behaviour, for that one narrow case. This is currently
accepted as a known, narrow residual risk rather than something the code
guards against further (see the plan's Task 4 controller ruling).

**An explicitly flagged job (`"audience": "end_user"` written directly on
the job) is not affected by this**, because `resolve_cron_audience()`
checks the job's own `audience` field first and returns immediately when
it's valid — the config is never consulted for that job at all. This is
exactly why the explicit flag matters even for a job the config net
already covers: the net can be knocked out by a config-shape mistake, the
explicit flag can't be.

There is a second, separate safety valve worth knowing about: if
`plan_cron_failure_delivery()` itself raises for any reason (not the
scenario above, which degrades gracefully rather than raising), the
caller in `run_one_job` catches it and falls back to delivering the
historical one-line summary rather than to silence — the code
deliberately never lets a policy failure suppress an **operator**-audience
job's failure notification, and never lets an exception cause it to
guess "end_user" and withhold when it isn't sure.

## Reference: key names and defaults

| Name | File | Notes |
|---|---|---|
| `CRON_AUDIENCES` | `cron/jobs.py` | `("operator", "end_user")` |
| `normalize_audience(value)` | `cron/jobs.py` | Used only by `create_job()`, not `update_job()` |
| `create_job(..., audience=None)` | `cron/jobs.py` | Persists the key only when explicitly set |
| `resolve_cron_audience(job, cfg=None)` | `cron/scheduler.py` | Never raises; explicit field wins over config net |
| `plan_cron_failure_delivery(job, error, cfg=None)` | `cron/scheduler.py` | Returns `(chat_text, operator_alert_text)` |
| `_send_cron_operator_alert(alert_text, cfg=None, ...)` | `cron/scheduler.py` | Delivers to `gateway.error_alerts.channel`, `wrap=False`, best-effort |
| `cron.end_user_targets` | `config.yaml` | List or bare string of delivery targets treated as `end_user` |
| `gateway.error_alerts.channel` | `config.yaml` | Where withheld-failure alerts land |
