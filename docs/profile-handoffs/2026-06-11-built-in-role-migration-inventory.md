# Built-in Role Migration Inventory
**Date:** 2026-06-11
**Branch:** local/customizations

This document inventories the five built-in Hermes roles targeted for shadow
package migration in the Role Packages v1 preparation task.

---

## Source Files Inspected

| File | Purpose |
|---|---|
| `config/hermes-profiles.yaml` | Authoritative role definitions: tools, approvals, paths, contracts |
| `config/hermes-routing-triggers.yaml` | Domain trigger terms and overlay rules |
| `hermes_cli/profile_routing.py` | Python routing constants (authoritative until Slice 2C) |
| `hermes_cli/profile_context.py` | Role context rendering and output-style injection |

---

## Role Inventory Table

### scribe

| Field | Value |
|---|---|
| `role_id` | `scribe` |
| `canonical_id` | `scribe` |
| `display_name` | Scribe |
| `role_family` | documentation |
| `default_model` | standard |
| `purpose_summary` | Durable memory, decisions, handoffs, state, open questions |
| **Routing domain** | `docs` → scribe (via `_DOCS_TERMS`) |
| **Key routing triggers** | docs, documentation, handoff, record the decision, update state, final status, зафиксируй |
| **docs_first_markers** | handoff, final status, update docs, update state, status update, зафиксируй |
| **Approval behavior** | Requires approval for: deleting/overwriting canonical docs, sensitive personal data, marking unverified facts as verified |
| **Reviewer behavior** | scribe_hook: not_required; security_review_hook: conditional (security docs / permission changes) |
| **Tool contract (allowed by default)** | docs_read, docs_write, repo_read |
| **Tool contract (allowed with confirmation)** | repo_write |
| **Tool contract (forbidden)** | production_deploy, service_restart, secrets_read, secrets_write, db_migration, trading_execute |
| **Tool category assumptions** | read_only_inspection + repo_edit |
| **Context rendering fields** | display_name, purpose_summary, personality_summary, tool_contract, escalation_targets, output_style |
| **Output style** | Record only durable outcomes that matter; avoid noisy artifacts |
| **Escalation targets** | engineer, security_auditor, chief_coordinator |
| **Current limitations/hacks** | scribe_hook wiring is built-in only; no package equivalent in MVP |

---

### researcher

| Field | Value |
|---|---|
| `role_id` | `researcher` |
| `canonical_id` | `researcher` |
| `display_name` | Researcher |
| `role_family` | research |
| `default_model` | standard |
| `purpose_summary` | External research, source evaluation, synthesis |
| **Routing domain** | `research` → researcher (via `_RESEARCH_TERMS`) |
| **Key routing triggers** | weather, news, company research, current facts, digest, report, btc, bitcoin, crypto, погода, комиссии |
| **Approval behavior** | Requires approval for: persisting untrusted external content, writing to production state |
| **Reviewer behavior** | scribe_hook: required (meaningful research/report updates); security_review_hook: conditional (external content/source trust risk) |
| **Tool contract (allowed by default)** | web_search, browser, docs_read |
| **Tool contract (allowed with confirmation)** | docs_write, email_draft |
| **Tool contract (forbidden)** | repo_write, production_deploy, service_restart, secrets_read, secrets_write, trading_execute |
| **Tool category assumptions** | read_only_inspection |
| **Context rendering fields** | display_name, purpose_summary, personality_summary, tool_contract, escalation_targets, output_style |
| **Output style** | Summarize evidence, cite source quality, call out uncertainty explicitly |
| **Escalation targets** | career_strategist, engineer, scribe |
| **Current limitations/hacks** | web_search/browser not mapped to a package tool category yet |

---

### engineer

| Field | Value |
|---|---|
| `role_id` | `engineer` |
| `canonical_id` | `engineer` |
| `display_name` | Engineer |
| `role_family` | engineering |
| `default_model` | reasoning |
| `purpose_summary` | Code, tests, repo config, debugging, runtime diagnostics, engineering fixes |
| **Routing domain** | `infra` → engineer (via `_INFRA_TERMS`) |
| **Key routing triggers** | deploy, docker, systemd, rollback, logs, db, monitoring, runtime, production, host, service, rebase |
| **Approval behavior** | Requires approval for: any production host mutation, service start/stop/restart, production config change, db migration, deploy, rollback, firewall changes |
| **Reviewer behavior** | scribe_hook: required (meaningful tasks); security_review_hook: conditional (auth/exposure/secret/scheduler/tool/permission/webui changes) |
| **Tool contract (allowed by default)** | repo_read, repo_write, git_status_diff, test_runner, shell_local, docs_read |
| **Tool contract (allowed with confirmation)** | docker_diagnostics, production_deploy, service_restart, scheduler_modify, db_migration, cloudflare_dns_proxy |
| **Tool contract (forbidden)** | secrets_write, trading_execute |
| **Tool category assumptions** | read_only_inspection + repo_edit (shell_general requires approval) |
| **Context rendering fields** | display_name, purpose_summary, personality_summary, tool_contract, escalation_targets, output_style + special note: "Repo/code mutation is allowed. Production/runtime mutation requires explicit approval." |
| **Output style** | What changed, tests run, risks, next step, rollback note when applicable |
| **Escalation targets** | security_auditor, scribe |
| **Overlay rules** | engineer_security_overlay (+security_auditor when security domain hit); engineer_scribe_overlay (+scribe when docs/security domain hit) |
| **Current limitations/hacks** | shell_after_approval and deploy_execution_after_approval are approval-gated but not enforceable in package MVP; reasoning model tier not expressible in package manifest |

