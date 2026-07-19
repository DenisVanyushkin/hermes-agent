# Semantic Full Historical Replay Report (Step 4B closure, Part 2)

**Дата:** 2026-07-19. Read-only (live DB `mode=ro`); Step 3 evaluator и все
SoT НЕ менялись. Provider: deterministic-phrase/rules-1.0.0, runtime 0.1.0.
Артефакты: `artifacts/shadow-evaluator/semantic-full-replay/`
(`semantic-full-historical-replay.json`, `case-results.jsonl` 3626 строк,
`fn-candidates.jsonl`, `fp-candidates.jsonl`). Время прогона 5м42с.

## 1. Классификация корпуса (10 013 записей — ничего не отброшено молча)

| Класс | n |
|---|---|
| full_text_usable | 3 624 |
| partial_text_usable | 2 |
| title_only_source_incomplete | 2 404 (НЕ считаются extraction-failure) |
| duplicate_vacancy (company+title collapse) | 3 983 |
| malformed / excluded_smoke | 0 |

Eligible = 3 626; **extraction success 3 626 / failures 0**.

## 2. Извлечение

Evidence coverage: none 2 632 / low (1–2 факта) 874 / medium (3–5) 120.
Топ per-fact emission: product_culture_signal 495, company.scale 493,
is_crypto_exchange 161, growth_mandate 126, internal_tools 107,
scope_breadth 98, transformation_phase 47… (полный список в JSON).
Clarifications генерируются по контракту (blocking scope/digital-ownership
доминируют). **Contract gaps: 0.**

## 3. Решения до/после semantic extraction (неизменённый Step 3)

| | before | after |
|---|---|---|
| unclear | 2 929 | 2 795 |
| not_recommended | 697 | 774 |
| promising | 0 | 57 |

Transitions: `unclear→unclear` 2795; `nr→nr` 697; **`unclear→not_recommended`
77** (извлечённые internal-tools/risk/narrow-сигналы дали weak/mismatch
мандаты); **`unclear→promising` 57** (извлечённые scope/growth + company-факты).
Изменённых кейсов 134 (3.7% eligible) — при recall-ограниченном провайдере.

## 4. Критические кандидаты и re-annotation

Critical FN candidates (positive feedback × after=not_recommended): **0**.
Critical FP candidates (👎 × after=strong/exceptional): **0**.
Кандидаты human re-annotation (≥3 извлечённых факта + фидбек): 2 (в JSON).

## 5. Ограничения провайдера (не policy-дефекты)

72.6% eligible-записей без единого semantic-факта — потолок phrase-правил на
реальных формулировках; это extraction_false_negative калибровочного класса,
НЕ дефект контракта или политики (запрещено переинтерпретировать — и не
переинтерпретируется). Направление стрелки решается заменой провайдера
(LLM-гейт), не правкой SoT.
