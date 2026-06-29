# Job-Intel Source of Truth

Status: read-only audit output  
Audited: 2026-06-28  
Canonical host/repo: `ssh hermes-agent && cd /home/hermes/.hermes/hermes-agent`  
Local output path: `docs/job-intel-source-of-truth.md`

## 1. Executive Summary

`job-intel` is the Hermes-hosted executive job/vacancy intelligence system. It discovers vacancies and company hiring signals, deduplicates and scores opportunities against Denis's executive product/search profile, stores durable state in SQLite, and sends Slack-oriented summaries, alerts, health reports, and per-vacancy cards.

Systems touched:

- Source acquisition: LinkedIn, HeadHunter, target company career pages, Greenhouse, Lever, Ashby, Teamtailor, SmartRecruiters, Personio, Recruitee, DuckDuckGo, RemoteOK, Remotive.
- Browser runtime: `/var/lib/browser-desktop` with per-source Chromium profiles.
- Storage: canonical SQLite DB at `/var/lib/job-intel/state/job_intel.sqlite3`.
- Messaging: Slack via Hermes `send_message_tool` gateway or Slack webhook.
- Optional auth support: Gmail IMAP only for HeadHunter OTP/challenge handling.
- Monitoring/reporting: Prometheus exporter, Grafana dashboards, systemd timers, health reports.

Relationship to future Recruiter role:

- `job-intel` should remain the operational system for vacancy discovery, historical vacancy facts, CRM/opportunity status, Slack delivery history, feedback, and application-status state.
- Recruiter should read `job-intel` state and career/source artifacts, but must not mutate CRM, scheduled jobs, Slack/Gmail/Telegram, browser profiles, or DB rows in MVP.
- Generated Recruiter outputs remain drafts unless Denis explicitly accepts them.

Biggest risks/gaps:

- No stable read-only Recruiter API exists yet; direct SQLite reads are possible but couple Recruiter to schema.
- CRM write paths exist in services/reconciler/reaction handling and are easy to call accidentally without a dedicated read-only facade.
- Several docs still mention older DB locations; current host evidence confirms `/var/lib/job-intel/state/job_intel.sqlite3`.
- `python3 -m job_intel --help` fails outside the project venv because system Python lacks `pydantic`; use `venv/bin/python` or host wrapper.
- The local workspace was already dirty/untracked before this audit; clean-scope proof must account for pre-existing untracked files.

## 2. Audit Baseline

Canonical host baseline:

- `pwd`: `/home/hermes/.hermes/hermes-agent`
- Branch: `local/customizations`
- HEAD: `9682a3478026ddc08221192ded4ee7343fa98fcc`
- `git status --short --untracked-files=all`: `?? docs/hermes-role-package-runtime-slice-plan.md`
- Protected stash observed and not touched: `stash@{0}: On local/customizations: codex-preserve-db-persistence-before-controlled-manual-sot-slice`

Runtime path resolution with `/etc/job-intel/job-intel.env` sourced:

- runtime_user: `hermes`
- runtime_home: `/home/hermes`
- environment: `production`
- workdir: `/home/hermes/.hermes/hermes-agent`
- state_dir: `/var/lib/job-intel/state`
- db_path: `/var/lib/job-intel/state/job_intel.sqlite3`
- scripts_dir: `/home/hermes/.hermes/hermes-agent/scripts`

DB candidates observed:

- `/var/lib/job-intel/state/job_intel.sqlite3`: `389300224` bytes, `jobintel:jobintel`, mode `660`, mtime `2026-06-28 10:50:47 +0200`; current active DB by size, mtime, env, and runtime resolution.
- `/home/hermes/.hermes/job_intel/job_intel.sqlite3`: `544768` bytes, stale/alternate.
- `/home/hermes/.hermes/job_intel/job_intel.db`: `0` bytes, stale/alternate.
- `/home/hermes/.hermes/job_intel/state/job_intel.sqlite3`: `602112` bytes, alternate/test-like.

Read-only commands used:

- `git branch --show-current`
- `git rev-parse HEAD`
- `git status --short --untracked-files=all`
- `git stash list | sed -n '1,5p'`
- `find job_intel -maxdepth 2 -type f | sort`
- `rg --files tests job_intel scripts deploy docs config`
- `systemctl list-timers --all --no-pager | grep -E "job-intel|NEXT|UNIT"`
- `systemctl list-unit-files --no-pager | grep -E "job-intel"`
- `systemctl cat job-intel-*.service --no-pager`
- redacted env-key inspection of `/etc/job-intel/job-intel.env`
- SQLite `mode=ro` schema and count inspection.
- `venv/bin/python -m job_intel --help`

Commands deliberately not run:

- No `daily`, `alert`, `enrichment`, `market`, `strategic`, `health`, `weekly-kpi`, `send-test`, `feedback-event`, `retire-stale`, `bootstrap`, `metrics-exporter`, service restart, scheduled job trigger, DB migration, Slack send, Gmail call, or CRM reconcile/apply command was run.

## 3. Code/Module Map