---

### security_auditor

| Field | Value |
|---|---|
| `role_id` | `security_auditor` |
| `canonical_id` | `security_auditor` |
| `display_name` | Security Auditor |
| `role_family` | security |
| `default_model` | critical |
| `purpose_summary` | Review sensitive diffs/actions, exposure, permissions, auth, secrets, public access |
| **Routing domain** | `security` → security_auditor (via `_SECURITY_TERMS`); also overlays engineer |
| **Key routing triggers** | auth, authentication, secrets, token, exposure, cloudflare, firewall, permissions, security review, security audit, audit, threat model |
| **Approval behavior** | Requires approval for: accepting residual high risk, changing security policy, exposing services publicly, weakening auth or permissions |
| **Reviewer behavior** | scribe_hook: required (security findings/decisions); security_review_hook: not_required (is the reviewer) |
| **Tool contract (allowed by default)** | repo_read, git_status_diff, docs_read |
| **Tool contract (allowed with confirmation)** | shell_local, browser, web_search, secrets_read |
| **Tool contract (forbidden)** | repo_write, production_deploy, service_restart, secrets_write, db_migration, trading_execute |
| **Tool category assumptions** | read_only_inspection only (no repo_edit) |
| **Context rendering fields** | display_name, purpose_summary, personality_summary, tool_contract, escalation_targets, output_style + special note: "Security Auditor is a reviewer, not a universal blocker." |
| **Output style** | State the security evidence, the risk, whether a reviewer is required, and the safest next step |
| **Escalation targets** | engineer, scribe, general_operator |
| **Current limitations/hacks** | `critical` model tier is not mappable to package manifests in MVP; `secrets_read` with confirmation deliberately excluded from shadow package |

---

### career_strategist

| Field | Value |
|---|---|
| `role_id` | `career_strategist` |
| `canonical_id` | `career_strategist` |
| `display_name` | Career Strategist |
| `role_family` | career |
| `default_model` | standard |
| `purpose_summary` | Vacancy evaluation, CV/cover-letter strategy, application decisions, recruiter messaging |
| **Routing domain** | `career` → career_strategist (via `_CAREER_TERMS`) |
| **Key routing triggers** | vacancy, cv, cover letter, recruiter, head of product, vp product, cpo, career, job, role fit, apply, application, job intel, interview, оцени вакансию, резюме |
| **Approval behavior** | Requires approval for: final apply decision override, editing production state |
| **Reviewer behavior** | scribe_hook: required (career decision/application plan); security_review_hook: conditional (external/personal data risk) |
| **Tool contract (allowed by default)** | job_intel_read, docs_read, web_search, browser |
| **Tool contract (allowed with confirmation)** | docs_write, email_draft, email_send, slack_send |
| **Tool contract (forbidden)** | repo_write, production_deploy, service_restart, secrets_read, secrets_write, trading_execute |
| **Tool category assumptions** | read_only_inspection (`job_intel_read` is not a package category in MVP) |
| **Context rendering fields** | display_name, purpose_summary, personality_summary, tool_contract, escalation_targets, output_style |
| **Output style** | Give crisp job strategy, trade-offs, and next action |
| **Escalation targets** | researcher, scribe, general_operator |
| **Overlay rules** | career_researcher_overlay (+researcher when research domain hit); career_scribe_overlay (+scribe when docs domain hit) |
| **Current limitations/hacks** | `job_intel_read` tool category does not exist in package taxonomy; email/slack categories not yet defined |

---

## Migration Readiness Summary

| Role | Shadow Package | Trigger Gap | Model Tier Gap | Tool Category Gap |
|---|---|---|---|---|
| scribe | hermes-scribe-core | None | None | None |
| researcher | hermes-researcher-core | None | None | `web_search`/`browser` not in taxonomy |
| engineer | hermes-engineer-core | None | reasoning → standard | `shell_general` advisory only |
| security_auditor | hermes-security-auditor-core | None | critical → standard | `secrets_read` intentionally excluded |
| career_strategist | hermes-career-strategist-core | None | None | `job_intel_read` not in taxonomy |

All five shadow packages validate cleanly. All five are ready as migration preparation fixtures.
