# Core Shadow Role Package Calibration
**Date:** 2026-06-11
**Branch:** local/customizations
**HEAD before work:** `55bba0e41` — fix(approval): ignore multiline slack reply quotes
**Shadow packages commit:** `661937662`

---

## 1. Calibration Summary — Five Roles

### 1.1 scribe → hermes-scribe-core

| Dimension | Built-in | Shadow Package | Gap |
|---|---|---|---|
| Role purpose | Durable memory, decisions, handoffs, state, open questions | Identical in persona | None |
| Persona text | Precise archivist — concise, factual, future-reader oriented | Directly captured | None |
| Routing domain | `docs` via keywords: docs, documentation, handoff, зафиксируй… | Shadow triggers: `hermes scribe core package`, `scribe role core package`, `invoke hermes scribe core` | **Not activated** — shadow triggers are unique, not the built-in docs-domain terms |
| Reviewer behavior | scribe_hook: not_required; security_review_hook: conditional | Not modelled — no package equivalent for hooks | Missing: hook wiring |
| Approval behavior | Approval for: delete/overwrite canonical docs, recording sensitive personal data, marking unverified facts | Not modelled in package manifest schema | Missing: per-action approval gates |
| Model tier | standard | standard | **None** |
| Tool contract (allowed by default) | docs_read, docs_write, repo_read | Mapped to: `read_only_inspection`, `repo_edit` | Adequate for MVP |
| Tool categories | repo_write with confirmation | `repo_edit` (advisory) | Acceptable; enforcement deferred |
| Output style | Record only durable outcomes; avoid noisy artifacts | Captured in persona | None |
| Skills in package | None | None | None |
| **Parity status** | — | — | **GOOD** |
| **Routing activation risk** | Low | Triggers are unique, no overlap with docs-domain terms | Low risk |
| **Recommendation** | Ready for observe_warn calibration | Extend with `docs_write` category when taxonomy adds it | GO |

---

### 1.2 researcher → hermes-researcher-core

| Dimension | Built-in | Shadow Package | Gap |
|---|---|---|---|
| Role purpose | External research, source evaluation, synthesis | Identical in persona | None |
| Persona text | Skeptical analyst — cites sources, separates facts from inference, notes uncertainty | Directly captured | None |
| Routing domain | `research` via keywords: weather, news, btc, digest, company research, погода, комиссии… | Shadow triggers: `hermes researcher core package` etc. | **Not activated** |
| Reviewer behavior | scribe_hook: required for meaningful research; security_review_hook: conditional | Not modelled | Missing |
| Approval behavior | Approval for: persisting untrusted external content, writing to production state | Not modelled | Missing |
| Model tier | standard | standard | **None** |
| Tool contract (allowed by default) | web_search, browser, docs_read | Only `read_only_inspection` | **MISSING: `web_search`, `browser`** |
| Tool contract (allowed with confirmation) | docs_write, email_draft | Not declared | Acceptable for MVP |
| Output style | Summarize evidence, cite source quality, call out uncertainty | Captured in persona | None |
| Skills in package | None | None | None |
| **Parity status** | — | — | **PARTIAL** |
| **Missing capabilities** | — | web_search, browser tool categories | High priority pre-v1 |
| **Routing activation risk** | Medium | Must ensure research triggers don't shadow weather/btc phrases | Review needed |
| **Recommendation** | Add `web_search` and `browser` to `KNOWN_TOOL_CATEGORIES` before v1 activation | Low risk otherwise | PARTIAL GO |

---

### 1.3 engineer → hermes-engineer-core

