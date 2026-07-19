# Shadow Evaluator SoT — Review & Readiness Report (Slice D)

**Дата:** 2026-07-19. Артефакты: decision SoT (human), decision-contract.yaml
(+schema+tests), golden decision cases, ambiguity inventory.

## 1. Sources reviewed

Полный список — ambiguity inventory §1. Legacy-код инспектировался только для
migration constraints; его логика не авторитетна.

## 2. Decisions inherited without change (из Step 1/2)

Feasibility constraints и их словарь; interaction rules и priorities; KZ lane
инварианты (sponsorship-независимость); timezone не gate; compensation
inactive; unknown ≠ false; evidence-иерархия источников Step 2; запрет
numeric weights; no silent learning; словарь fit-выхода задания 3A.

## 3. Decisions newly formalized

Матрица рекомендаций (36 ячеек + терминальные правила); caps-механизм;
uncertain-グрейд unknown'ы (work_format/country/digital-ownership);
правило «≥3 concerns → band −1, mismatch недостижим concerns'ами»;
runtime-семантика 6 interaction-эффектов (gate = документирующий no-op;
allow = prevent-match, не suppress); качественная confidence-модель
(min-по-критическим-фактам, без усреднения); clarification/explanation
контракты; fallback: общий словарь + lane-маркер; replay-протокол и
disagreement taxonomy.

## 4. Ambiguities found

12 позиций — ambiguity inventory §2 (A1–A12). Все закрыты в SoT, кроме
вынесенных владельцу (§7).

## 5–6. Alternatives considered → recommended policy

| Вопрос (§25 задания) | Альтернативы | Принято (rationale) |
|---|---|---|
| 1. uncertain → strong? | (a) да при clarification-grade; (b) никогда | **(b)**: жёсткий cap promising — проще, честнее, соответствует save_for_later-поведению; (a) требует градации «серьёзности» unknown → скрытая шкала (R2) |
| 2. один sponsorship-вопрос → unclear? | forced unclear / cap promising | **cap promising**: uncertain ≠ непознаваемо; unclear зарезервирован за unknown band'ами |
| 3. exceptional mandate + weak company | strong / promising | **promising**: бренд — ядро мотивации (L4); company weak подрывает карьерную конверсию |
| 4. company mismatch → всегда nr? | да / promising при exceptional mandate | **да**: mismatch зарезервирован за нарушениями карьерно-критической семантики (outsourcing, small-local-core); «мягкие» негативы вроде crypto капятся в weak interaction-правилом и дают promising |
| 5. concerns → mismatch без чисел? | нет / порог | **нет**: mismatch только от strong anti/critical конфликта; ≥3 concerns понижают band на 1 (единственный счётный порог, объявлен явно) |
| 6. coverage для exceptional | — | критические факты секции ≥ medium + ≥1 strong support high (contract confidence_policy) |
| 7. title-only > promising? | да по бренду / нет | **нет**: cap_incomplete_text; иначе возвращается keyword/brand-интуиция legacy (R1) |
| 8. Wise Pricing/Acquiring | strong / promising | **strong** по band'у (monetization exception), но фактически promising пока текст title-only; с полным текстом → strong |
| 9. KZ fallback при standby | скрыть / оценивать | **оценивать в shadow**, lane-маркер + fallback_state=standby, delivery disabled |
| 10. crypto employer cap | без cap / cap promising | **cap promising** + company_fit ≤ weak; exploration-eligible |
| 11. strong mandate + company unknown | strong / promising | **promising** + recommendation_changing clarification |
| 12. unknown → unclear vs confidence | — | unclear только когда band unknown (mandate) или матрица даёт unclear; остальное — confidence + clarifications |
| 13. big-tech/early-startup exploration | eligible / нет | **нет** до ответов владельца (это direct questions Step 1) |

## 7. Unresolved owner decisions

- **O1. Словарь recommendation vs process SoT §4** (apply/investigate/save/
  exploration/reject). SoT §8 даёт приоритет процессному документу. Принятое
  здесь: словарь 3A + mapping-таблица (Decision SoT §1). Требуется формальная
  поправка process SoT §4 (или отклонение mapping'а).
- **O2. Утвердить матрицу §7 и caps §8 целиком** — это новая продуктовая
  политика, впервые записанная; до approve implementation не начинается.
- **O3. Wise re-fetch**: подтвердить prerequisite replay (re-fetch полных
  текстов title-only ядра) — иначе acceptance-критерий «Wise APAC в верхнем
  band» проверяем только на synthetic-ожидании gd_wise_apac_fulltext.
- **O4. cap_crypto_employer = promising**: подтвердить (агрессивнее, чем
  «company concern» Step 1, но мягче исторических 19/19 declines).
- **O5. big_tech/early_startup**: ответить на direct questions или явно
  разрешить exploration до ответа.
- **O6. Порог «≥3 concerns → −1 band»**: подтвердить или заменить (это
  единственная счётная конструкция контракта).

## 8. Implementation readiness

Готово: полный словарь выходов, детерминированный граф, полная матрица
(валидируется схемой), unknown-таблица по полям, executable-семантика
interactions, confidence-политика, clarification/explanation форматы,
fallback/exploration, replay-протокол, 24 golden decision cases,
консистентность golden↔matrix проверяется тестом. Future coding agent может
реализовать evaluator трансляцией контракта; собственных product-решений не
остаётся (кроме O1–O6, которые должны быть закрыты ДО старта).

## 9. Risks of premature implementation

1. Реализация до O2 закрепит неутверждённую матрицу как поведение.
2. Реализация до O3 даст ложный сигнал «флагманы не проходят» в replay.
3. Пропуск approve O1 создаст третий словарь в Slack/CRM-интеграциях позже.
4. Соблазн «дочислить» confidence/concerns в коде — запрещено: любые новые
   пороги должны сначала появиться здесь.