| Path | Responsibility | Read/write behavior | Recruiter relevance | Confidence |
|---|---|---|---|---|
| `job_intel/cli.py` | Main CLI and orchestration for collection, scoring, reports, alerts, health, Slack delivery, feedback ingest. | Heavy writer for normal commands; help text read-only. | Read command map only; do not call mutating commands from Recruiter MVP. | high |
| `job_intel/store.py` | SQLite schema, migrations, CRUD, notification/feedback/report tables. | `connect(read_only=True)` exists; many methods write. | Best source for building a read-only facade. | high |
| `job_intel/runtime.py` | Runtime path/env resolution and runtime contract/provenance. | Reads env/files/git metadata. | Recruiter should reuse path resolution only if running on host. | high |
| `job_intel/models.py` | `Vacancy`, `Evaluation`, `VacancyResult` Pydantic models. | Pure model definitions. | Safe reusable type contract for vacancy facts and evaluations. | high |
| `job_intel/sources.py` | DuckDuckGo, RemoteOK, Remotive, LinkedIn/HH browser-worker wrappers, query generation. | Network/browser fetches; unsafe in audit except source reading. | Recruiter may reuse normalized vacancy shape, not live fetching in MVP. | high |
| `job_intel/browser_sourcing.py` | Playwright/browser extraction, login-wall/auth/anti-bot/session health, JSON-LD parsing. | Browser/network, diagnostics; may touch runtime artifacts. | Not for Recruiter MVP except as upstream producer of stored facts. | high |
| `job_intel/browser_worker.py` | Browser worker CLI for LinkedIn/HH/probe. | Starts/attaches browser runtime. | Do not invoke from Recruiter MVP. | high |
| `job_intel/ats_sources.py` | ATS fetchers for Greenhouse, Lever, Ashby, SmartRecruiters, Teamtailor, Personio, Recruitee. | HTTP/network fetches. | Upstream source producer; Recruiter should read stored results. | high |
| `job_intel/company_intel.py` | Target company monitoring, career URL discovery, company intelligence snapshots/events. | Network fetches and DB writes via store. | Good read source for company context after read API exists. | high |
| `job_intel/strategic.py` | Strategic signals/predictions from company and vacancy state. | Reads/writes strategic tables. | Useful context; treat predictions as derived, not fact. | high |
| `job_intel/dedup.py` | Canonical URL/key and duplicate detection. | Pure logic. | Safe to reuse for read-side canonical lookup. | high |
| `job_intel/evaluator.py` | Deterministic scoring v1/v2 and v3 shadow guardrails. | Pure scoring logic, config reads. | Recruiter can consult existing output; fresh evaluation should be explicit. | high |
| `job_intel/digest.py` | Slack/report formatting and rejection buckets. | Pure formatting. | Reports are not authoritative; useful examples. | high |
| `job_intel/observability.py` | Observability rows, rejection classifier, exporter. | Reads DB for exporter; `record_daily_observability` writes. | Useful health/source quality context, not authoritative vacancy facts. | high |
| `job_intel/enrichment.py` | Detects high-value candidate questions. | Pure helper. | Potential Recruiter input-gap helper. | medium |
| `job_intel/performance.py` | Run/source performance spans and compact reports. | Write/read helper around performance tables. | Read-only health context only. | medium |
| `job_intel/crm_constants.py` | Valid opportunity statuses and guarded terminal states. | Pure constants. | Must be reused by any approval-gated CRM action. | high |
| `job_intel/crm_repository.py` | Opportunity/events/tasks/artifacts/contacts/slack mapping repository. | Many writes; read methods use `connect(read_only=True)`. | Core source for opportunity/status read API. | high |
| `job_intel/crm_service.py` | CRM lifecycle and Slack reaction semantics. | Mutates status/events/tasks/artifacts. | Do not call mutating methods in Recruiter MVP. | high |
| `job_intel/crm_reconciler.py` | Backfill/reconcile mapping/feedback/CRM state with `dry_run` or `apply`. | `--apply` mutates; dry-run still reads current CRM. | Approval-gated only; not MVP. | high |
| `job_intel/idea_reaction_capture.py` | Slack idea reactions to Google Docs. | Can append to Docs unless dry-run. | Adjacent, not core job-intel CRM. Disable for Recruiter MVP. | medium |
| `job_intel/config.py` | YAML seed/config loading. | Reads seed files. | Safe read source for search preferences if privacy handled. | high |
| `deploy/systemd/job-intel-*.service/timer` | Host timer/service definitions. | Deployment config; running timers mutates DB/sends reports. | Observe only. | high |
| `scripts/job_intel_host_wrapper.sh` | Host wrapper: env, workdir, DB path, browser dirs, run type, venv. | Creates dirs/markers and runs commands. | Do not invoke for Recruiter MVP. | high |
| `deploy/docker/job-intel-exporter.py` | Prometheus exporter startup. | Long-running read exporter. | Metrics only. | high |
| `tests/job_intel/*.py` | Coverage for alert, browser acquisition, CRM, delivery, scoring, storage, source, health, strategic behavior. | Test-only. | Reuse tests when adding read facade. | high |

Primary test files observed:

- `tests/job_intel/test_cli_commands.py`
- `tests/job_intel/test_crm_repository.py`
- `tests/job_intel/test_crm_transitions.py`
- `tests/job_intel/test_crm_reconciler.py`
- `tests/job_intel/test_crm_reaction_routing.py`
- `tests/job_intel/test_crm_manual_url.py`
- `tests/job_intel/test_delivery.py`
- `tests/job_intel/test_scoring.py`
- `tests/job_intel/test_title_guardrails.py`
- `tests/job_intel/test_storage.py`
- `tests/job_intel/test_sources.py`
- `tests/job_intel/test_browser_acquisition.py`
- `tests/job_intel/test_health_report.py`
- `tests/job_intel/test_strategic.py`

## 4. Runtime and Scheduled Jobs

Current systemd timer state:

- Enabled active timers: `job-intel-health.timer`, `job-intel-daily.timer`, `job-intel-weekly-kpi.timer`.
- Disabled installed timers: `job-intel-alert.timer`, `job-intel-enrichment.timer`, `job-intel-market.timer`, `job-intel-strategic.timer`.
- Enabled installed services but not directly active as timers: alert/enrichment/market/strategic services are installed; timers disabled.

Current next/last schedule evidence:

- `job-intel-health.timer`: next `2026-06-29 02:15:00 CEST`; last `2026-06-28 02:15:00 CEST`.
- `job-intel-weekly-kpi.timer`: next `2026-06-29 09:04:25 CEST`; last `2026-06-22 09:10:45 CEST`.
- `job-intel-daily.timer`: next `2026-06-29 10:20:25 CEST`; last `2026-06-28 10:36:22 CEST`.

Packaged schedules:

- `job-intel-daily.timer`: `OnCalendar=*-*-* 09,17:00/5`
- `job-intel-health.timer`: `OnCalendar=*-*-* 02:15`
- `job-intel-weekly-kpi.timer`: host unit currently weekly KPI; packaged timer observed in repo separately.
- `job-intel-alert.timer`: `OnCalendar=hourly` but disabled on host.
- `job-intel-enrichment.timer`: `OnCalendar=*-*-* 03:20` but disabled on host.
- `job-intel-market.timer`: `OnCalendar=*-*-* 08:20` but disabled on host.
- `job-intel-strategic.timer`: `OnCalendar=Sun 11:30` but disabled on host.

Systemd service pattern:

- User/group: `hermes:hermes`.
- Env file: `/etc/job-intel/job-intel.env`.
- Workdir: `/home/hermes/.hermes/hermes-agent`.
- Wrapper: `/home/hermes/.hermes/hermes-agent/scripts/job_intel_host_wrapper.sh <command>`.
- Hardening: `NoNewPrivileges=yes`, `PrivateTmp=yes`, `ProtectSystem=strict`, `ProtectHome=read-only`.
- Writable paths: `/var/lib/job-intel`, `/var/log/job-intel`, `/etc/job-intel`, `/var/lib/browser-desktop`.

Runtime env keys observed, values redacted:

- `HERMES_HOME`, `HERMES_HOME_MODE`, `HERMES_SKIP_CHMOD`
- `JOB_INTEL_DB_PATH`, `JOB_INTEL_STATE_DIR`, `JOB_INTEL_WORKDIR`, `JOB_INTEL_ENVIRONMENT`, `JOB_INTEL_RUN_TYPE`
- `JOB_INTEL_ENABLED_SOURCES`
- `JOB_INTEL_BROWSER_PROFILE_DIR`, `JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN`, `JOB_INTEL_BROWSER_PROFILE_DIR_HH`, `JOB_INTEL_BROWSER_PROFILE_DIR_COMPANY_CAREER`, `JOB_INTEL_BROWSER_RUNTIME_DIR`
- `JOB_INTEL_ATS_SEEDS_ASHBY`, `JOB_INTEL_ATS_SEEDS_GREENHOUSE`
- `JOB_INTEL_SLACK_WEBHOOK_URL`
- `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_HOME_CHANNEL`
- `SCORING_MODEL_VERSION`
- `PLAYWRIGHT_BROWSERS_PATH`, `XDG_CACHE_HOME`

CLI help observed through venv:

```text
job-intel {bootstrap,daily,alert,enrichment,market,strategic,health,weekly-kpi,metrics-exporter,doctor,browser-health,send-test,feedback-event,retire-stale}
```

Safety classification:

- Safe to observe: git status/head, systemctl list/cat/status, redacted env-key listing, SQLite `mode=ro` schema/count queries.
- Unsafe without explicit approval: `bootstrap`, `daily`, `alert`, `enrichment`, `market`, `strategic`, `health`, `weekly-kpi`, `metrics-exporter`, `send-test`, `feedback-event`, `retire-stale`, browser-worker commands, wrapper commands, systemctl start/restart, migrations, CRM reconciler `apply`.
- Caution: `doctor` and `browser-health` may inspect browser/source state and should remain disabled in a strict read-only audit unless Denis approves.

## 5. Database and Storage

Database technology: SQLite with WAL configured in `job_intel/store.py` schema. Canonical DB path: `/var/lib/job-intel/state/job_intel.sqlite3`.

Migration mechanism:

- `JobIntelStore.bootstrap()` runs `SCHEMA` and `_ensure_column` migrations.
- Schema and migrations live in `job_intel/store.py`.
- `JobIntelStore.connect(read_only=True)` exists and opens DB read-only for selected repository methods.

Observed table counts:

- `runs`: 358
- `vacancies`: 6475
- `vacancy_evaluations`: 87881
- `vacancy_observability`: 65857
- `vacancy_rejection_events`: 807369
- `vacancy_rejection_summary`: 65809
- `vacancy_scoring_shadow`: 70451
- `notifications`: 836
- `vacancy_slack_messages`: 86
- `vacancy_feedback`: 35
- `vacancy_feedback_state`: 25
- `opportunities`: 32
- `opportunity_events`: 81
- `opportunity_tasks`: 3
- `opportunity_artifacts`: 0
- `opportunity_contacts`: 0
- `slack_message_map`: 55
- `company_intelligence`: 22
- `company_intelligence_events`: 5493
- `registry_company_runs`: 322
- `strategic_signals`: 1915
- `strategic_predictions`: 486
- `source_kpi_run`: 758
- `production_observation_daily`: 34
- `job_intel_performance_spans`: 231
- `duplicate_links`: 13549
- `candidate_memory`: 0
- `enrichment_questions`: 0
- `user_feedback_opportunities`: 654
- `vacancy_card_decisions`: 408

### Tables

Table: `runs`  
Purpose: run lifecycle and provenance.  
Source-of-truth status: authoritative for job-intel run history.  
Important columns: `id`, `mode`, `started_at`, `finished_at`, `status`, `notes`, `metadata_json`, `provenance_json`, `run_type`.  
Writers: `JobIntelStore.start_run`, `finish_run`, CLI run commands.  
Readers: health/reporting/observability.  
Recruiter relevance: run provenance and recency checks.  
Risks/gaps: metadata is JSON and command-specific.

Table: `vacancies`  
Purpose: canonical vacancy inventory.  
Source-of-truth status: authoritative for discovered vacancy facts as captured by job-intel.  
Important columns: `vacancy_key`, `source`, `source_id`, `company`, `title`, `location`, `url`, `description`, `salary`, `metadata_json`, `first_seen_at`, `last_seen_at`, `status`.  
Writers: ingestion/dedup pipeline.  
Readers: scoring, CRM, reports, Recruiter read-only lookup.  
Recruiter relevance: primary vacancy text/fact source.  
Risks/gaps: vacancy text may be stale or partial; Recruiter should expose provenance/age.

Table: `vacancy_evaluations`  
Purpose: stored deterministic evaluation result per vacancy/run.  
Source-of-truth status: derived.  
Important columns: `vacancy_key`, `run_id`, `score`, `tier`, `recommendation`, `salary_tier`, JSON signal/reason fields.  
Writers: scoring pipeline.  
Readers: reports, alert selection.  
Recruiter relevance: useful prior evaluation.  
Risks/gaps: not a substitute for fresh Recruiter judgment when job text changed.

Table: `duplicate_links`  
Purpose: duplicate-to-canonical vacancy relationships.  
Source-of-truth status: derived.  
Important columns: `canonical_vacancy_key`, `duplicate_vacancy_key`, `reason`, `similarity`, `created_at`.  
Writers: dedup pipeline.  
Readers: reports/analytics.  
Recruiter relevance: avoid repeated evaluations.  
Risks/gaps: similarity false positives possible.

Table: `vacancy_observability`  
Purpose: normalized per-run vacancy observability/funnel row.  
Source-of-truth status: derived/reporting.  
Important columns: `run_id`, `vacancy_key`, `source`, `source_key`, `role_bucket`, `geo_bucket`, `industry_bucket`, `accepted`, `notified`, `score`, `score_band`, `confidence`, `canonical_url`, `company`, `title`, `recommendation`.  
Writers: `record_daily_observability`.  
Readers: Grafana/exporter/KPI reports.  
Recruiter relevance: source/quality context only.  
Risks/gaps: derived from run-time scoring; not canonical application status.