| Dimension | Built-in | Shadow Package | Gap |
|---|---|---|---|
| Role purpose | Code, tests, repo config, debugging, runtime diagnostics, engineering fixes | Identical in persona | None |
| Persona text | Pragmatic senior engineer/SRE — small diffs, tests where relevant, no silent production mutation | Directly captured | None |
| Routing domain | `infra` via keywords: deploy, docker, systemd, rollback, logs, db, monitoring, production, host… | Shadow triggers: `hermes engineer core package` etc. | **Not activated** |
| Reviewer behavior | scribe_hook: required for meaningful tasks; security_review_hook: conditional | Not modelled | Missing |
| Approval behavior | Approval for: any production host mutation, service start/stop, deploy, rollback, db migration, firewall changes | Not modelled | Missing |
| Model tier | **reasoning** | standard | **MISSING: cannot express `reasoning` tier in package manifest** |
| Tool contract (allowed by default) | repo_read, repo_write, git_status_diff, test_runner, shell_local, docs_read | `read_only_inspection`, `repo_edit` | `shell_general` (advisory in MVP) missing |
| Tool contract (allowed with confirmation) | production_deploy, service_restart, docker_diagnostics, db_migration | Not declared | Acceptable for MVP |
| Context rendering | Special note: "Repo/code mutation is allowed. Production/runtime mutation requires explicit approval." | Captured in persona | None |
| Skills in package | None | None | None |
| **Parity status** | — | — | **PARTIAL** |
| **Missing capabilities** | — | `reasoning` model tier; `shell_general` category | Both pre-v1 blockers |
| **Over-modeled capabilities** | — | None | None |
| **Routing activation risk** | High — infra domain covers many common ops phrases | Must ensure shadow triggers don't overlap infra terms | Critical review needed |
| **Recommendation** | Add `model_tier_request: reasoning` machinery; add `shell_general` to taxonomy | Caution on routing activation | PARTIAL GO |

---

### 1.4 security_auditor → hermes-security-auditor-core

| Dimension | Built-in | Shadow Package | Gap |
|---|---|---|---|
| Role purpose | Review sensitive diffs, exposure, permissions, auth, secrets, public access | Identical in persona | None |
| Persona text | Paranoid but practical — distinguishes real risk, not a universal blocker, gives clear pass/fail | Directly captured | None |
| Routing domain | `security` via keywords: auth, authentication, secrets, token, cloudflare, firewall, permissions, audit, threat model… | Shadow triggers: `hermes sa core package` etc. | **Not activated** |
| Reviewer behavior | scribe_hook: required for security findings; security_review_hook: not_required (is the reviewer) | Not modelled | Missing |
| Approval behavior | Approval for: accepting residual high risk, changing security policy, exposing services publicly, weakening auth | Not modelled | Missing |
| Model tier | **critical** | standard | **MISSING: cannot express `critical` tier in package manifest** |
| Tool contract (allowed by default) | repo_read, git_status_diff, docs_read | `read_only_inspection` | Adequate |
| Tool contract (allowed with confirmation) | shell_local, browser, web_search, **secrets_read** | None declared (secrets_read intentionally excluded) | `secrets_read` intentionally absent |
| Context rendering | Special note: "Security Auditor is a reviewer, not a universal blocker." | Captured in persona | None |
| Skills in package | None | None | None |
| **Parity status** | — | — | **PARTIAL** |
| **Missing capabilities** | — | `critical` model tier; `secrets_read` intentionally excluded | Critical tier is pre-v1 blocker |
| **Over-modeled capabilities** | — | None | None |
| **Routing activation risk** | Medium — security domain uses specific terms but `audit` is substring-sensitive | Triggers were adjusted (`sa`) to avoid `audit` substring | Moderate risk |
| **Recommendation** | Add `model_tier_request: critical` machinery before v1 activation; keep `secrets_read` excluded unless carefully gated | Triggers are safe | PARTIAL GO |

---

### 1.5 career_strategist → hermes-career-strategist-core

| Dimension | Built-in | Shadow Package | Gap |
|---|---|---|---|
| Role purpose | Vacancy evaluation, CV/cover-letter strategy, application decisions, recruiter messaging | Identical in persona | None |
| Persona text | Executive career advisor — direct, commercial, thesis-driven, no fake experience, no auto-submit | Directly captured | None |
| Routing domain | `career` via keywords: vacancy, cv, cover letter, recruiter, career, job, apply, job intel, interview, оцени вакансию, резюме… | Shadow triggers: `hermes cs core package` etc. | **Not activated** |
| Reviewer behavior | scribe_hook: required for career decisions/application plans; security_review_hook: conditional | Not modelled | Missing |
| Approval behavior | Approval for: final apply decision override, editing production state | Not modelled | Missing |
| Model tier | standard | standard | **None** |
| Tool contract (allowed by default) | **job_intel_read**, docs_read, web_search, browser | Only `read_only_inspection` | **MISSING: `job_intel_read`, `web_search`, `browser`** |
| Tool contract (allowed with confirmation) | email_draft, email_send, slack_send | Not declared | Acceptable for MVP |
| Output style | Crisp job strategy, trade-offs, next action | Captured in persona | None |
| Skills in package | None | None | None |
| **Parity status** | — | — | **PARTIAL** |
| **Missing capabilities** | — | `job_intel_read`, `web_search`, `browser` | All pre-v1 requirements |
| **Routing activation risk** | Low-medium — career terms are specific; `career` substring triggered overlap during creation (resolved via `cs` abbrev) | Triggers are safe | Low risk |
| **Recommendation** | Add `job_intel_read` to `KNOWN_TOOL_CATEGORIES`; add `web_search`/`browser`; then update manifest | Triggers are ready | PARTIAL GO |

