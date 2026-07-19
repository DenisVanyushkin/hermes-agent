# User Career Preference Model — Research Report

**Дата:** 2026-07-19
**Режим:** исследование, read-only. Код не менялся. Продолжение [аудита рекомендательной системы](2026-07-19-recommendation-system-audit.md).
**Субъект:** Denis Vanyushkin — Product & Monetization Leader, Almaty, Kazakhstan.
**Источники:** очищенная история реакций (только реальный пользователь `U0B384BQHSM`; тестовые `U_TEST`/`U_AUDIT`/`U_SMOKE_*` исключены), CRM-отказы с причинами (37 declined с кодами), вербатим-ответы в 👎-тредах, полные тексты вакансий, структурированное резюме (`career_facts.json` v1.1), явные предпочтения (`preferences.yaml`), позиционирование application-материалов (`hermes_vacancy_materials_sot.md`), критерии decision support (`hermes_recruiter_decision_support.md`).
**Сопутствующий артефакт:** [career-preference-model.draft.yaml](career-preference-model.draft.yaml) — машиночитаемый черновик Source of Truth (не внедрён).

> **Гигиена данных.** В «позитивах» БД была синтетика: applied/⭐ на Canva «Digital Asset Manager (6-month Contract)» принадлежат тестовым пользователям. Чистое ядро реального пользователя: **5 applied, 1 exceptional, 4 interesting, ~13 save_for_later, 87 not_interesting**.

---

## 1. Executive Summary

Идеальная вакансия для этого пользователя — это **не индустрия, а форма мандата**: роль уровня Head of / Director / VP с широкой зоной ответственности (бизнес-линия, регион, платформа целиком), близкая к выручке (growth, монетизация, P&L), в глобальной продуктовой скейлап-компании с сильным брендом, при реалистичной географии (remote, либо релокация с визовой поддержкой — Сингапур/Лондон подтверждены поведением).

Три главных открытия:

1. **Определяющая ось — широта мандата, а не домен.** Внутри одних и тех же компаний пользователь откликается на «APAC Growth & Expansion» (Wise) и «Global Payments Network Infrastructure» (Airwallex), но отклоняет «Onboarding Experience», «Payment Fraud», «Data Product» тех же компаний с кодом role_mismatch. Индустрия не меняется — меняется охват роли.
2. **География — это feasibility-ограничение, а не вкус.** Все otклонения по коду «3» (location) — США/Канада/Индия/Израиль onsite; при этом Remote US помечается interesting/save, а релокация в Сингапур — прямой отклик. Работа-авторизация за пределами KZ не подтверждена ни для одной страны — пользователь фильтрует по реализуемости.
3. **Crypto — компания-уровневый негатив, но не абсолют.** Все 13 OKX и 6 Coinbase → 👎/declined с кодом company_quality; вербатим по OKX: «нужна крипта и китайский» — барьер непереносимой доменной экспертизы. Но платформенные роли Coinbase сначала попадали в save_for_later — форма роли привлекала, компания перевешивала.

Скрытая мотивация (реконструкция, medium confidence): выход с локального рынка на глобальный уровень с **легитимизацией через бренд** (Wise/Airwallex/Monzo/Brex — все «карьерно-конвертируемые» имена; главный риск в его собственных материалах — «not coming from a Western scale-up brand»), и **возврат executive-охвата** (P&L/Director+), который сузился в текущей роли Tribe Lead.

---

## 2. Career Motivation

### 2.1 Факты карьеры (из резюме)

Траектория 18 лет: Coca-Cola (маркетинг) → Kcell 2008–2016 (retention → acquisition → продуктовый портфель → и.о. директора B2C с полным P&L) → Alma TV (продуктовый директор, удержание выручки на падающем рынке) → Digi Камбоджа (CCO, 170+ чел., перестройка от экспатской модели к делегированной) → Beeline (Head of Strategy) → AituPay/BTS Digital 2020–2023 (коммерческий директор: запуск и масштабирование e-Wallet на 5 стран; стратегическое решение о закрытии нежизнеспособного направления) → Kcell 2023–2025 (Product Development Director: телеком+финтех+роуминг, P&L, turnaround абонентской базы, первый в Казахстане FWA) → **сейчас: Tribe Lead SuperApp** (50 FTE, культурная трансформация, спасение стратегического пивота за месяц до рискованной миграции платформы).

