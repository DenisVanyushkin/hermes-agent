# Agent Task — Step 1: Career Preference Model SoT Normalization

## Role

Ты работаешь как внешний coding/research agent на canonical host:

```bash
ssh hermes-agent
cd /home/hermes/.hermes/hermes-agent
```

Работай только на этом хосте и в этом репозитории. Не используй локальные копии как Source of Truth.

## Source of Truth

Главный процессный SoT:

```text
docs/job-intel-improvements/jul19/job-intel-career-preference-system-development-sot.md
```

Исследовательские источники:

```text
docs/audit/2026-07-19-recommendation-system-audit.md
docs/audit/2026-07-19-career-preference-model.md
```

Текущий draft:

```text
career-preference-model.draft.yaml
```

Также найди и проанализируй фактические runtime-конфиги и контракты, связанные с preferences/scoring/search criteria. Не предполагай пути — зафиксируй найденные пути evidence в отчете.

## Mission

Преобразовать исследовательский `career-preference-model.draft.yaml` в строгий, версионируемый и валидируемый Source of Truth для всех карьерных компонентов, **не подключая его к production evaluator**.

Это Step 1 из пятишагового плана. Не переходи к feature extraction, shadow evaluator или production changes.

## User decisions that MUST be encoded

### Relocation

- готов к релокации практически в любую страну;
- исключить подсанкционные и нестабильные страны;
- Африка сейчас не рассматривается;
- USA onsite/hybrid допустим только при explicit employer visa/relocation sponsorship;
- USA remote не является автоматическим blocker;
- KZ local roles не являются hard veto: создать отдельную fallback policy, так как при ухудшении текущей рабочей ситуации пользователь готов временно снизить планку.

### Remote timezone

- hard ограничения отсутствуют;
- любая timezone difference допустима на текущем этапе;
- timezone может быть risk/clarification, но не rejection gate;
- пользователь допускает переезд в более удобную timezone ради качественной роли.

### Compensation

- не использовать компенсацию как gate, penalty или ranking feature;
- отсутствие salary range не является негативом;
- compensation preference остается inactive/unknown до появления реальных отказов по этой причине.

## Required model structure

Нормализованная модель должна физически разделять минимум:

```yaml
career_preference_model:
  metadata:
  motivations:
  feasibility_constraints:
  mandate_preferences:
  company_preferences:
  anti_preferences:
  interaction_rules:
  exploration_policy:
  local_market_fallback_policy:
  evidence_registry:
  change_policy:
```

Допустима другая структура только при явном доказательстве, что она лучше покрывает требования SoT.

## Mandatory corrections to the draft

1. Нормализовать enum `strength` и `confidence`; исключить несогласованные значения вроде `medium_high`, либо формально добавить их в schema с rationale.
2. Заменить `product_function_only` на правило, которое допускает hybrid product/commercial/GM/COO-adjacent роли при наличии digital business ownership.
3. Заменить title-based `seniority_floor` на scope/mandate-based criterion; title оставить evidence, не hard truth.
4. Разделить company-level и role-level negatives.
5. Не допустить двойных/тройных штрафов за один и тот же сигнал.
6. Добавить interaction rules минимум для:
   - narrow scope + monetization exception;
   - B2B + platform-as-business exception;
   - remote role suppresses country onsite penalty;
   - crypto employer != automatic role veto;
   - USA relocation requires explicit sponsorship;
   - KZ fallback lane;
   - platform-as-business != platform engineering.
7. Добавить field-level provenance:
   - source type: behavioral | explicit | inferred;
   - evidence pointer;
   - evidence count where available;
   - last validated date;
   - status: active | hypothesis | exploration | inactive;
   - override allowed/conditions where relevant.
8. Добавить versioning, compatibility и change-approval policy.

## Required deliverables

Предложи точные repo paths и подготовь:

1. normalized `career-preference-model.yaml`;
2. strict schema (`.schema.json`, Pydantic or equivalent — выбери минимальный поддерживаемый вариант);
3. human-readable contract `career-preference-model.md`;
4. `career-preference-model-migration-map.md`;
5. schema/invariant validation tests;
6. read-only diff/comparison report: draft vs normalized;
7. inventory всех текущих consumer/config locations, которые позже потребуется мигрировать;
8. explicit list of unresolved questions — только те, которые действительно блокируют SoT normalization.

## Migration map requirements

Проверь фактический код и отобрази:

```text
current source/rule
→ normalized SoT field/rule
→ future consumer
→ migration status
→ conflict/deprecation risk
```

Ищи как минимум:

- preferences config;
- search criteria;
- scoring YAML;
- evaluator hardcoded weights/rules;
- universe anchors/company discovery;
- feedback taxonomy/calibration mappings;
- recruiter/application-materials inputs.

Не меняй эти consumers в рамках Step 1.

## Required invariants

Validation должна гарантировать:

- unique IDs;
- strict allowed enums;
- no unknown fields where strictness is intended;
- all interaction-rule references resolve;
- all active rules contain provenance;
- compensation is inactive and has zero gating/ranking effect;
- timezone is not a hard gate;
- USA onsite without explicit sponsorship is infeasible;
- USA remote is not automatically infeasible;
- Africa/sanctioned/unstable relocation policy represented explicitly;
- KZ local fallback is represented separately from global core preference;
- industry/country/title cannot carry standalone active preference weight;
- no production integration exists.

## Tests / golden policy cases

Добавь contract-level tests минимум для следующих сценариев:

1. Remote US Director role → geography alone does not reject.
2. US onsite without explicit sponsorship → infeasible.
3. US onsite with explicit sponsorship → eligible for downstream evaluation.
4. Berlin/Dubai/Singapore relocation with sponsorship → feasible unless unstable/sanctioned rule applies.
5. Africa relocation → currently excluded.
6. KZ local strong role → eligible only via local fallback lane, not global core preference.
7. Narrow Pricing role → narrow-scope penalty can be overridden by monetization rule.
8. B2B platform-as-business → no generic B2B rejection.
9. Crypto employer + broad transferable mandate → company concern/exploration, not automatic role veto.
10. Platform engineering role → must not inherit platform-as-business positive.
11. GM Digital / GM Market with product/P&L ownership → not rejected as non-product title.
12. Compensation missing → no penalty or rejection.
13. Large timezone gap → uncertainty/risk only, no rejection.

## Execution constraints

- No production ranking changes.
- No evaluator integration.
- No Slack delivery changes.
- No cron changes.
- No gateway restart.
- No live config changes.
- No source expansion.
- No ML model or learned weights.
- Do not delete legacy configs.
- Do not push unless separately instructed.
- Preserve protected stash and unrelated working-tree changes.

## Working method

1. Preflight: host, repo, branch, HEAD, status, stash, relevant files.
2. Read all SoT/research inputs.
3. Produce a concise architecture/schema proposal before editing.
4. Implement smallest coherent slice.
5. Add tests before declaring completion.
6. Run targeted tests, schema validation, `git diff --check`, and any applicable lint.
7. Review for drift against the process SoT.
8. Stop at commit gate unless explicit permission to commit has already been given.

## Definition of Done

Step 1 is complete only when:

- normalized SoT and strict schema exist;
- all listed user decisions are encoded unambiguously;
- model domains are separated;
- interaction rules cover known counterexamples;
- migration map is evidence-based from actual code;
- tests cover required invariants and policy scenarios;
- production behavior is unchanged;
- final report states exact files, tests, unresolved risks and recommended Step 2 inputs.

## Final report format

1. Verdict
2. Preflight evidence
3. Files created/changed
4. Normalized model structure
5. User decisions encoded
6. Interaction rules
7. Migration inventory
8. Tests and validation results
9. Drift check against SoT
10. Unresolved questions/risks
11. Step 2 readiness
12. Git status and explicit confirmation of no push/restart/live changes