---

## 2. Tool Category Gap Analysis

Current `KNOWN_TOOL_CATEGORIES`:
```
production_deploy
read_only_inspection
repo_edit
secrets_read
shell_general
```

### Missing Categories

#### `web_search` / `browser`

| Field | Detail |
|---|---|
| **Roles needing it** | `researcher`, `career_strategist` (allowed by default in built-in) |
| **Why needed** | Researcher's primary function is external research via web. Career strategist needs company/market lookups. |
| **Read-only or mutation-capable** | Read-only — no state mutation; HTTP GET only |
| **Should be allowed in observe_warn?** | Yes — low risk, no production impact |
| **Needs enforcement later?** | No — low-risk read-only category; advisory mode sufficient |
| **Priority** | High — blocks complete researcher and career_strategist manifest parity |
| **Proposed category names** | `web_search` (keyword queries), `web_browse` (full browser navigation) |

#### `job_intel_read`

| Field | Detail |
|---|---|
| **Roles needing it** | `career_strategist` (allowed by default in built-in) |
| **Why needed** | Career strategist reads the job_intel SQLite database for vacancy analysis |
| **Read-only or mutation-capable** | Read-only — WAL mode, connect(read_only=True) |
| **Should be allowed in observe_warn?** | Yes — internal read-only database access |
| **Needs enforcement later?** | Potentially — restricts access to job_intel data to career-family roles only |
| **Priority** | Medium — career_strategist-specific; not needed for other shadow packages |
| **Proposed category name** | `job_intel_read` |

#### `shell_general`

| Field | Detail |
|---|---|
| **Roles needing it** | `engineer` (shell_local allowed by default; deploy/restart with confirmation) |
| **Why needed** | Engineer's primary execution surface is shell: tests, git, docker diagnostics |
| **Read-only or mutation-capable** | **Mutation-capable** — arbitrary shell commands can modify state |
| **Should be allowed in observe_warn?** | With caution — observe_warn mode should log shell calls; approval gate must remain active |
| **Needs enforcement later?** | Yes — high priority for enforced_tools mode to restrict to test/read-only shell patterns |
| **Priority** | High — engineer package has no shell access until this category exists |
| **Proposed category name** | `shell_general` (already in KNOWN_TOOL_CATEGORIES — just not declared in engineer manifest yet) |

**Note:** `shell_general` already exists in `KNOWN_TOOL_CATEGORIES`. The engineer shadow package can be updated to declare it immediately. The approval-gate semantics (production mutation requires explicit approval) must be preserved in the persona text.

---

## 3. Model Tier Gap Analysis

### Current Model Tier Support in Package Manifests

Package manifests support:
```yaml
model_tier_request: standard   # ← only explicitly tested tier
```

### Missing Tiers

#### `coding` — needed for engineer

| Field | Detail |
|---|---|
| **Built-in setting** | `default_model: coding` in `config/hermes-profiles.yaml` |
| **Effect** | Uses the dedicated engineering/coding tier for complex repo, debugging, and runtime analysis work |
| **Current package manifest** | `model_tier_request: standard` — silently degrades to a less capable model |
| **Risk if not resolved before v1** | Engineer package tasks will use the wrong model; complex debugging may produce lower-quality results |
| **Resolution** | Add `model_tier_request: coding` as a valid value in the package schema validator; route engineer and hermes-engineer-core through the coding fallback chain |

#### `critical` — needed for security_auditor

| Field | Detail |
|---|---|
| **Built-in setting** | `default_model: critical` in `config/hermes-profiles.yaml` |
| **Effect** | Highest-capability model for security reviews where false negatives are expensive |
| **Current package manifest** | `model_tier_request: standard` — significant capability downgrade |
| **Risk if not resolved before v1** | Security auditor package produces lower-quality security reviews; risk of missed findings |
| **Resolution** | Add `model_tier_request: critical` as a valid value; treat it with the same access gates as `secrets_read` (needs user confirmation or system-level override) |
| **Priority** | **Critical** — must be resolved before security_auditor package can be activated in production |