Образование: математика и computer science (+экономика). Языки: русский, английский; **не** китайский (вербатим-отказ OKX). Доп. навыки: AI, Python, SQL.

### 2.2 Почему меняет работу (реконструкция с evidence)

| Мотив | Evidence | Уверенность |
|---|---|---|
| **Потолок локального рынка** — все 18 лет в KZ/Камбодже; все отклики — глобальные компании; ни одного отклика на KZ-вакансии (Ticketon/Айтигенио/Kolesa отклонены) | реакции + CRM | High |
| **Легитимизация брендом** — материалы прямо называют главным риском «не из западного скейлапа» и строят стратегию компенсации «complexity, ownership, and measurable transformation in emerging markets» | vacancy_materials_sot §10.1, §4.4 | High |
| **Возврат executive-охвата** — карьерное движение 2025: Director (P&L, 4 домена) → Tribe Lead (1 продукт, без P&L); целевые роли в preferences.yaml: VP/CPO/Head of/GM/Head of Monetization | резюме + preferences.yaml | Medium-High |
| **Тяга к фазе строительства** — каждое место работы описано через turnaround/запуск/перестройку, ни одно — через «поддерживал стабильный процесс» | career_facts (категории achievements: transformation, turnaround, launch, pivot, org design) | Medium |

### 2.3 Что будет ростом, что деградацией

- **Рост:** GM/региональная экспансия, P&L бизнес-линии, VP/Head of в глобальном скейлапе, мандат на трансформацию; бренд, конвертируемый в следующую роль.
- **Деградация:** IC или low-manager (код `ic_or_low_manager_role` в таксономии; Ticketon Product Lead → «2» seniority_scope), узкий фичевый скоуп (все wise/airwallex role_mismatch), проектная/delivery-функция (`wrong_function_project_delivery` — hard blocker), внутренние инструменты (OKX Internal HR & Finance → 👎).
- **Максимальное удовлетворение** (синтез): построить/перестроить заметный продуктовый бизнес с прямым влиянием на выручку в компании, чьё имя работает на карьеру, — в роли, где он владеет и стратегией, и организацией.

---

## 3. Positive Pattern Analysis (ручной разбор всех позитивов)

### Applied (5, из них 1 exceptional)

**1–2. Airwallex — Head of Product, Global Payments Network Infrastructure** (два варианта: SG и London-relocate-SG; applied + interesting оба).
Текст роли: ядро money-movement платформы (collect/treasury/payouts/FX rails), $8B компания, 26 офисов, «founder-like energy», «zero-to-one», «true ownership». Реконструированные причины: **платформа-как-ядро бизнеса** (не фича), глобальный масштаб, «Head of»-титул, техническая сложность как валюта (его компенсация отсутствия западного бренда), явная релокация с ожидаемой визовой поддержкой, культурный код builder'а. Примечательно: он же отклонил в Airwallex «Payment Fraud» (узкий домен) и «Embedded Finance» (код 8/other).

**3. Wise — Product Director, APAC Growth & Expansion** (applied + ⭐ единственная + interesting; плюс save на Product Lead-версию той же роли).
Реконструкция: **региональная экспансия = квази-GM мандат** — рынок целиком, а не фича; growth в титуле (сильнейший позитивный маркер: lift 2.55); APAC = его сильная сторона (emerging markets, Камбоджа, ЦА); Сингапур реалистичен; бренд Wise. Это эталон «идеальной вакансии» — единственная ⭐ за всю историю.

**4. Monzo — Senior Product Director, Business Banking** (London).
Реконструкция: бизнес-линия (800k бизнес-клиентов) ≈ P&L-охват; B2C-ДНК компании; mission-driven бренд; Лондон приемлем. Контраст: «Flex (Borrowing)» того же Monzo — declined (role_mismatch): продуктовая линия уже, дальше от его монетизационной идентичности.