Table: `vacancy_rejection_events`  
Purpose: per-reason rejection analytics.  
Source-of-truth status: derived.  
Important columns: `run_id`, `vacancy_key`, `rejection_reason`, `reason_type`, `severity`.  
Writers: observability.  
Readers: rejection dashboards.  
Recruiter relevance: helps explain skip decisions.  
Risks/gaps: taxonomy should be shown as classifier output, not fact.

Table: `vacancy_rejection_summary`  
Purpose: per-vacancy rejection aggregate.  
Source-of-truth status: derived.  
Important columns: `run_id`, `vacancy_key`, `score_band`, `recommendation`, blocker/unknown/warning counts and top reasons.  
Writers: observability.  
Readers: reports/dashboards.  
Recruiter relevance: compact prior rationale.  
Risks/gaps: top reason can hide nuance.

Table: `candidate_memory`  
Purpose: candidate facts/memory store.  
Source-of-truth status: unknown; empty in live DB.  
Important columns: `key`, `value`, `source`, `confidence`, `updated_at`.  
Writers: currently no active evidence from counts.  
Readers: unknown.  
Recruiter relevance: do not depend on it yet.  
Risks/gaps: empty and unclear authority.

Table: `enrichment_questions`  
Purpose: candidate/job clarification questions.  
Source-of-truth status: draft/work queue; empty in live DB.  
Important columns: `question`, `answer`, `status`, `created_at`, `answered_at`.  
Writers: enrichment.  
Readers: reports.  
Recruiter relevance: useful missing-input pattern.  
Risks/gaps: no live rows; not a stable SoT.

Table: `notifications`  
Purpose: delivery attempts and message bodies.  
Source-of-truth status: authoritative for job-intel delivery attempts, report for content.  
Important columns: `run_id`, `vacancy_id`, `channel`, `message_type`, `notification_kind`, `card_key`, `delivery_status`, `delivery_error`, `sent_at`, `body`, `payload_json`.  
Writers: report/alert/delivery commands.  
Readers: health, audit, card suppression.  
Recruiter relevance: delivery history and Slack trace.  
Risks/gaps: contains message bodies; privacy-sensitive.

Table: `vacancy_card_decisions`  
Purpose: per-run decision whether to send/suppress vacancy cards.  
Source-of-truth status: derived.  
Important columns: `run_id`, `vacancy_id`, `vacancy_key`, `card_key`, `decision`, `suppression_reason`, previous send/feedback fields.  
Writers: daily notification planning.  
Readers: suppression/debug reports.  
Recruiter relevance: explains why a vacancy was or was not surfaced.  
Risks/gaps: decision not equal to user application preference.

Table: `source_kpi_run`  
Purpose: per-source runtime/acquisition/scoring quality KPIs.  
Source-of-truth status: derived/reporting.  
Important columns: `run_id`, `source`, `source_status`, `acquisition_mode`, `runtime_seconds`, `login_walls`, `auth_redirects`, `anti_bot_events`, `found_count`, `accepted_count`, `notified_count`, `error_class`.  
Writers: daily pipeline.  
Readers: weekly KPI, dashboards, health.  
Recruiter relevance: source confidence context.  
Risks/gaps: not vacancy fact authority.

Table: `registry_company_runs`  
Purpose: per-company ATS/source collection attempts.  
Source-of-truth status: derived from registry plus run attempts.  
Important columns: `run_id`, `source`, `company_name`, `tier`, `ats_vendor`, `ats_slug`, `attempted`, `collected`, `vacancies_found`, `source_status`.  
Writers: target company/ATS collection.  
Readers: source diagnostics.  
Recruiter relevance: company/source coverage context.  
Risks/gaps: company names can be sensitive search-strategy data.

Table: `production_observation_daily`  
Purpose: daily production aggregate.  
Source-of-truth status: derived/reporting.  
Important columns: `run_id`, `runtime_seconds`, `total_collected`, `total_unique`, `duplicate_rate`, fit counts, auth/source failure JSON, reaction/application rates.  
Writers: daily pipeline.  
Readers: dashboards/health.  
Recruiter relevance: operational confidence only.  
Risks/gaps: aggregate, not per-opportunity truth.

Table: `job_intel_performance_spans`  
Purpose: performance spans per run/source.  
Source-of-truth status: derived telemetry.  
Important columns: `run_id`, `span_name`, `source_name`, `duration_ms`, counts, errors, metadata.  
Writers: performance recorder.  
Readers: performance reports.  
Recruiter relevance: none for MVP except diagnostics.  
Risks/gaps: telemetry only.

Table: `company_intelligence`  
Purpose: company-level summary, risk flags, career URLs, signal snapshots.  
Source-of-truth status: derived/cache.  
Important columns: `company`, `summary`, `risk_flags_json`, `target_category`, `website`, `signals_json`, `career_urls_json`, `opening_count`, timestamps.  
Writers: company monitoring.  
Readers: market/strategic reports.  
Recruiter relevance: useful company context with staleness warning.  
Risks/gaps: summaries and signals can drift.

Table: `company_intelligence_events`  
Purpose: event stream for company intelligence findings.  
Source-of-truth status: derived/cache.  
Important columns: `company`, `event_type`, `source`, `title`, `url`, `summary`, `details_json`, `seen_at`.  
Writers: company monitoring.  
Readers: strategic layer.  
Recruiter relevance: evidence list for company context.  
Risks/gaps: may include duplicate/noisy events.

Table: `strategic_signals`  
Purpose: derived company strategy/hiring signals.  
Source-of-truth status: derived.  
Important columns: `company`, `signal_type`, `confidence`, `horizon_days`, `probability`, `rationale`, `evidence_json`, `source`.  
Writers: strategic layer.  
Readers: strategic report.  
Recruiter relevance: optional context; never factual authority.  
Risks/gaps: predictive labels need disclaimers.

Table: `strategic_predictions`  
Purpose: future opportunity predictions.  
Source-of-truth status: derived/prediction.  
Important columns: `company`, `prediction_type`, `probability`, `horizon_days`, `rationale`, `evidence_json`, resolution fields.  
Writers: strategic layer.  
Readers: strategic report.  
Recruiter relevance: prioritization only.  
Risks/gaps: high risk of overclaiming.