### Summary

| Role | Built-in tier | Package tier | Gap severity |
|---|---|---|---|
| scribe | standard | standard | None |
| researcher | standard | standard | None |
| engineer | **coding** | standard | High |
| security_auditor | **critical** | standard | Critical |
| career_strategist | standard | standard | None |

---

## 4. Routing Risk Analysis

### Built-in Trigger Coverage vs Shadow Package Trigger Candidates

The shadow packages use intentionally abbreviated triggers (`hermes sa core package`, `hermes cs core package`) that bear no relation to normal user phrases. This is correct for MVP: they exist only for explicit package testing, not for live routing activation.

### For v1 Routing Activation: Risk Per Role

#### scribe — LOW risk

- Built-in docs-domain triggers are mostly long phrases: `profile handoff`, `capture durable memory`, `today's work`
- Russian triggers: `зафиксируй`, `финальный статус` — specific enough
- **Overlay risk:** None — scribe is primarily an overlay target, not a primary route
- **docs-first mechanism:** Docs-first markers (`handoff`, `final status`, `update state`) correctly elevate scribe over engineer when docs signals dominate
- **Gap:** No Russian docs-first case beyond `финальный статус` in the corpus (see Golden Corpus section)

#### researcher — MEDIUM risk

- Research terms include broad words: `report`, `digest`, `news`, `current context`
- `report` could ambiguously trigger researcher for prompts that intend engineer (e.g., "give me a report on the docker status")
- `digest` and `news` are specific enough
- **Overlay risk:** career+researcher overlay works correctly (confirmed in corpus)
- **Missing intent:** No Russian infra prompts in corpus to confirm infra-domain terms don't accidentally route Russian engineering tasks to researcher

#### engineer — HIGH risk

- Infra terms are very broad: `change`, `service`, `operational`, `host`, `log`, `build`, `patch`
- `change` and `log` are the riskiest — they appear in many non-engineering contexts
- `security audit is required for this change` → routes to engineer (via `change`) + security_auditor + scribe (3-hop); not pure security_auditor
- **Overlay risk:** engineer+security_auditor+scribe overlay is correct but complex
- **docs-first vs infra conflict:** `update the runbook after the deploy` → engineer+scribe (not scribe alone), because `deploy` (infra) wins over docs-first elevation when docs-first markers are absent
- **Russian gap:** No Russian infra terms exist (`_INFRA_TERMS` is EN-only), so Russian engineering tasks fall through to `general_operator` — known limitation

#### security_auditor — MEDIUM risk

- Security terms are specific: `auth`, `authentication`, `cloudflare`, `firewall`, `threat model`
- `audit` is a substring risk (resolved in shadow package triggers via `sa` abbreviation)
- Pure security prompts without infra terms → security_auditor (correct)
- Security + infra prompts → engineer primary with security_auditor overlay (correct)
- **Gap:** `security audit is required for this change` routes to engineer (via `change`), not security_auditor — this is correct behavior but may surprise users expecting security_auditor primary

#### career_strategist — LOW-MEDIUM risk

- Career terms are specific: `vacancy`, `cv`, `cover letter`, `recruiter`, `job intel`, `interview`
- `career` and `job` are broader but still domain-specific
- `apply` could be ambiguous in non-career contexts (`apply the patch`)
- `career` substring overlap was caught during shadow package creation (resolved via `cs` abbreviation)
- **Overlay risk:** career+researcher overlay works correctly (confirmed in corpus)

### Overlay Rules — All Present, No Gaps

| Overlay | Status |
|---|---|
| engineer + security_auditor | Confirmed in corpus |
| engineer + scribe (via docs/security signal) | Confirmed in corpus |
| career_strategist + researcher | Confirmed in corpus |
| security_auditor + scribe | Confirmed in corpus |
| max 3-hop chain boundary | Confirmed in corpus |

---

## 5. Golden Corpus Gap Analysis

### Current Coverage (53 entries)