**5. Brex — Director of Product, Growth/AI** (Vancouver).
Реконструкция: growth-мандат (acquisition+onboarding целиком) + AI как инструмент. Осложнение: та же вакансия позже получала 👎 с кодами location(3)/data_quality(7) на повторных отправках — Ванкувер был скорее «применился несмотря на», и раздражение от resend'ов. Вывод: при исключительной роли North America не абсолютный блокер, но трение.

### Interesting (не applied)

**Affirm — Senior Director, Product Management** (Remote US): широкий senior-мандат, B2C-кредитование, **remote** — роль нравится, feasibility (US work auth) неясна → не конвертировалось в отклик.

### Save_for_later (сигнал «нравится, но есть препятствие»)

Wise **Pricing** и **Acquiring** (Лондон) — узкие домены, но это **ядро монетизации** — его профессиональная идентичность («Head of Monetization» в целевых ролях); исключение из правила «узкий скоуп = минус». Affirm Financial Platforms (Remote US/Canada), Coinbase Financial Engineering / Compliance Automation (сохранены, позже компания отклонена целиком), Adyen India GPM (роль ок, гео нет), Payoneer Core AI Platform (Израиль), Canva Product Lead Content Group (Сидней). Паттерн: **save = хорошая форма роли × сомнительная реализуемость** (гео/компания).

---

## 4. Negative Pattern Analysis (87 not_interesting, 37 declined)

Коды из тредов (пользователь отвечал номерами меню): 1=role_mismatch (12), 3=location (12), 4=company_quality (16), 2=seniority, 5=industry/thesis, 6=comp, 7=data_quality (9).

