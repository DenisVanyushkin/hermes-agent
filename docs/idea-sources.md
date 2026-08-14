# Реестр внешних сигналов для `idle-idea-prompt`

`idea-signal-collector.py` — ограниченный сборщик источников для генератора
идей. Это **не** поисковый агент: он не делает широких повторных web-search и
не добавляет новые домены автоматически. Его вход — версионируемый
`config/idea_sources.yaml`; выход — структурированный brief в
`$HERMES_HOME/state/idea_signal_brief.json`.

## Контракт качества

Один прогон ограничен:

- не более 20 источников;
- один запрос и максимум один retry на источник;
- timeout не больше 10 секунд;
- максимум два сигнала от источника и два от тематической корзины;
- максимум 10 сигналов в brief;
- публикации только из последних 7 календарных дней;
- `updated_at` не заменяет `published_at`;
- источники без подтверждённой даты не дают материалы для генератора.

Статус brief означает:

| Статус | Значение для downstream |
|---|---|
| `ok` | Получено не меньше `min_usable_signals` валидных сигналов без отказов источников. |
| `degraded` | Есть пригодные сигналы, но часть источников отказала либо материалов меньше минимума. Генератор не заявляет полное покрытие тем. |
| `no_signals` | Сбор завершён честно, но свежих пригодных материалов нет. Не использовать как свежую внешнюю основу. |
| `failed` | Ни один eligible-источник не завершился корректно. Не использовать как свежую внешнюю основу. |

`idle_idea_context.py` инжектирует только текущий brief со статусом `ok` или
`degraded`, возрастом не более 30 часов и валидными `run_id`/`signals`. Все
остальные варианты намеренно превращаются в отсутствие внешнего контекста.

## Стартовый реестр

В стартовом наборе все технически проверенные источники начинают в
**probation**: endpoint отвечает, формат и даты проверены, но они ещё не имеют
истории регулярных успешных запусков. При первом включении эти источники не
кормят генератор; после порогов probation автоматически получают `active`.

- arXiv `q-bio.NC` — здоровье/энергия, исследовательские препринты;
- FTC Consumer Protection — финансы, покупки и потребительский риск;
- arXiv `cs.HC` — обучение и рабочие практики;
- CPSC Recalls — дом/быт/покупки;
- GitHub Releases `NousResearch/hermes-agent` — Hermes;
- MDN Blog и Python Insider — программирование.

Источники, упёршиеся в anti-bot/403 или не имеющие подтверждённого endpoint’а,
занесены как `candidate`, а не «тихо работающие»: APA PsycPORT, NIH Research
Matters, SEC Investor Alerts, CDC Travelers’ Health и Travel.State.gov.

Это осознанно неполное покрытие шести корзин. Пустая корзина честно уходит в
`missing_baskets`; модель не должна дорисовывать будто нашла свежие материалы.

## Как добавить источник

1. Создать запись в `config/idea_sources.yaml` со статусом `candidate`.
2. Указать владельца источника, тематическую корзину, `discovery_url`, тип и
   ожидаемые ограничения. У `candidate` не допускается «взяли случайный URL
   из выдачи». Нужен первичный/официальный источник или исследовательская база.
3. Найти конечный RSS/Atom/API endpoint и проверить вручную:

   ```bash
   curl -L --max-time 12 -A 'HermesIdeaCollector/0.1' \
     -H 'Accept: application/json, application/atom+xml, application/rss+xml, application/xml, text/xml' \
     '<endpoint>'
   ```

   Нужны: нормальный HTTP-ответ, читаемый формат, title, canonical URL и
   фактическая дата публикации.
4. Добавить fixture и тест parser’а/даты. Никаких «200 значит норм».
5. Изменить запись на `probation`, добавив `channel`, `feed_url`, лимиты и
   `requires_published_date: true`.
6. Прогнать минимум 5 запусков и набрать минимум 10 items. Автопереход в
   `active` допустим только при:
   - 5 попытках, из которых 3 успешные;
   - не менее 90% валидных publication dates;
   - не менее 30% материалов, проходящих начальный quality filter;
   - отсутствии жёстких причин suspension.
7. Проверить `idea_source_health.json` и `idea_source_events.jsonl`, затем
   включить изменение через обычный code review.

## Дисквалификация и восстановление

Состояние жизни источника хранится отдельно от registry в
`$HERMES_HOME/state/idea_source_health.json`; неизменяемые причины — в
`idea_source_events.jsonl`.

### Автоматическое понижение и suspension

- Один провал активного источника → `degraded`.
- Три последовательных провала или пять из последних семи → `suspended`.
- После 10 items менее 90% валидных publication dates → `suspended`.
- После 10 items больше 70% URL/title-дублей → `suspended`.
- Три успешных прогона у `degraded` возвращают `active`.

### Ручные операции

```bash
python3 scripts/idea_source_health.py --state-dir "$HERMES_HOME/state" status
python3 scripts/idea_source_health.py --state-dir "$HERMES_HOME/state" suspend cpsc_recalls \
  --reason 'feed changed structure; parser produces undated records'
python3 scripts/idea_source_health.py --state-dir "$HERMES_HOME/state" reactivate cpsc_recalls \
  --reason 'endpoint/parser fixed and fixture verified'
```

`suspend` не удаляет историю. `reactivate` всегда начинает новый probation
цикл и требует причину — нельзя одной кнопкой вернуть источник в `active`.
Исключение: источник, который в reviewed registry всё ещё `candidate`, нельзя
продвинуть через последовательность `suspend` → `reactivate`; сначала нужно
проверить endpoint/parser и отдельным reviewed change перевести его в
`probation`.

## Локальная проверка

Проверка без записи в runtime-state:

```bash
python3 scripts/idea_signal_collector.py --dry-run
```

Проверка с локальным state-dir в checkout:

```bash
python3 scripts/idea_signal_collector.py --state-dir /tmp/idea-signal-state
python3 scripts/idea_source_health.py --state-dir /tmp/idea-signal-state status
```

## Runtime-интеграция

`scripts/sync-runtime-scripts.sh` синхронизирует `idea_sources.yaml` рядом с
runtime-копией скриптов. Благодаря этому `idea_signal_collector.py` одинаково
работает из checkout и из `$HERMES_HOME/scripts`.

**Важно:** этот change set не меняет живые cron jobs. Для включения после
review отдельно потребуется:

1. синхронизировать runtime scripts;
2. заменить `idea-signal-collector` на no-agent/script job, вызывающий
   `idea_signal_collector.py`;
3. сохранить существующий `context_from` или убедиться, что
   `idle_idea_context.py` инжектирует файл brief;
4. сделать controlled dry-run и проверить `run_id`/`run_status` в downstream;
5. только затем включать расписание.