| Category | EN | RU | Total | Status |
|---|---|---|---|---|
| security | 6 | 0 | 6 | EN-only correct (`_SECURITY_TERMS` has no RU) |
| infra | 6 | 0 | 6 | **MISSING RU** — infra is EN-only by design; document explicitly |
| career | 4 | 4 | 8 | Good |
| docs | 4 | 4 | 8 | Good |
| docs_first | 4 | 1 | 5 | **RU light** — only `финальный статус`; `зафиксируй решение` not covered |
| research | 4 | 2 | 6 | Good |
| overlay | 5 | 0 | 5 | **MISSING RU overlays** |
| benign | 3 | 2 | 5 | Good |
| stripping | 4 | 0 | 4 | Good |

### Identified Gaps for v1 Readiness

#### Gap 1: Russian infra → general_operator fallback (DOCUMENTATION gap)
Russian-language engineering tasks have no infra routing because `_INFRA_TERMS` is EN-only. This is correct but undocumented. Russian users requesting engineering work get `general_operator`. This should be a documented limitation, not a bug fix before v1.

**Recommended action:** Add 2 corpus entries confirming `Задеплой сервис` → `general_operator` (not engineer) to make this explicit.

#### Gap 2: docs-first elevation for more RU cases
Only `финальный статус` (docs_first, ru). Missing: `зафиксируй решение о деплое` (docs-first over infra signal), `обнови state и сделай handoff`.

**Confirmed routing:** `Зафиксируй решение о деплое` → scribe (docs-first elevation works for RU). `Обнови state и сделай handoff` → scribe.

**Recommended action:** Add 2 RU docs_first entries.

#### Gap 3: scribe vs engineer without docs-first marker
`update the runbook after the deploy` → engineer+scribe (engineer primary because `deploy` wins). `capture the operational change state` → engineer+scribe. These are correct but not in corpus.

**Recommended action:** Add 2 entries documenting that docs+infra without docs-first marker → engineer primary with scribe overlay.

#### Gap 4: Pure security vs security+change
`security audit is required for this change` → engineer primary (via `change`) + security_auditor overlay. `Perform a security audit of the authentication flow` → security_auditor primary. Both are correct but the first is surprising.

**Recommended action:** Add 1 entry for security+infra → engineer primary with security_auditor overlay.

#### Gap 5: Researcher vs career ambiguity 
`do company research for this vacancy` → career_strategist+researcher overlay. Correct. But `review this company for due diligence` alone → researcher, not career_strategist. This split is correct but undocumented.

**Recommended action:** Add 1 entry for pure due_diligence → researcher (no career overlay).

#### Gap 6: Russian overlay cases
No RU overlay cases in corpus. `Зафиксируй решение о деплое` → scribe (docs-first, no overlay). No RU career+researcher case.

**Recommended action:** Add 1 RU docs entry with overlay-relevant signals.

### Missing Cases Summary

| ID | Category | Lang | Prompt (candidate) | Expected |
|---|---|---|---|---|
| `infra_ru_fallback_deploy` | infra_ru_fallback | ru | Задеплой сервис на прод хост | general_operator |
| `infra_ru_fallback_docker` | infra_ru_fallback | ru | Проверь докер логи на ошибки | general_operator |
| `docs_first_ru_zafiksiruy_reshenie` | docs_first | ru | Зафиксируй решение о деплое | scribe |
| `docs_first_ru_obnovit_state_handoff` | docs_first | ru | Обнови state и сделай handoff после изменения | scribe |
| `docs_infra_no_docs_first` | docs_infra | en | update the runbook after the deploy | engineer (chain: [engineer, scribe]) |
| `security_infra_change` | security | en | security audit is required for this change | engineer (chain: [engineer, security_auditor, scribe]) |
| `research_pure_due_diligence` | research | en | Review this company for due diligence | researcher |

Total: 7 new entries recommended. All verified against current routing.

---

## 6. Recommended v1 Routing Activation Sequence

### Pre-activation Requirements (must be done before activating any package routing)

1. ~~**Add `web_search`/`browser` to `KNOWN_TOOL_CATEGORIES`**~~ **✓ DONE 2026-06-11** — `web_search`, `web_browse`, `job_intel_read` added to `KNOWN_TOOL_CATEGORIES` and `hermes-role-tool-map.yaml`.

2. ~~**Add `job_intel_read` to `KNOWN_TOOL_CATEGORIES`**~~ **✓ DONE 2026-06-11** — included in item 1 above.

3. ~~**Add `shell_general` to engineer-core manifest**~~ **✓ DONE 2026-06-11** — `shell_general` added to `hermes-engineer-core` allowed_categories.