| Свойство-триггер отказа | Кейсы | Глубинная причина (реконструкция) |
|---|---|---|
| **Узкий фичевый/доменный скоуп** | Wise Onboarding Experience, Data Product, Financial Crime; Airwallex Payment Fraud; Coinbase Payments Core, Developer/Core Infrastructure; Adyen Developer Experience; Monzo Flex | Низкая переносимость, нет стратегического влияния, шаг вниз от портфельного управления. «Executive scope» для него = ширина, не глубина |
| **Операционные/внутренние функции** | OKX Internal HR & Finance Systems, VIP Products; Canva FP&A; Block Head of Strategic Product Sales | Операционная/функциональная роль вместо продуктовой; сервисная позиция без ownership |
| **Компания crypto-биржа** | OKX ×9, Coinbase ×6 (все → 4 company_quality) | Не «industry mismatch», а качество/риск компании + непереносимая доменная экспертиза: «нужна крипта и китайский» (вербатим). Регуляторный/репутационный риск для карьерной траектории |
| **Onsite в недоступной географии** | Adyen NA/India, Brex (US/Canada), Affirm Shopping & Offers, Payoneer Израиль | Нет work authorization нигде за пределами KZ; onsite без визовой поддержки = нереализуемо, какой бы ни была роль |
| **Локальный рынок / малый масштаб / несоответствие уровня** | Ticketon (Афиша) — «2»; Айтигенио EdTech CPO — «5»; MAREE CPO — все коды сразу (1–6) | Возврат в локальный контекст = деградация; малая компания без бренда не решает мотивационную задачу |
| **Сломанные карточки** | «7» data_quality ×9 (Brex GPM, Affirm и др.) | Не предпочтение, а мусор пайплайна (resend'ы, битые данные) — исключить из обучения |

Важно: **87 негативов ≠ 87 «плохих ролей»**. Значительная часть — повторные отправки одной вакансии (см. аудит) и мусор данных; в declined-ядре ~30 уникальных осмысленных отказов.

---

## 5. Latent Preferences (скрытые предпочтения)

| # | Гипотеза | Evidence | Conf. |
|---|---|---|---|
| L1 | **Мандат важнее домена**: платит вниманием за «регион/линия/платформа целиком», игнорирует «фича/домен» | Внутрикомпанейские контрасты Wise/Airwallex/Monzo (§3–4) | **High** |
| L2 | **Монетизация — личная идентичность**: pricing/acquiring/monetization привлекают даже при узком скоупе | Save на Wise Pricing/Acquiring; headline резюме; «Head of Monetization» в целевых ролях | High |
| L3 | **Строитель/трансформатор, не оператор**: тянется к фазам build/expansion/turnaround, избегает maintenance | 100% achievements в резюме — трансформации/запуски/пивоты; ⭐ на expansion-роль; 👎 на internal tools | Medium-High |
| L4 | **Бренд как карьерный актив**: все отклики — компании с международно узнаваемым именем | 5/5 applied = Wise/Airwallex/Monzo/Brex; §10.1 материалов называет отсутствие бренда главным риском | High |
| L5 | **Feasibility-фильтр сильнее want-фильтра**: сначала «возможно ли», потом «хочу ли» | Save вместо apply для Remote US ролей; отклики только туда, где есть relocation-механика | High |
| L6 | **Комфорт с emerging markets как дифференциатор**: APAC/ЦА роли резонируют | ⭐ APAC Growth; карьера KZ+Камбоджа; позиционирование «emerging-market complexity» | Medium |
| L7 | **AI — инструмент, не предмет**: growth/AI ок, AI-инфраструктура — слабее | Applied Brex Growth/AI; save (не apply) Payoneer Core AI Platform; lift ai_ml 0.78 | Low-Medium |
| L8 | **Гибридная идентичность продукт×коммерция×стратегия** — чисто-технические продуктовые роли (infra/devex) не привлекают | Отказы Coinbase Core/Developer Infra; резюме: CCO/коммерческий директор в анамнезе; §9.11 «intentionally hybrid» | Medium-High |
| L9 | Не любит зрелые забюрократизированные структуры | Код таксономии `company_too_big_bureaucratic`; `enterprise_bureaucracy −15` в его preferences.yaml; Digi-достижение = демонтаж централизованной модели | Medium (мало поведенческих кейсов) |

---

## 6. Preference Ontology (что нравится — дерево)

```
PREFERENCE ROOT
│
├── MANDATE (широта и власть роли) ................................ [ядро, High]
│   ├── Scope breadth
│   │   ├── Business line ownership (Monzo Business Banking)
│   │   ├── Regional/market ownership (Wise APAC G&E) ← эталон
│   │   ├── Platform-as-the-business (Airwallex GPNI)
│   │   └── Portfolio (мульти-домен, как Kcell Director)
│   ├── Revenue proximity
│   │   ├── P&L ownership
│   │   ├── Growth (acquisition/expansion) ← сильнейший титульный маркер
│   │   └── Monetization / Pricing / Acquiring ← идентичность (L2)
│   ├── Authority
│   │   ├── Strategy ownership (не execution чужой стратегии)
│   │   ├── Org design / operating model
│   │   └── Executive visibility (board-level exposure)
│   └── Seniority: Head of / Director / VP / GM / CPO
│
├── TRANSFORMATION (фаза и характер работы) ....................... [High]
│   ├── Market expansion / new market entry
│   ├── Turnaround (падающая база → рост)
│   ├── Zero-to-one в рамках платформы (новая линия)
│   └── Org/culture transformation (reactive → product-driven)
│
├── BUSINESS SHAPE .................................................. [Medium-High]
│   ├── B2C / consumer-scale (или SMB-масс: Monzo BB)
│   ├── Platform / ecosystem / superapp
│   ├── Subscriptions / transактивная монетизация
│   └── Mobile-first
│
├── COMPANY (см. §8) ................................................ [High]
│   ├── Global scale-up с брендом
│   ├── High growth phase
│   ├── Product-driven culture
│   └── Emerging-markets footprint как плюс (L6)
│
└── FEASIBILITY (пропускной фильтр, не «предпочтение») ............ [High]
    ├── Remote-first
    ├── Relocation с visa support: Singapore ✓, London ✓ (поведенчески)
    └── RU/EN рабочие языки
```

## 7. Anti-Preference Ontology (что отталкивает)

```
ANTI-PREFERENCE ROOT
│
├── ABSOLUTE (практически гарантируют отказ)
│   ├── Onsite без визового пути (US/Canada/India/Israel onsite: 12 отказов, 0 исключений
│   │     среди onsite; Brex Vancouver — пограничный контрпример, но кончился 👎 location)
│   ├── Непереносимая доменная экспертиза как входной барьер
│   │     («нужна крипта и китайский»; регуляторно-специализированные роли)
│   ├── IC / low-manager уровень (Ticketon; ic_or_low_manager_role)
│   ├── Функция ≠ продукт: sales (Block), FP&A (Canva), delivery/project management
│   │     (hard blocker в его же preferences: pure_project_management −30)
│   └── Санкционная/репутационная география и компании (RU/BY — из preferences.yaml)
│
├── STRONG (сильный негатив, преодолим только исключительной ролью)
│   ├── Crypto-биржа как работодатель (OKX 13/13, Coinbase 6 declined —
│   │     но платформенные роли Coinbase были в save: негатив на КОМПАНИЮ, не форму роли)
│   ├── Узкий фичевый скоуп в большой компании (onboarding, fraud, data product,
│   │     developer experience) — «шаг вниз» при формально подходящем титуле
│   ├── Internal tools / back-office (0 позитивов, стабильные 👎)
│   └── Локальная компания малого масштаба (Ticketon, Айтигенио, MAREE)
│
└── SOFT (нежелательно, но допустимо)
    ├── B2B/enterprise-центричность (lift 0.53 — но Airwallex/Brex B2B-ish и applied:
    │     смягчается платформенностью и масштабом)
    ├── Fraud/risk/compliance-нагруженность роли (lift 0.71)
    ├── Чисто-инфраструктурные технические роли (Coinbase Core Infra)
    ├── AI-как-предмет (AI-инфраструктура) при AI-как-инструмент = плюс (L7)
    └── Зрелая бюрократическая структура (enterprise_bureaucracy)
```

**Ключевое различение:** «crypto», «USA», «B2B» — не абсолютные вето. Абсолютны: нереализуемая география, непереносимый доменный барьер, уровень ниже executive, не-продуктовая функция.

---

## 8. Company Preference Model

Что делает компанию привлекательной (независимо от вакансии):

| Свойство | Полярность | Evidence | Conf. |
|---|---|---|---|
| Глобальный масштаб (мульти-регион, 200+ рынков) | +++ | 5/5 applied глобальны; Airwallex «26 offices» | High |
| Узнаваемый бренд tier-1 скейлапа | +++ | Wise/Monzo/Brex/Airwallex vs отказы локальным; L4 | High |
| Фаза активного роста/экспансии | ++ | ⭐ на expansion-роль; резюме-идентичность | Medium-High |
| Product-driven культура («founder energy», ownership-язык) | ++ | Текст Airwallex; anti: weak_product_culture −20 в его preferences | Medium |
| B2C или масс-SMB модель | ++ | Monzo, Wise, Affirm; preferences.yaml business_models | Medium-High |
| Платформа/экосистема в ядре | ++ | Airwallex GPNI, superapp-бэкграунд | High |
| Emerging-markets присутствие | + | APAC ⭐; L6 | Medium |
| Crypto-биржа | −−− | OKX/Coinbase: 19 declined | High |
| Локальная KZ/RU-рынок компания | −− (для этого поиска) | Ticketon, Айтигенио, Kolesa, MAREE | High |
| Аутсорсинг/агентство | −−− | outsourcing_company −40 (его собственный вес) | High (заявлено) |
| Big Tech (FAANG) | ? | ни одного кейса в данных | Unknown |
| Ранний стартап (<100 чел.) | ? | ни одного кейса | Unknown |

Отдельно: fintech/telecom как индустрии — **нейтральны** (аудит: payments 41%/41%). Привлекательность Wise ≠ «fintech», а = глобальный масштаб + бренд + рост + платформа. Это же объясняет, почему 20+ других fintech-ролей отклонены.

## 9. Role Preference Model

| Свойство роли | Полярность | Evidence |
|---|---|---|
| Growth / Expansion в мандате | +++ | lift 2.55; ⭐ Wise; applied Brex |
| Регион/бизнес-линия/платформа целиком | +++ | §3 vs §4 контрасты |
| Монетизация/pricing в ядре | ++ | saves Wise Pricing/Acquiring; идентичность |
| P&L (явный или де-факто) | ++ | резюме; целевые роли; no_pnl_ownership = hard blocker |
| Org-мандат (построить/перестроить команду) | ++ | резюме-паттерн; 50–170 FTE опыт |
| Executive exposure (board, funding rounds) | + | BTS/AituPay достижения |
| «Head of»/«VP»/«GM» титул | + | целевые роли; Director — нейтрален (lift 0.77 — размыт узкими Director-ролями) |
| Relocation-пакет в тексте роли | + | lift 2.68; applied на «Relocate to Singapore» |
| Узкий фичевый домен (onboarding/fraud/data/devex) | −− | 12× role_mismatch |
| Internal tools / поддерживающие функции | −−− | 0 pos |
| Не-продуктовая функция (sales/FP&A/delivery) | −−− (вето) | Block, Canva FP&A |
| IC/низкий менеджерский уровень | −−− (вето) | Ticketon «2» |
| Чистая инфраструктура/DevEx | − | Coinbase/Adyen отказы (но платформа-как-бизнес = +++: различать!) |

Тонкое, но важное различение: **«платформа как бизнес» (GPNI — деньги ходят через неё) ≠ «платформенная инженерия» (Core Infrastructure & Reliability)**. Первое — сильный позитив, второе — негатив.

---

## 10. Feature Inventory (каталог признаков для recommendation engine)

Типы: B=boolean, C=categorical, O=ordinal, N=continuous.

**Мандат роли**
| Признак | Тип | Пример | Зачем |
|---|---|---|---|
| scope_breadth | O: feature < domain < business_line < region < portfolio | Wise APAC=region | ядро модели (L1) |
| revenue_proximity | O: support < enabling < indirect < direct_pnl | Monzo BB=direct | ключевой дискриминатор |
| growth_mandate | B | Brex Growth=true | сильнейший титульный маркер |
| monetization_core | B | Wise Pricing=true | идентичность (L2) |
| org_mandate | B (строит/перестраивает команду?) | true | резюме-паттерн |
| seniority_level | O: IC < manager < senior_mgr < director < head_vp < c_level | head_vp | вето ниже director |
| title_family | C: product/growth/gm/commercial/strategy/ops/sales/finance | product | вето sales/finance/delivery |
| executive_exposure | B (board/C-suite visibility) | true | мотивация |
| transformation_phase | C: build/scale/turnaround/optimize/maintain | scale | L3: maintain = минус |
| domain_transferability | O: generic < adjacent < specialized < barrier | OKX=barrier | вербатим-кейс |
| people_scope | N (FTE) | 50 | привычный масштаб 20–170 |

**Компания**
| Признак | Тип | Пример | Зачем |
|---|---|---|---|
| company_global_scale | O: local < regional < multi_region < global | Airwallex=global | High-предпочтение |
| brand_recognition | O: unknown < niche < known < tier1_scaleup < big_tech | Wise=tier1 | L4 |
| company_stage | C: seed/growth/scaleup/public/mature | scaleup | growth-фаза = + |
| company_size_fte | N | 2200 | сладкое пятно 500–5000 (гипотеза) |
| business_model | C: b2c/smb_mass/b2b_enterprise/marketplace/platform | smb_mass | B2C/масс = + |
| is_crypto_exchange | B | OKX=true | сильный негатив |
| is_outsourcing | B | — | вето (заявлено) |
| product_culture_signal | B (ownership-язык в тексте) | true | medium |
| emerging_markets_footprint | B | true | L6 |
| industry_vertical | C: payments/banking/crypto/telecom/edtech/… | payments | нейтрален сам по себе — нужен только для exploration-учёта |

**Feasibility**
| Признак | Тип | Пример | Зачем |
|---|---|---|---|
| work_format | C: onsite/hybrid/remote | remote | фильтр |
| location_feasibility | O: home < relocation_supported < relocation_unclear < infeasible | SG=supported | 12 отказов кода «3» |
| visa_support_stated | B | Airwallex=true | конвертирует save→apply |
| language_requirement | C: en/ru/other_required | OKX=zh → вето | вербатим |
| timezone_offset_hours | N | +3 | гипотеза для remote |
| comp_range | N | — | 0 данных — собрать |

**Служебные (не предпочтения, но нужны движку)**
card_data_quality (B — код «7» = исключить из обучения), is_resend (B), reaction_source_user (C — фильтр тестовых), previous_company_feedback_ratio (N — company prior).

## 11. Preference Strength × Confidence (свод)

| Вывод | Сила | Уверенность |
|---|---|---|
| Feasibility-гейт географии (onsite без визы = отказ) | Critical | High |
| Не-продуктовая функция = отказ | Critical | High |
| Уровень ниже Director-эквивалента = отказ | Critical | High |
| Доменный барьер (язык/крипто-экспертиза) = отказ | Critical | High (1 вербатим + паттерн) |
| Широта мандата (region/line/platform) | Strong | High |
| Growth/expansion в мандате | Strong | High |
| Tier-1 бренд скейлапа | Strong | Medium-High |
| Crypto-биржа как работодатель | Strong (негатив) | High |
| Узкий фичевый скоуп | Strong (негатив) | High |
| Монетизация/pricing притягивают | Medium | Medium-High |
| B2C/масс-модель | Medium | Medium |
| Фаза build/scale vs maintain | Medium | Medium |
| Emerging markets как плюс | Weak | Medium |
| B2B-enterprise минус | Weak | Medium (Airwallex/Brex контрпримеры) |
| AI-как-инструмент плюс / AI-как-предмет минус | Weak | Low |
| Бюрократия/зрелость минус | Weak | Low (заявлено, мало поведения) |
| Компенсационный порог | Unknown | — (0 данных) |
| Big Tech привлекательность | Unknown | — |

---

## 12. Unknown Areas + план controlled exploration

Система показывала пользователю почти исключительно fintech/AI-скейлапы поздней стадии. Неизвестно ничего про:

| Область | Почему важно проверить | Как проверить (controlled, не шум) |
|---|---|---|
| **Индустрии**: HealthTech, Gaming, DevTools, ClimateTech, Mobility, Travel, EdTech-global | индустрия нейтральна ⇒ хорошие мандаты вне fintech могут быть золотом | 1–2 exploration-карточки/нед.: только роли, проходящие Critical-гейты (region/line-мандат, global scale-up, feasible geo) — меняется ТОЛЬКО индустрия |
| **Telecom-возврат** (Vodafone/Orange/e& digital arms) | в резюме — ядро экспертизы, в выдаче не было ни одного | то же |
| **Big Tech** (Google/Meta/Amazon уровня Director) | неизвестно, интересен ли трек против скейлапов | показать 2–3 роли с сильным мандатом |
| **Ранние стартапы** (Series A-B, Head of Product) | больше мандата, меньше бренда — какой trade-off выберет? | пары «стартап vs скейлап» с похожим мандатом |
| **GM/COO-траектория** (не product-титулы) | гибридная идентичность (L8) допускает | 1–2 GM Digital / GM Market роли |
| **Компенсация** | 0 данных | добавить в 👎-тред вопрос «если бы комп был X — изменилось бы?» только для отказов без других причин |
| **Разрешённые города релокации** | подтверждены только SG/London; Dubai/Amsterdam/Berlin неизвестны | спросить напрямую один раз (не exploration) — это constraint, не вкус |
| **Remote-глубина**: полностью-async US-компания с +11h разницей — реализуемо? | Remote US сейчас в подвешенном save | 1 пробная карточка + прямой вопрос |

Принцип: exploration меняет **одну ось за раз** при зафиксированных Critical-гейтах; каждая exploration-карточка помечается, и её реакция весит больше обычной (информационная ценность).

## 13. Validation (попытки опровергнуть собственные выводы)

| Вывод | Поиск контрпримера | Результат |
|---|---|---|
| «Любит глобальные компании» | Есть ли позитивы на локальные? | Meteoro Platform «Product Lead» — save_for_later (неизвестная компания). Слабый контрпример: save, не apply; вывод устоял, но не абсолютен |
| «Не любит USA» | Позитивы с США? | **Опровергнуто в исходной форме**: Affirm Remote US = interesting, saves на Remote US/Canada; Brex Vancouver = applied. Переформулировано: негативна не страна, а **onsite-нереализуемость**; remote/relocation-supported США приемлемы |
| «Не любит crypto» | Позитивы на crypto? | Частично опровергнуто: Coinbase FinEng/Compliance Automation были в save. Уточнение: негатив — **компания-биржа**, а не наличие крипто-контекста; форма роли может временно перевешивать |
| «Любит fintech» (гипотеза старой системы) | Отказы на fintech? | 30+ declined fintech-ролей — подтверждён нейтралитет индустрии |
| «Не любит узкий скоуп» | Позитивы на узкие роли? | **Найден системный контрпример**: Wise Pricing/Acquiring (узкие, но монетизационные) → правило уточнено: узкий скоуп допустим, если домен = монетизация (L2) |
| «Не любит B2B» | Позитивы на B2B? | Airwallex (B2B payments) и Brex (B2B spend) — applied. Вывод ослаблен до Soft: B2B-масштабная платформа приемлема, негативен скорее enterprise-sales-контекст |
| «Applied = сильный позитив» | Все ли applied чистые? | Canva DAM «applied» — синтетика тестовых юзеров (исключено); Brex applied позже сопровождался 👎 location — applied тоже бывает амбивалентным |
| «Не любит Director-титулы» (lift 0.77) | — | Ложный сигнал: 3/5 applied — Director-титулы. Lift искажён массой узких Director-ролей; титул не признак, признак — мандат |

Главный итог валидации: **пять из восьми первичных формулировок пришлось уточнить**. Это подтверждает центральный тезис: предпочтения живут на уровне свойств мандата и реализуемости, любые категориальные ярлыки (страна, индустрия, титул) — прокси, которые ломаются на контрпримерах.

## 14. Recommendation Engine Implications (границы применения модели)

Без проектирования алгоритмов — только следствия для любого потребителя модели:

1. **Порядок применения:** сначала Critical-гейты (feasibility, функция, уровень, доменный барьер) — они объясняют ~60% отказов; затем скоринг Strong/Medium-предпочтений; категориальные признаки (индустрия, страна, титул) использовать только как сырьё для вычисления свойств, не как самостоятельные веса.
2. **Единый Source of Truth:** [career-preference-model.draft.yaml](career-preference-model.draft.yaml) — черновик для всех потребителей (scoring, CV-tailoring, company discovery, recruiter-переписка). CV-tailoring уже фактически использует эту модель неявно (позиционирование = Strong-предпочтения); расхождений между материалами и поведением не обнаружено.
3. **Гигиена обучения:** исключать тестовых пользователей, resend-дубли и код «7» из любого обучения; company prior строить по чистым отказам.
4. **Данные, которых не хватает** (по убыванию ценности): компенсационный порог, список допустимых городов релокации, remote-timezone границы, индустриальные exploration-реакции.

---

## Приложение. Ответы на success criteria

- **Идеальная вакансия:** Head of Product / Product Director / GM с мандатом на регион, бизнес-линию или платформу-как-бизнес, с growth/монетизационной осью и P&L, в глобальном product-driven скейлапе с брендом уровня Wise/Airwallex, remote или с релокацией + виза (SG/London подтверждены). Эталон из данных: Wise «Product Director — APAC Growth & Expansion».
- **Гарантируют отказ:** onsite без визового пути; не-продуктовая функция; уровень ниже Director; входной доменный барьер (язык, крипто-экспертиза); локальная компания малого масштаба.
- **Подтверждено данными:** иерархия §11 со strength/confidence.
- **Остаются гипотезами:** фаза компании, бюрократия, AI-полярность, emerging-markets бонус, B2B-полярность.
- **Собрать:** §12 (comp, города, timezone, индустрии через controlled exploration).
- **Минимальная формальная модель:** YAML-черновик рядом с этим документом.