Table: `opportunities`  
Purpose: CRM opportunity records.  
Source-of-truth status: authoritative for application/opportunity status inside job-intel.  
Important columns: `id`, `vacancy_id`, `company`, `title`, `source`, `source_url`, `canonical_url`, `ats`, `ats_job_id`, `status`, `score`, `confidence`, `recommendation`, Slack fields, artifact/task pointers, timestamps.  
Writers: CRM service, delivery, reconciler, Slack reaction flow.  
Readers: CRM repository/search, future Recruiter read API.  
Recruiter relevance: primary application/opportunity status source.  
Risks/gaps: no dedicated read-only public API yet.

Table: `opportunity_events`  
Purpose: CRM event timeline.  
Source-of-truth status: authoritative event log for CRM actions.  
Important columns: `opportunity_id`, `event_type`, `event_source`, `actor`, `payload_json`, `created_at`.  
Writers: CRM service/reconciler.  
Readers: CRM audit/history.  
Recruiter relevance: application history and decision timeline.  
Risks/gaps: JSON payload may include sensitive user/action details.

Table: `opportunity_tasks`  
Purpose: CRM follow-up/action queue.  
Source-of-truth status: authoritative for job-intel task state.  
Important columns: `opportunity_id`, `task_type`, `status`, `owner`, `due_at`, `note`, `created_at`, `completed_at`.  
Writers: CRM service/reconciler.  
Readers: CRM UI/future Recruiter read.  
Recruiter relevance: follow-up/action status.  
Risks/gaps: Recruiter MVP must not create/update tasks.

Table: `opportunity_artifacts`  
Purpose: application artifact records/placeholders.  
Source-of-truth status: draft metadata; empty in live DB.  
Important columns: `opportunity_id`, `artifact_type`, `version`, `content_path`, `content_text`, `summary`, `model`, `qa_status`, `qa_notes`.  
Writers: CRM service artifact placeholder path.  
Readers: future application material workflow.  
Recruiter relevance: draft lookup only if populated later.  
Risks/gaps: empty today; generated content must remain draft.

Table: `opportunity_contacts`  
Purpose: recruiter/contact records.  
Source-of-truth status: unknown; empty in live DB.  
Important columns: `opportunity_id`, `name`, `role`, `company`, `email`, `linkedin_url`, `source`, `confidence`.  
Writers/readers: unclear from current live rows.  
Recruiter relevance: not MVP-ready.  
Risks/gaps: privacy-sensitive and empty.

Table: `slack_message_map`  
Purpose: maps Slack messages to CRM opportunities.  
Source-of-truth status: authoritative mapping.  
Important columns: `opportunity_id`, `slack_channel_id`, `slack_message_ts`, `slack_thread_ts`, `created_at`.  
Writers: CRM service/reconciler.  
Readers: Slack reaction handling.  
Recruiter relevance: trace Slack feedback to opportunity.  
Risks/gaps: missing mapping yields `missing_mapping`.

Table: `vacancy_slack_messages`  
Purpose: vacancy card/message delivery metadata.  
Source-of-truth status: authoritative delivery mapping.  
Important columns: `vacancy_id`, `run_id`, `slack_channel`, `slack_message_ts`, `vacancy_key`, `canonical_url`, `card_key`, `notification_id`, message type, sent metadata.  
Writers: CLI delivery.  
Readers: feedback ingest.  
Recruiter relevance: trace user feedback and delivery status.  
Risks/gaps: channel/message identifiers are sensitive operational metadata.

Table: `vacancy_feedback`  
Purpose: Slack reaction event log for vacancy cards.  
Source-of-truth status: authoritative for captured feedback events.  
Important columns: `vacancy_id`, `slack_message_ts`, `feedback_type`, `event_type`, `event_timestamp`, `user_id`, `raw_event_json`, `card_key`, `slack_channel`.  
Writers: `run_feedback_event`.  
Readers: suppression/CRM reconciliation.  
Recruiter relevance: user preference signal.  
Risks/gaps: raw event JSON is privacy-sensitive.

Table: `vacancy_feedback_state`  
Purpose: active feedback state after reaction add/remove.  
Source-of-truth status: derived from feedback events.  
Important columns: `vacancy_id`, `feedback_type`, `active`, `updated_at`, Slack mapping fields.  
Writers: store feedback event logic.  
Readers: suppression/CRM.  
Recruiter relevance: compact user preference signal.  
Risks/gaps: derive from event log when precision matters.

Table: `user_feedback_opportunities`  
Purpose: legacy/simple vacancy feedback opportunity state.  
Source-of-truth status: legacy/unknown.  
Important columns: `vacancy_key`, `run_id`, `status`, `updated_at`, `notes`.  
Writers/readers: legacy compatibility tests indicate compatibility path.  
Recruiter relevance: secondary only after CRM state.  
Risks/gaps: potential overlap with `opportunities`.

Table: `vacancy_scoring_shadow`  
Purpose: v2/v3 scoring comparison/shadow gates.  
Source-of-truth status: derived/experiment.  
Important columns: `run_id`, `vacancy_key`, `score_v2`, `recommendation_v2`, `score_v3`, `recommendation_v3`, gates/function class, source key.  
Writers: daily scoring.  
Readers: scoring QA.  
Recruiter relevance: helps explain guardrails.  
Risks/gaps: v3 explicitly shadow; do not use as production decision unless enabled.

## 6. Vacancy Ingestion Flow

Supported current sources:

- LinkedIn: browser-native via `fetch_linkedin_vacancies`, `browser_worker.py`, and `BrowserSourceClient.search_linkedin`.
- HeadHunter/HH: browser-native via `fetch_headhunter_vacancies`, `browser_worker.py`, and `BrowserSourceClient.search_headhunter`.
- Target company career pages: `fetch_company_career_vacancies`, company monitoring, JSON-LD extraction.
- ATS: Greenhouse, Lever, Ashby, SmartRecruiters, Teamtailor, Personio, Recruitee in `job_intel/ats_sources.py`.
- Discovery/weak sources: DuckDuckGo, RemoteOK, Remotive in `job_intel/sources.py`; default enabled set excludes RemoteOK/Remotive/DuckDuckGo unless env enables them.