4. ~~**Add `model_tier_request: reasoning/critical` machinery**~~ **✓ DONE 2026-06-11** — `VALID_MODEL_TIERS` added and now include `coding`; engineer updated to `coding`, security_auditor to `critical`.

5. ~~**Extend golden corpus** with the 7 missing entries identified above.~~ **✓ DONE 2026-06-11** — 7 entries added.

### Activation Sequence (once pre-activation requirements are met)

| Phase | Action | Risk | Prerequisite |
|---|---|---|---|
| 1 | Activate `hermes-scribe-core` routing | Low | observe_warn calibration, extended corpus |
| 2 | Activate `hermes-researcher-core` routing | Medium | web_search/browser in taxonomy |
| 3 | Activate `hermes-career-strategist-core` routing | Low-medium | job_intel_read in taxonomy |
| 4 | Activate `hermes-security-auditor-core` routing | Medium | critical model tier machinery |
| 5 | Activate `hermes-engineer-core` routing | High | coding model tier with fallback `coding -> reasoning -> standard`; shell_general declared; extensive observe_warn calibration |
| 6 | Enable `enforced_tools` (post-v1) | Varies | ≥1 week observe_warn with <5% false positive rate per role |

**Rationale for sequence:** Scribe and researcher are lowest-mutation-risk roles. Career strategist is safe because its triggers are domain-specific. Security auditor before engineer because security_auditor is read-only. Engineer last because it has the broadest infra trigger surface and mutation capability.

---

## 7. Tests Run and Results

All required test suites passed before and after calibration work (report and test additions are read-only/additive):

```
test_core_shadow_role_packages.py     108 passed
test_example_role_packages.py          25 passed
test_role_package_manifest.py         }
test_role_package_cli.py              } 133 passed
test_role_package_skills.py           }
test_role_policy.py                   }

test_routing_golden_corpus.py          }
test_profile_routing.py               } 98 passed
test_profile_validation.py            }

scripts/validate_profile_architecture.py: PASSED

Total: 339 passed, 0 failed

---

## Post-Taxonomy-Gap Update (2026-06-11)

Pre-activation requirements 1, 2, 3, 5 closed in commit `test(roles): add shadow role taxonomy and routing corpus gaps`:

- **`web_search`, `web_browse`, `job_intel_read`** added to `KNOWN_TOOL_CATEGORIES` and `hermes-role-tool-map.yaml`
- **`shell_general`** added to `hermes-engineer-core` manifest
- **7 golden corpus entries** added covering: Russian infra fallback (×2), Russian docs-first (×2), infra+docs overlay, security+infra chain, pure research
- **`test_core_shadow_role_packages.py`** extended with `TestNewTaxonomyCategories` (11 tests) and `TestPreV1CorpusGaps` (1 test)

Remaining pre-v1 blockers after `feat(roles): support package model tier requests` commit:
- observe_warn calibration run (≥1 week, <5% false positive rate per role before routing activation)
- package routing activation (behind feature flag; scribe first in v1 sequence)
```

---

## 8. Commit Hash

Report-only commit: see git log after this file is committed.

---

## Appendix: Shadow Package Trigger Reference

| Package | EN triggers | RU triggers |
|---|---|---|
| hermes-scribe-core | hermes scribe core package, scribe role core package, invoke hermes scribe core | пакет роли hermes scribe core, базовый пакет роли scribe |
| hermes-researcher-core | hermes researcher core package, researcher role core package, invoke hermes researcher core | пакет роли hermes researcher core, базовый пакет роли researcher |
| hermes-engineer-core | hermes engineer core package, engineer role core package, invoke hermes engineer core | пакет роли hermes engineer core, базовый пакет роли engineer |
| hermes-security-auditor-core | hermes sa core package, hermes-sa-core pkg, invoke hermes-sa-core | пакет роли hermes sa core, базовый пакет hermes-sa-core |
| hermes-career-strategist-core | hermes cs core package, hermes-cs-core pkg, invoke hermes-cs-core | пакет роли hermes cs core, базовый пакет hermes-cs-core |

Trigger abbreviations for security_auditor (`sa`) and career_strategist (`cs`) were required because:
- `security audit` (built-in term) is a substring of `hermes security auditor core package`
- `career` (built-in term) is a substring of `hermes career strategist core package`
