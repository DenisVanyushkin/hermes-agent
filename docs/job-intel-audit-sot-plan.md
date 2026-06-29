# Job-Intel Audit SoT Plan

Status: draft SoT for read-only audit  
Target repository path: `docs/job-intel-audit-sot-plan.md`  
Expected audit output: `docs/job-intel-source-of-truth.md`  
Related SoT:

- `docs/hermes-recruiter-skill-package-architecture-sot.md`
- `docs/hermes-role-package-runtime-slice-plan.md`
- existing job-intel runtime/architecture docs if present in repo

## 1. Purpose

This document defines how to audit the existing job-intel system before implementing the Recruiter role.

The goal is to understand and document the existing job-intel implementation so the Recruiter role can reuse it safely instead of duplicating it.

The audit must produce a source-of-truth document:

```text
docs/job-intel-source-of-truth.md
```

## 2. Audit stance

This is a read-only discovery task.

The auditor must not:

- change source files;
- change configs;
- run live scheduled jobs;
- send Slack, Telegram, Gmail, or LinkedIn messages;
- modify the database;
- run write/apply/reconcile commands;
- commit;
- push;
- touch stash;
- restart gateway;
- change live production pilot config.

The audit may:

- inspect code;
- inspect docs;
- inspect config files with secrets redacted;
- inspect database schema using read-only sqlite commands if available and safe;
- list CLI commands/help text;
- inspect logs/reports only as evidence;
- identify safe read-only commands vs write/apply commands.

## 3. Canonical environment

Work on:

```bash
ssh hermes-agent
cd /home/hermes/.hermes/hermes-agent
```

Protected stash must remain untouched:

```text
stash@{0}: On local/customizations: codex-preserve-db-persistence-before-controlled-manual-sot-slice
```

The audit must record:

```text
branch
HEAD
git status
live config state relevant to job-intel if inspected
```

## 4. Required output structure

The produced `docs/job-intel-source-of-truth.md` must use this structure.

### 4.1 Executive summary

Include:

- what job-intel does;
- what systems it touches;
- what data it owns;
- how it relates to the future Recruiter role;
- biggest implementation risks/gaps.

### 4.2 Code/module map

Document:

- source directories and major modules;
- CLI entrypoints;
- scheduled job entrypoints;
- DB/storage modules;
- Slack/Telegram/Gmail/browser integration modules;
- evaluation/scoring modules;
- CRM/opportunity modules;
- report/artifact modules;
- tests relevant to job-intel.

For each module:

```text
path
responsibility
read/write behavior
relevance to Recruiter
confidence
```

### 4.3 Runtime and scheduled jobs

Document:

- daily/alert/enrichment jobs;
- trigger mechanism;
- schedules if configured;
- environment/config dependencies;
- expected outputs;
- safe way to observe status;
- commands that must not be run during audit.

### 4.4 Database and storage

Document:

- DB path(s);
- database technology;
- tables;
- important columns;
- relationships;
- lifecycle of vacancies/opportunities/events/tasks/artifacts/contacts/message maps;
- which tables are source of truth;
- which tables are caches or derived state;
- migration mechanism if any;
- read-only inspection commands.

Required table documentation format:

```text
Table: <name>
Purpose:
Source-of-truth status: authoritative | derived | cache | unknown
Important columns:
Writers:
Readers:
Recruiter relevance:
Risks/gaps:
```

### 4.5 Vacancy ingestion flow

Document:

- supported sources: LinkedIn, HH, careers pages, manual URLs, JSON-LD, or other discovered sources;
- fetch/read mechanisms;
- login-wall handling;
- dedup logic;
- snapshot/artifact storage;
- parse/enrichment steps;
- failure states;
- where vacancy text can be safely reused by Recruiter.

### 4.6 Evaluation/scoring flow

Document:

- scoring criteria;
- target company config;
- red flags;
- title/function guardrails;
- location/remote rules;
- exclusion rules;
- model usage if any;
- deterministic vs LLM evaluation;
- where Recruiter should trust job-intel output vs perform fresh evaluation.

### 4.7 CRM/opportunity lifecycle

Document:

- opportunity states;
- status transitions;
- events/tasks/artifacts;
- active/reopen/decline/applied/follow-up flows;
- Slack reaction mapping;
- command mapping;
- manual URL ingestion;
- source of truth for application status;
- commands that mutate CRM and require explicit approval.