Fetch/read mechanisms:

- Browser-backed sources use `/var/lib/browser-desktop/profiles/{linkedin,hh,company-career}` and worker subprocess payloads.
- ATS sources use HTTP/API/XML/HTML extraction depending on vendor.
- Company career pages and browser extraction parse JSON-LD `JobPosting` where available.
- DuckDuckGo and remote boards are HTTP/search based and considered lower-priority/noisier.

Login-wall/anti-bot handling:

- `BrowserSessionHealth` records `login_walls`, `auth_redirects`, `anti_bot_events`, extraction failures, profile/session status.
- HeadHunter has Gmail OTP support through `JOB_INTEL_GMAIL_*` env vars; audit did not inspect or use secrets.

Dedup logic:

- `dedup.canonical_job_url` normalizes URLs.
- `dedup.canonical_vacancy_key` builds stable keys from source/url or source/company/title/location.
- `duplicate_links` stores canonical/duplicate relationships.
- `vacancy_observability.canonical_url` preserves real URL without duplicate suffix.

Snapshot/artifact storage:

- Main durable vacancy facts live in `vacancies`.
- Runtime diagnostics/browser artifacts may be written under browser/log dirs during actual runs; none were created by this audit.
- Application artifact table exists but live count is zero.

Where vacancy text can be safely reused:

- Recruiter should read `vacancies.description`, `title`, `company`, `location`, `url`, `metadata_json`, and latest `vacancy_evaluations` through a dedicated read-only query API or SQLite `mode=ro`.
- Recruiter must report source, first/last seen timestamps, and whether text may be stale.

## 7. Evaluation/Scoring Flow

Scoring is deterministic code, not an LLM evaluation path in the audited modules.

Inputs:

- Candidate/search profile seeds: `job_intel/seed/candidate.yaml`, `search_criteria.yaml`, `scoring.yaml`, `company_red_flags.yaml`, `target_companies.yaml`.
- Vacancy facts from `Vacancy` model.
- Target company and company intelligence context.

Scoring modules:

- `classify_vacancy` identifies executive product leadership vs non-product or generic roles.
- `score_vacancy_v1` and `score_vacancy_v2` produce production-style `Evaluation`.
- `score_vacancy_v3_shadow` computes a gated shadow score; code comment says it does not drive production recommendation decisions yet.
- `SCORING_MODEL_VERSION` selects scoring version through env/config.

Guardrails and exclusion rules:

- Hard blocklist for sales/marketing/customer-success "Executive" titles.
- Executive product leadership requires product-domain and seniority signals.
- Product-growth signal is gated to product-growth ownership, not generic growth/performance/BD.
- Non-product function penalties apply when product-domain is absent.
- RemoteOK/Remotive receive generic remote noise penalties when executive title signal is absent.
- Review-mode semantics: `near_miss` visible in daily report but must not alert.

Where Recruiter should trust output:

- Trust stored score/recommendation as job-intel's historical deterministic decision at a specific run/time.
- Do not treat it as final Recruiter verdict when vacancy content is stale, partial, or user asks for a fresh assessment.
- Trust title/function guardrails as reusable caution signals.

## 8. CRM/Opportunity Lifecycle

Valid opportunity statuses from `job_intel/crm_constants.py`:

```text
discovered, notified, watchlist, evaluation_requested, evaluated,
artifact_requested, artifacts_ready, application_planned, applied,
application_confirmed, outreach_planned, outreach_sent, recruiter_replied,
interviewing, assessment_received, offer_process, rejected_by_company,
declined_by_me, on_hold, stale, closed, archived
```

Terminal guarded statuses:

```text
rejected_by_company, declined_by_me, closed, archived
```

Core lifecycle behavior:

- `CRMService.ensure_opportunity_for_vacancy` creates/reuses opportunity by canonical URL, ATS job id, or company/title/location signature.
- Slack delivery can transition `discovered -> notified`.
- Slack reaction handling can change status and create tasks/artifact placeholders.
- `CRMReconciler.run(dry_run=True|apply=True)` backfills mappings/feedback/statuses; `apply=True` mutates.

Slack reaction mapping in CRM service:

- `eyes`/`bookmark`: `watchlist` plus `review_opportunity` task.
- `+1`/`thumbsup`/`thumbs_up`: `evaluation_requested` or `evaluated`.
- `fire`/`star`: `artifact_requested`, `generate_artifacts` task, placeholder artifact.
- `rocket`: `application_planned` only if current status is `artifacts_ready`; otherwise priority signal/review task.
- `-1`/`thumbsdown`/`thumbs_down`/`x`: `declined_by_me`.
- `question`: evaluation/review path.
- `mailbox_with_mail`: `outreach_planned`, `send_outreach` task.

Source of truth for application status:

- Primary: `opportunities.status` with `opportunity_events` history.
- Supporting: `opportunity_tasks`, `opportunity_artifacts`, `slack_message_map`, `vacancy_feedback`, `vacancy_feedback_state`.
- Legacy/secondary: `user_feedback_opportunities`.

Commands/actions requiring explicit approval:

- `CRMReconciler(... apply=True)`.
- Any `CRMService.transition_opportunity`, task/artifact/contact creation/update.
- `job-intel feedback-event` because it writes feedback and can feed CRM.
- Any manual URL ingestion or reconcile/apply path found in tests/code.

## 9. Notification and Platform Integration

Slack:

- Main delivery is `_deliver_to_slack` in `job_intel/cli.py`.
- If `JOB_INTEL_SLACK_WEBHOOK_URL` is absent or `prefer_gateway=True`, delivery uses Hermes `send_message_tool({"target": "slack:...", "message": ...})`.
- If webhook is configured, delivery uses `requests.post(webhook, json={"text": message, "channel": channel})`.
- Delivery metadata stored in `notifications` and `vacancy_slack_messages`.
- Feedback/reactions are ingested by `run_feedback_event` and stored in `vacancy_feedback`/`vacancy_feedback_state`.

Telegram:

- No direct job-intel Telegram delivery path found in audited `job_intel` modules.

Gmail:

- Gmail appears only for HeadHunter OTP/challenge support in `browser_sourcing.py` via `JOB_INTEL_GMAIL_*` env vars.
- Recruiter MVP must not use Gmail outbound or IMAP.

Google Docs:

