# Career Preference Model — Human-Readable Contract (v1.0.0)

**Статус:** normalized, NOT integrated в production. Step 1 из пятишагового плана
([process SoT](job-intel-career-preference-system-development-sot.md)).
**Машинные артефакты:** `job_intel/preference_model/career-preference-model.yaml`
(модель), `job_intel/preference_model/model.py` (Pydantic-контракт, источник
схемы), `job_intel/preference_model/career-preference-model.schema.json`
(сгенерированная JSON Schema).
**Тесты:** `tests/job_intel/test_preference_model.py` (инварианты + 13 golden
policy cases).

## 1. Назначение

Единый версионируемый Source of Truth карьерных предпочтений для всех будущих
потребителей: shadow evaluator (Step 3), feature extraction (Step 2), company
discovery, CV-tailoring, recruiter messaging. Ни один production-компонент не
читает эту модель до отдельного утверждённого rollout-шага; contract-тест
`test_no_production_integration` это enforce'ит.

## 2. Структура модели

| Секция | Что содержит |
|---|---|
| `metadata` | версии, статус, data basis, происхождение |
| `motivations` | 4 карьерные мотивации (контекст, не правила скоринга) |
| `feasibility_constraints` | «могу/не могу»: гео, формат работы, функция, scope, доменный барьер + `timezone_policy` + `compensation_policy` |
| `mandate_preferences` | «хочу» на уровне роли: scope breadth, growth, монетизация, P&L, org-мандат, фаза |
| `company_preferences` | «хочу» на уровне компании: масштаб, бренд, стадия, модель, культура, platform-as-business |
| `anti_preferences` | негативы, физически разделённые на `level: company` и `level: role`, с tier strong/soft |
| `interaction_rules` | 8 правил-исключений с приоритетами и структурными условиями |
| `exploration_policy` | контролируемое исследование неизвестных осей (1–2 карточки/нед., одна ось за раз) |
| `local_market_fallback_policy` | отдельная KZ-lane: manual activation, standby, никогда не смешивается с core |
| `evidence_registry` | 9 источников evidence; каждое правило ссылается сюда по `registry_id` |
| `change_policy` | semver, extra=forbid-совместимость, owner approval, no silent learning |

## 3. Ключевые семантические решения

1. **Feasibility ≠ preference.** «Не могу принять» (verdict feasible/uncertain/
   infeasible) и «не хочу» (preferences/anti-preferences) — разные секции и
   разные вердикты будущего evaluator'а.
2. **Mandate over labels.** Оси `industry`, `country`, `title` запрещены как
   самостоятельные активные предпочтения (валидатор `FORBIDDEN_STANDALONE_AXES`).
   Категориальные ярлыки — сырьё для вычисления свойств.
3. **Scope вместо titles.** Seniority floor задан через
   `fc_scope_below_executive` (scope/мандат), титул — только evidence.
   Функциональный гейт `fc_function_digital_business_ownership` допускает
   hybrid product/commercial/GM/COO-роли при наличии digital business
   ownership (замена драфтового `product_function_only`).
4. **Условия правил структурные** (`when`: work_format, country_group,
   sponsorship_stated, flags_all/flags_none), поэтому политика проверяется
   детерминированным matcher'ом без построения evaluator'а.
5. **Один сигнал — один штраф.** Interaction rules с `suppress`/`exclude_from`
   гарантируют отсутствие двойных штрафов (например, platform engineering
   получает soft-негатив `pure_infrastructure_devex`, но не наследует позитив
   `platform_as_the_business` и не получает второй штраф за то же).

## 4. Закодированные решения пользователя (2026-07-19)

| Решение | Кодировка |
|---|---|
| Релокация почти в любую страну | `fc_onsite_sponsorship_unknown` → uncertain (не reject) |
| Санкционные / нестабильные страны | `fc_sanctioned_geo`, `fc_unstable_geo` → infeasible (критерий, не поддерживаемый список стран) |
| Африка сейчас не рассматривается | `fc_africa_current_stage` → infeasible, override allowed по явному решению владельца |
| USA onsite/hybrid только с явным sponsorship | `fc_usa_onsite_requires_explicit_sponsorship` (no/unknown → infeasible; yes → общие основания) |
| Remote US не blocker | конструкции onsite-scoped; guard-валидатор «remote USA не может быть infeasible» + `ir_remote_suppresses_onsite_country_penalty` |
| KZ — fallback lane, не veto | `fc_kz_local_lane` (lane=fallback_local) + `local_market_fallback_policy` (manual_by_user, standby) + `ir_kz_fallback_lane` |
| Timezone — не gate | `timezone_policy.hard_gate=false`, treatment=risk_or_clarification; валидатор запрещает timezone-условия в constraints |
| Compensation неактивна | `compensation_policy`: status=inactive, gating/ranking=false, missing salary не негатив; валидатор запрещает comp-условия |

## 5. Interaction rules (полный список)

| id | Смысл |
|---|---|
| `ir_narrow_scope_monetization_exception` | узкий скоуп прощается монетизационным доменам |
| `ir_b2b_platform_business_exception` | B2B-штраф снимается для platform-as-business |
| `ir_remote_suppresses_onsite_country_penalty` | remote не наследует onsite-штраф страны |
| `ir_crypto_employer_not_role_veto` | crypto employer → company concern, не role veto |
| `ir_usa_relocation_requires_sponsorship` | USA relocation только с явным sponsorship |
| `ir_kz_fallback_lane` | KZ роли → fallback lane |
| `ir_platform_engineering_not_platform_business` | platform engineering не наследует platform-business позитив |
| `ir_hybrid_gm_roles_allowed` | GM/hybrid c digital business ownership проходят функциональный гейт |

## 6. Versioning & change policy

- `model_version` / `schema_version` — semver; MAJOR = breaking (удаление/
  переименование полей, изменение семантики), MINOR = новые правила/enum,
  PATCH = evidence/status/wording.
- Потребители пинят MAJOR и отклоняют неподдерживаемый. `extra=forbid`:
  неизвестное поле — ошибка валидации, а не тихий passthrough.
- Любое изменение — только с явным approval владельца (процессный SoT §8).
  Feedback-контуры могут только ПРЕДЛАГАТЬ изменения. `no_silent_learning: true`.

## 7. Нормализация enum

`strength`: critical | strong | medium | weak.
`confidence`: high | medium | low | unknown.
Драфтовые `medium_high`/`low_medium` округлены **вниз** (консервативно);
исходная формулировка сохранена комментарием в YAML и в diff-отчёте. Rationale:
шестиуровневая шкала не даёт downstream-потребителям действий, отличных от
пятиуровневой, а несогласованные значения были главным источником drift'а.
