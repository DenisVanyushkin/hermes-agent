# Step 5B — Readiness Gaps

**Дата:** 2026-07-19. Только реальные gaps; quality findings провайдера сюда не входят.

## Gaps (2, оба закрываются внутри Step 5B, blockers нет)

1. **Параметризация replay-раннеров.** `replay_full.py:84` и `replay_flagships.py:59` инстанцируют `DeterministicPhraseProvider` захардкоженно. Для cross-provider replay нужен параметр провайдера (calibration.py уже умеет). Малый bounded-слайс внутри 5B execution plan; runtime semantics не меняются.
2. **Cost/latency оси не встроены в раннеры.** Сейчас usage/latency собирает только smoke-harness. Для §9.4 нужно, чтобы benchmark-прогоны публиковали cost/latency из recordings (метаданные уже пишутся — нужен только сбор в отчёт).

## Гейты (не gaps): owner-approve на платные LLM-прогоны calibration/bounded/full replay (суммы в step5b-input-inventory.md).

## Явно НЕ blockers (benchmark findings)

Signal-prefix confusion, reject-rate 11/96, концентрация на GPNI, натянутые интерпретации, latency tail, ограниченный recall любого провайдера — это то, что benchmark измеряет, а не причины его не запускать.