- `idea_reaction_capture.py` can append Slack idea reactions to Google Docs unless run with `--dry-run`.
- This is adjacent and not core job-intel vacancy CRM.

Outbound risk:

- `send-test`, daily/alert/weekly/health/report commands, feedback-event, and idea-reaction capture can produce messages or write DB state.
- Recruiter MVP must keep all outbound sends approval-gated or disabled.

## 10. Artifacts and Reports

Reports:

- Daily digest/executive opportunity reports: generated by `run_daily`, formatters in `digest.py`, stored in `notifications.body` and delivery metadata.
- Exceptional alerts: `run_alert_scan`, currently timer disabled; uses persisted inventory.
- Weekly source quality: `run_weekly_kpi_report`, enabled timer.
- Health warning/report: `run_health_report`, enabled timer; can skip Slack when healthy unless configured otherwise.
- Market/strategic/enrichment reports: code and timers exist, host timers disabled.
- Grafana dashboards: `deploy/grafana/job-intel-*.json`.
- Metrics exporter: `deploy/docker/job-intel-exporter.py` and `JobIntelObservabilityExporter`.

Vacancy snapshots:

- Authoritative captured vacancy fields are in `vacancies`.
- Browser diagnostics may exist outside DB during live runs; not inspected for content in this audit.

Application materials:

- `opportunity_artifacts` table exists for artifact records/content/path/model/QA state.
- Live count is `0`; current application materials in this DB are not populated.
- Placeholder artifacts can be created by CRM reaction/reconciler paths and are drafts/stubs.

Naming conventions:

- Vacancy identity: `vacancy_key`, `canonical_url`, `card_key`.
- Slack identity: `slack_channel`, `slack_channel_id`, `slack_message_ts`, `slack_thread_ts`.
- Opportunity identity: `opportunities.id`, plus canonical URL or ATS job id.

## 11. Existing Career-Search SoT Artifacts

| Path/location | Owner/system | Authoritative vs draft/cache | Privacy sensitivity | Recruiter relevance |
|---|---|---|---|---|
| `job_intel/seed/candidate.yaml` | job-intel config | likely authoritative career profile seed for job-intel scoring | high | source for career facts/preferences, but Recruiter should confirm authority before treating as global SoT |
| `job_intel/seed/search_criteria.yaml` | job-intel config | authoritative for job-intel search criteria | medium/high | role/business model preferences |
| `job_intel/seed/scoring.yaml` | job-intel config | authoritative for deterministic job-intel score weights | medium | explains scoring, not necessarily Recruiter narrative |
| `job_intel/seed/company_red_flags.yaml` | job-intel config | authoritative for job-intel company red flags | medium | company risk checks |
| `job_intel/seed/target_companies.yaml` | job-intel config | authoritative job-intel target/watchlist seed | high | target company context |
| `job_intel/seed/deduplication.yaml` | job-intel config | authoritative dedup threshold config | low | canonical lookup behavior |
| `job_intel/seed/runtime.yaml` | job-intel config | authoritative default Slack/runtime config in repo, overridden by host env | medium | report channel defaults |
| `docs/company-registry-seed.yaml` | repo docs/config | authoritative/seed registry for ATS company acquisition when configured | medium/high | company/ATS source seed |
| `/var/lib/job-intel/state/job_intel.sqlite3` | job-intel runtime | authoritative for live vacancy/opportunity/event/status state | high | primary read source |
| `docs/job-intel-runtime.md` | repo docs | report/reference, historically stale-prone | low | operational context only |
| `docs/job-intel-architecture.md` | repo docs | architecture notes/report | low | design context |
| `docs/job-intel-host-runtime.md` | repo docs | host runtime reference | low | operational context |
| `docs/job-intel-operator-guide.md` | repo docs | operator guide | low | safe command guidance |
| `docs/job-intel-company-intelligence-architecture.md` | repo docs | architecture/report | low | company-intel context |
| `docs/job-intel-executive-source-architecture.md` | repo docs | architecture/report | low | source strategy context |
| `docs/decision-quality-top-opportunities-audit.md` | repo docs | report | medium | decision-quality context |
| `docs/opportunity-thesis-calibration.md` | repo docs | report/draft calibration | medium | positioning/context only |
| `opportunity_artifacts` table | job-intel DB | draft artifact metadata; live empty | high | future draft lookup only |
| `opportunity_contacts` table | job-intel DB | unknown; live empty | high | not MVP-ready |

No generated CV/cover-letter corpus was found in the audited job-intel DB; application artifacts table is empty.

## 12. Safe Recruiter Integration Points

Safe MVP read-only uses:

- Vacancy lookup by `vacancy_key`, URL/canonical URL, or recent/top vacancy query from `vacancies`.
- Latest deterministic evaluation lookup from `vacancy_evaluations` plus `vacancy_scoring_shadow` as shadow-only context.
- Opportunity lookup from `opportunities` by ID, vacancy ID, canonical URL, ATS job id, or Slack mapping.
- Application/status history from `opportunity_events`, `opportunity_tasks`, `vacancy_feedback`, and `slack_message_map`.
- Career preference lookup from seed YAML files, with privacy handling and explicit source labeling.
- Company context lookup from `company_intelligence`, `company_intelligence_events`, `strategic_signals`, `strategic_predictions`, labeled as derived/predictive.
- Source health lookup from `source_kpi_run`, `production_observation_daily`, and `runs`.

Must remain disabled or approval-gated:

- CRM status changes.
- Task, artifact, contact, feedback, or mapping writes.
- Slack/Gmail/Telegram/Google Docs/LinkedIn outbound or browser actions.
- Manual apply/application commands.
- Global reconcile/apply commands.
- DB migrations/bootstrap.
- Scheduled job triggers.
- Browser worker/source acquisition commands.
- Service restarts or live config changes.

Recommended read-only API shape:

```text
get_vacancy(vacancy_key|url) -> vacancy facts + provenance + latest evaluation
get_opportunity(opportunity_id|vacancy_key|url) -> status + events + tasks + artifacts summary
get_career_preferences() -> seed config metadata + redacted facts
get_company_context(company) -> company intelligence + source timestamps + predictive warning
get_source_health() -> latest per-source health summaries
```

## 13. Gaps and Recommendations

