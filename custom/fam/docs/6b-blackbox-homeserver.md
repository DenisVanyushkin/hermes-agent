# 6b — Внешний blackbox-мониторинг gateway (homeserver)

**Что:** внешняя liveness-проба «жив ли gateway Гермеса-для-Амины (hermes-home,
VM 200, 192.168.20.10)» с homeserver, где уже крутится Prometheus + blackbox +
Alertmanager. Мёртвая VM не может алертить о себе — поэтому проба ВНЕШНЯЯ. Это
закрывает пункт сквозного DoD «выключенный gateway → алерт».

**Локация:** `den@192.168.1.21` (LAN1), swarm `prometheus_*`. Конфиги:
`/srv/services/prometheus/etc/prometheus.yml`,
`/srv/services/prometheus/etc/alert_rules.yml`,
`/srv/services/monitoring/blackbox/blackbox.yml`,
`/srv/services/prometheus/alertmanager/alertmanager.yml`.

## Факты (сверено 2026-07-14)
- Достижимость LAN→hermes-home:80 **ОТКРЫТА**: с homeserver
  `curl -m5 http://192.168.20.10:80/` → **HTTP 200**. Дырка firewall уже есть,
  править ничего не нужно.
- Модуль blackbox `http_2xx` уже определён (`blackbox.yml`), джоб `blackbox-http`
  уже есть (`prometheus.yml`).
- Существующий target `name: hermes` в `blackbox-icmp` — это **VPS**
  (75.119.154.183), НЕ hermes-home. hermes-home ранее не мониторился.
- Роутинг: `severity="critical"` → receiver `nm-critical` → webhook
  `notification-manager` (существующий канал алертов Денису). Значит алерт с
  `severity: critical` дойдёт Денису тем же путём, что и остальные.

## Изменения (аддитивные, применяются на homeserver)

### 1. Prometheus target — `prometheus.yml`, джоб `blackbox-http`
Добавить отдельный `static_configs`-элемент в конец списка `targets` джоба
`blackbox-http` (лейбл нужен для точного match в правиле):
```yaml
      - targets: ["http://192.168.20.10:80"]
        labels: {name: hermes-home}
```
Джоб уже делает relabel `__address__ → __param_target → instance` и шлёт на
`blackbox-exporter:9115`, так что новый target подхватится тем же механизмом.

### 2. Alert-правило — `alert_rules.yml`, группа `availability`
```yaml
  - alert: HermesHomeGatewayDown
    expr: probe_success{job="blackbox-http", instance="http://192.168.20.10:80"} == 0
    for: 3m
    labels: {severity: critical, source: prometheus}
    annotations:
      summary: "Гермес (Амина) gateway недоступен"
      description: "HTTP-проба к hermes-home gateway (192.168.20.10:80) не 2xx уже 3 минуты."
```
`severity: critical` → маршрут `nm-critical` → notification-manager → Денис.

### 3. Валидация и перезагрузка (ОБЯЗАТЕЛЬНО валидировать до reload)
```bash
# промтул внутри контейнера prometheus:
cid=$(docker ps --filter name=prometheus_prometheus -q | head -1)
docker exec "$cid" promtool check config /etc/prometheus/prometheus.yml
docker exec "$cid" promtool check rules /etc/prometheus/etc/alert_rules.yml  # путь по монтированию
# при OK — reload без рестарта:
curl -sS -X POST http://localhost:9090/-/reload && echo reloaded
```
Если валидация не прошла — **откатить** правки из `.bak` до reload.

## Приёмка (verification)
1. **Не разрушающе (можно сразу):** после reload проба зелёная —
   `curl -sG 'http://localhost:9090/api/v1/query' --data-urlencode 'query=probe_success{instance="http://192.168.20.10:80"}'`
   → значение `1`. Правило `HermesHomeGatewayDown` видно в
   `http://localhost:9090/rules` в состоянии `inactive`.
2. **Разрушающе (ТРЕБУЕТ ДЕНИСА, координировать окно — прерывает ассистента
   Амины):** на VM `systemctl --user stop hermes-gateway.service` → в течение
   ~3 мин `probe_success` → 0, правило → `firing`, алерт приходит Денису через
   notification-manager. Затем `systemctl --user start hermes-gateway.service`
   → проба → 1, алерт гаснет. Зафиксировать PASS здесь.

## Статус применения
- [x] Достижимость сверена (HTTP 200, 2026-07-14).
- [ ] Target + правило применены на homeserver, промтул OK, reload, проба
      зелёная (probe_success=1) — заполнить при применении.
- [ ] Разрушающая приёмка (stop→alert→start) — с Денисом.

## Откат
Правки чисто аддитивные. Откат: убрать добавленный target-блок и правило
`HermesHomeGatewayDown`, `curl -X POST .../-/reload`. Бэкапы кладём рядом как
`*.bak-6b-YYYYMMDD` (стиль существующих `.bak` в каталоге).