### 4.8 Notification and platform integration

Document:

- Slack channels/threads/message maps;
- Telegram usage if any;
- Gmail usage if any;
- message delivery metadata;
- resend/report commands;
- read-only vs outbound actions;
- risk of accidental sends.

### 4.9 Artifacts and reports

Document:

- where daily summaries live;
- where performance reports live;
- where vacancy snapshots live;
- where application materials, if any, live;
- naming conventions;
- whether artifacts are authoritative, draft, report, or cache.

### 4.10 Existing career-search SoT artifacts

Inventory all current artifacts related to Denis's job search:

- CV/profile facts;
- preferences;
- target companies;
- red flags;
- application history;
- generated CV/CL/messages;
- follow-ups;
- recruiter contacts;
- CRM mappings.

For each artifact:

```text
path/location
owner/system
authoritative vs draft/cache
privacy sensitivity
Recruiter relevance
```

### 4.11 Safe Recruiter integration points

Identify what the Recruiter role can safely use in MVP:

```text
read-only vacancy lookup
read-only opportunity lookup
read-only application status lookup
read-only career preferences lookup
read-only artifact lookup
```

Identify what must remain disabled or approval-gated:

```text
CRM status changes
Slack/Gmail/Telegram outbound sends
manual apply commands
global reconcile/apply commands
DB migrations
scheduled job triggers
```

### 4.12 Gaps and recommendations

Document gaps in this format:

```text
Gap ID:
Severity: blocker | high | medium | low
Area:
Finding:
Evidence:
Impact on Recruiter:
Recommended action:
Do before MVP? yes/no
```

## 5. Audit evidence rules

Findings must include concrete evidence:

- file paths;
- function/class names;
- command names;
- config keys;
- DB table/column names;
- observed help text;
- log/report paths;
- exact read-only commands used.

Do not include secrets, tokens, private keys, cookies, OAuth material, raw auth files, or personal data beyond what is already intended as career SoT.

If a secret-shaped value is encountered, record only:

```text
<redacted: secret-shaped value present>
```

## 6. Read-only command policy

The auditor may run commands only when they are clearly read-only.

Allowed examples:

```bash
git status --short --untracked-files=all
git rev-parse HEAD
find . -maxdepth 4 -iname '*job*' -o -iname '*intel*'
grep -R "job_intel" -n .
python -m <module> --help
sqlite3 <db> '.schema'
sqlite3 <db> 'select name from sqlite_master where type="table";'
```

Disallowed unless Denis explicitly approves:

```text
commands containing --apply
commands that send/resend/deliver messages
commands that mutate Slack/Gmail/Telegram/CRM/DB
scheduled job runs
database migrations
service restarts
pipeline live validation
provider/model calls
```

If uncertain, classify the command as unsafe and do not run it.

## 7. Source-of-truth classification rules

Every discovered data store/artifact must be classified as one of:

```text
authoritative
report
draft
cache
derived
unknown
```

Definitions:

- `authoritative`: system/user treats it as source of truth.
- `report`: generated summary of authoritative state.
- `draft`: generated content requiring user acceptance.
- `cache`: reusable but not authoritative fetched/derived content.
- `derived`: computed from other sources.
- `unknown`: not enough evidence.

The final report must clearly distinguish:

```text
job-intel CRM DB = likely source of truth for application/opportunity status, if confirmed by audit
career SoT markdown/private docs = source of truth for Denis facts/preferences, if established
application materials = drafts unless explicitly accepted
```

## 8. How findings should feed Recruiter design

The audit must end with a section:

```text
Recruiter Integration Recommendations
```

It must answer:

- What can Recruiter safely read?
- What must Recruiter never mutate in MVP?
- Where should vacancy facts come from?
- Where should application status come from?
- Where should Denis career facts/preferences come from?
- What existing commands or modules can be reused?
- What gaps block Recruiter MVP?
- What gaps can wait?

## 9. Definition of Done

The audit is complete when:

- `docs/job-intel-source-of-truth.md` exists;
- repo remains clean except the intended document if the task includes writing it;
- no live state was changed;
- no messages were sent;
- no DB writes occurred;
- no scheduled jobs were run;
- all important job-intel components are mapped;
- source-of-truth vs draft/cache boundaries are explicit;
- safe read-only integration points for Recruiter are identified;
- write/apply/outbound actions are clearly marked as approval-gated or forbidden for MVP.