Gap ID: JI-SOT-001  
Severity: blocker  
Area: Recruiter integration API  
Finding: There is no dedicated read-only Recruiter/job-intel facade; direct imports expose mutating services and direct SQLite reads couple consumers to schema.  
Evidence: `crm_service.py` mutates status/tasks/artifacts; `crm_repository.py` has read methods but constructor calls `store.bootstrap()`; `store.py` mixes read and write methods.  
Impact on Recruiter: MVP could accidentally call write paths or drift with schema.  
Recommended action: add a small read-only query module/class that opens `JobIntelStore.connect(read_only=True)` and exposes only approved lookups.  
Do before MVP? yes

Gap ID: JI-SOT-002  
Severity: high  
Area: Source-of-truth docs drift  
Finding: Historical/local docs mention older DB paths, while host env/runtime confirms `/var/lib/job-intel/state/job_intel.sqlite3`.  
Evidence: runtime resolution and file stats from host; old `.hermes/job_intel` DB files are much smaller/stale.  
Impact on Recruiter: wrong DB path would yield stale or empty opportunity state.  
Recommended action: make this document and host env the authority; update older docs later if desired.  
Do before MVP? yes

Gap ID: JI-SOT-003  
Severity: high  
Area: CRM mutation boundaries  
Finding: Slack reaction and reconciler semantics can create tasks/artifacts and transition opportunity statuses.  
Evidence: `CRMService._apply_reaction_added`, `CRMReconciler.run(... apply=True)`, CRM tables with live opportunity/event/task rows.  
Impact on Recruiter: conversational Recruiter could accidentally mutate application status if it reuses CRM service directly.  
Recommended action: deny CRM writes in role policy and expose read-only status only.  
Do before MVP? yes

Gap ID: JI-SOT-004  
Severity: medium  
Area: Runtime invocation  
Finding: `python3 -m job_intel --help` fails with `ModuleNotFoundError: No module named 'pydantic'`; venv invocation works.  
Evidence: host command failure, then `venv/bin/python -m job_intel --help` success.  
Impact on Recruiter: ad hoc command execution may fail or use wrong environment.  
Recommended action: Recruiter should not execute job-intel CLI; internal host wrappers should remain operational-only.  
Do before MVP? no if read facade is used

Gap ID: JI-SOT-005  
Severity: medium  
Area: Career facts authority  
Finding: `candidate.yaml` contains profile facts used by job-intel scoring, but no global Recruiter career SoT hierarchy was proven in this audit.  
Evidence: seed file inventory; role/recruiter docs say generated text is not career fact.  
Impact on Recruiter: risk of treating scoring seed as complete career truth.  
Recommended action: define explicit Recruiter career SoT order before generating CV/CL material.  
Do before MVP? yes for document generation; no for vacancy lookup

Gap ID: JI-SOT-006  
Severity: medium  
Area: Application artifacts  
Finding: `opportunity_artifacts` exists but live count is zero; artifact generation lifecycle is placeholder/stub-level in CRM reaction path.  
Evidence: DB count `opportunity_artifacts|0`; `ensure_placeholder_artifact(... qa_status="stub")`.  
Impact on Recruiter: no existing authoritative application materials to reuse from DB.  
Recommended action: treat all generated Recruiter materials as drafts and store/write only after explicit approval in later phase.  
Do before MVP? no

Gap ID: JI-SOT-007  
Severity: low  
Area: Adjacent integrations  
Finding: Google Docs idea-reaction capture exists beside job-intel and can write unless `--dry-run`; it is not core job-intel vacancy CRM.  
Evidence: `idea_reaction_capture.py`, `scripts/slack_idea_reaction_capture.py`.  
Impact on Recruiter: accidental integration confusion.  
Recommended action: keep out of Recruiter MVP unless explicitly requested.  
Do before MVP? no

## 14. Recruiter Integration Recommendations

What Recruiter can safely read:

- `vacancies`, latest `vacancy_evaluations`, `vacancy_observability`, `vacancy_rejection_summary`, `opportunities`, `opportunity_events`, `opportunity_tasks`, `slack_message_map`, `vacancy_feedback_state`, seed YAML configs, and selected company/strategic context.

What Recruiter must never mutate in MVP:

- SQLite DB rows, CRM status/tasks/artifacts/contacts, Slack/Gmail/Telegram/Google Docs/LinkedIn state, browser profiles, systemd timers, host env/config, repo source, or scheduled jobs.

Where vacancy facts should come from:

- Primary: `vacancies` by `vacancy_key` or canonical URL.
- Supporting: `metadata_json`, source/run provenance, latest evaluation.
- Fallback: user-provided vacancy text when no DB record exists.

Where application status should come from:

- Primary: `opportunities.status` and `opportunity_events`.
- Supporting: `opportunity_tasks`, `slack_message_map`, `vacancy_feedback_state`.
- Legacy fallback: `user_feedback_opportunities` only if no CRM opportunity exists.

Where Denis career facts/preferences should come from:

- For job-intel scoring context: `job_intel/seed/candidate.yaml`, `search_criteria.yaml`, `scoring.yaml`, `company_red_flags.yaml`, `target_companies.yaml`.
- For Recruiter document generation: require an explicit career SoT hierarchy; do not treat generated content or previous drafts as facts.

Existing modules to reuse:

- Safe logic: `models.py`, `dedup.py`, selected read-only store/repository query patterns, scoring guardrail explanations from `evaluator.py`.
- Avoid direct use: `cli.py` run commands, `CRMService` mutators, `CRMReconciler.apply`, browser/source fetchers, delivery helpers.

MVP blockers:

- Add/define a read-only Recruiter lookup facade.
- Pin authoritative DB path and runtime environment.
- Pin career facts/preference authority for generated documents.
- Enforce role policy: no DB writes, no outbound sends, no browser/source acquisition.

Can wait:

- Application artifact persistence.
- Contact discovery.
- Gmail/Slack/LinkedIn outbound drafting/sending.
- Strategic predictions as active routing input.
- CRM reconcile/apply workflows.

## 15. Definition of Done Evidence

- This file exists as `docs/job-intel-source-of-truth.md`.
- Canonical host source/config/DB/timers/stash/gateway were not modified by this audit.
- No messages were sent.
- No scheduled jobs were run.
- No DB write commands were run.
- SQLite was inspected only through `mode=ro`.
- Important job-intel components, DB tables, source-of-truth boundaries, and Recruiter-safe read points are mapped.
- Local workspace was not clean before audit; many untracked docs/temp files existed. The intended audit output is this new document.
