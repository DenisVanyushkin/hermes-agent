# Draft vs Normalized — Comparison Report (read-only)

**Дата:** 2026-07-19.
Draft: `docs/audit/career-preference-model.draft.yaml` (0.1.0-draft, не изменён).
Normalized: `job_intel/preference_model/career-preference-model.yaml` (1.0.0).

## 1. Структурные изменения

| Draft | Normalized | Причина |
|---|---|---|
| `hard_constraints` (плоский список) | `feasibility_constraints.constraints` со структурными `when`-условиями, verdict enum и lane | тестируемость; разделение feasibility от preference |
| `preferences.mandate` / `.company` / `.feasibility_bonuses` | `mandate_preferences` / `company_preferences` (bonuses влиты в mandate как axis=feasibility_signal) | физическое разделение доменов; bonuses — role-level сигналы |
| `anti_preferences.strong` / `.soft` | единый список с явными `level: company\|role` + `tier: strong\|soft` | mandatory correction #4 (company vs role negatives) |
| исключения прозой (`exception: monetization_core`) | 8 `interaction_rules` с priority, структурным `when` и типизированным `effect` | mandatory correction #6; override semantics |
| `confidence.revised_during_validation`, `known_data_gaps` | evidence registry + exploration policy + разрешённые вопросы закрыты user decisions | provenance-модель |
| нет | `local_market_fallback_policy`, `timezone_policy`, `compensation_policy`, `change_policy`, `evidence_registry` | user decisions 2026-07-19 + versioning policy |

## 2. Правки правил (mandatory corrections)

| # | Draft | Normalized |
|---|---|---|
| 1 | `confidence: medium_high` (restore_executive_scope, monetization_core, brand_tier), Low-Medium в отчёте | округлено вниз до `medium`/`low`; исходное значение — комментарием в YAML. Enum строгий: high/medium/low/unknown |
| 2 | `product_function_only`: «non-product functions are vetoed» | `fc_function_digital_business_ownership`: вето только при отсутствии digital business ownership; hybrid product/commercial/GM/COO допустимы (`ir_hybrid_gm_roles_allowed`) |
| 3 | `seniority_floor`: «below Director-equivalent» (title-based) | `fc_scope_below_executive`: scope/mandate-based; титул — evidence, не hard truth |
| 4 | crypto/small_local в общем списке anti | `level: company` у crypto_exchange_employer/small_local_company/outsourcing/bureaucratic; narrow_scope/internal_tools/b2b/fraud/devex/ai — `level: role` |
| 5 | narrow scope + devex + infra могли штрафоваться параллельно | `ir_platform_engineering_not_platform_business` (exclude_from) + suppress-правила: один сигнал — одна точка приложения |
| 6 | 2 исключения прозой | 8 interaction rules (полный обязательный список задания) |
| 7 | `evidence:` строка | `provenance{source_type, evidence[registry_id,detail], evidence_count, last_validated, notes}` + `status` + `override` |
| 8 | нет | `change_policy` (semver, MAJOR-pinning, owner approval, no_silent_learning) |

## 3. Новое содержание vs draft (user decisions 2026-07-19)

- **USA**: draft знал только «onsite инфизибелен вообще»; normalized кодирует
  тройную политику: onsite/hybrid USA без явного sponsorship = infeasible;
  с explicit sponsorship = общие основания; remote US = не блокируется
  (+ guard-валидатор в схеме).
- **Релокация**: draft ограничивался behavioral SG/London + «ask user»;
  normalized: почти любая страна, unknown sponsorship вне USA = uncertain
  (clarification), санкционные/нестабильные/Африка = infeasible критериями.
  `behaviorally_confirmed_relocation` из draft сохранён как evidence-деталь,
  а не ограничение.
- **KZ**: в draft отсутствовал; normalized добавляет fallback lane
  (feasible + lane=fallback_local, manual activation, standby).
- **Timezone**: draft: `remote_timezone_limits` в known_data_gaps; normalized:
  hard_gate=false, risk/clarification only.
- **Compensation**: draft: gap; normalized: политика inactive с нулевым
  gating/ranking эффектом и «missing salary ≠ негатив», защищено валидатором.

## 4. Удалено/не перенесено из draft

| Что | Почему |
|---|---|
| `feature_weights.note` | принцип перенесён в валидатор FORBIDDEN_STANDALONE_AXES и contract md; численные веса — engine concern |
| `behaviorally_confirmed_relocation/unconfirmed_relocation` списки городов | заменены user decision «почти любая страна»; города больше не constraint |
| `exploration_candidates.direct_questions`: relocation cities, comp floor, timezone limits | закрыты решениями пользователя 2026-07-19; остались big_tech/early_startup attitude |
| `axis: work_format full_async_us_remote` note «feasibility unknown» | сохранена как exploration axis, но с пометкой risk-flag only (timezone не gate) |
| industry-нейтралитет как текст | стал enforce-able инвариантом схемы |

## 5. Инварианты, которых у draft не было

unique ids; resolvable interaction targets; provenance у каждого active rule;
compensation inactive/effect-free; timezone не gate; remote-USA-не-infeasible
guard; запрет industry/country/title осей; production_integration=false;
extra=forbid на всех уровнях; schema.json ↔ Pydantic sync-тест.
