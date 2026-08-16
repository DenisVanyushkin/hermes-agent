#!/usr/bin/env bash
# Поднимает сетевое пространство имён для браузерного профиля LinkedIn.
#
# WireGuard — единственный туннель, переживающий такой переезд без демона
# внутри namespace: `ip link set ... netns` переносит интерфейс, а UDP-сокет
# остаётся в исходном namespace и продолжает пользоваться маршрутами хоста.
# Поэтому внутри ln-eg может быть ровно один маршрут по умолчанию — в туннель.
#
# veth-пара нужна только для доступа оператора к noVNC. Маршрута по умолчанию
# через неё нет и быть не должно: иначе падение туннеля выпустило бы браузер
# напрямую, с датацентрового адреса, что и есть предотвращаемый отказ.
set -euo pipefail

# Скрипт вызывается и вручную, и из browser-desktop-bootstrap.sh, который
# окружения не передаёт. Поэтому env-файл загружается здесь, до чтения
# обязательных переменных: иначе запуск десктопа падает на первой строке.
ENV_FILE="${LINKEDIN_NETNS_ENV:-/etc/job-intel/linkedin-netns.env}"
if [[ -r "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
  set +a
fi

NETNS="${LINKEDIN_NETNS:-ln-eg}"
WG_IF="${LINKEDIN_WG_IF:-wg0-ln}"
WG_CONF="${LINKEDIN_WG_CONF:-/etc/wireguard/wg0-ln.conf}"
WG_ADDR="${LINKEDIN_WG_ADDR:?LINKEDIN_WG_ADDR не задан (адрес пира из Firewalla)}"
VETH_HOST="veth-ln-host"
VETH_NS="veth-ln-ns"
# Подсеть туннеля выводится из адреса пира: она лежит внутри 10.0.0.0/8, но
# резать её нельзя — на ней шлюз, который обслуживает DNS.
GATEWAY_NET="${LINKEDIN_GATEWAY_NET:-$(echo "${WG_ADDR}" | cut -d. -f1-3).0/24}"

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    echo "Нужны права root: netns и wg настраиваются от root." >&2
    exit 1
  fi
}

require_wireguard_tools() {
  # Модуль ядра и userspace-утилиты ставятся отдельно: на VPS модуль есть,
  # а wg/wg-quick могут отсутствовать. Без явной проверки скрипт падает на
  # невнятном "command not found" уже после того, как создал namespace.
  local missing=()
  command -v wg >/dev/null 2>&1 || missing+=(wg)
  command -v wg-quick >/dev/null 2>&1 || missing+=(wg-quick)
  if [[ "${#missing[@]}" -gt 0 ]]; then
    echo "Не найдены: ${missing[*]}. Установите пакет wireguard-tools." >&2
    exit 1
  fi
}

ensure_resolver() {
  # wg-quick strip выкидывает строки Address и DNS, поэтому резолвер внутри
  # namespace не появится сам. Берём его из того же конфига, где лежит
  # туннель: второй ручной ввод того же адреса означал бы возможность их
  # расхождения, а расходиться они будут молча.
  local dns
  dns="$(grep -iE "^[[:space:]]*DNS[[:space:]]*=" "${WG_CONF}" | head -1 | cut -d= -f2 | tr -d " " | cut -d, -f1)"
  if [[ -z "${dns}" ]]; then
    echo "Конфиг ${WG_CONF} не содержит строки DNS: namespace остался бы без" >&2
    echo "разрешения имён, а это отказ ровно того молчаливого класса, который" >&2
    echo "вся конструкция устраняет." >&2
    exit 1
  fi
  mkdir -p "/etc/netns/${NETNS}"
  echo "nameserver ${dns}" > "/etc/netns/${NETNS}/resolv.conf"
}

ensure_netns() {
  if ! ip netns list | awk '{print $1}' | grep -qx "${NETNS}"; then
    ip netns add "${NETNS}"
  fi
  ip -n "${NETNS}" link set lo up
}

refresh_peer_endpoint() {
  # Firewalla advertises a DDNS hostname. WireGuard stores only the numeric
  # address resolved by userspace, so an already-running interface otherwise
  # remains pinned to a stale address after the home endpoint changes.
  # Resolve on the host: namespace DNS is behind the tunnel being repaired.
  local endpoint peer_key endpoint_host endpoint_port resolved_endpoint
  endpoint="$(grep -iE '^[[:space:]]*Endpoint[[:space:]]*=' "${WG_CONF}" | head -1 | cut -d= -f2- | tr -d ' ' || true)"
  peer_key="$(grep -iE '^[[:space:]]*PublicKey[[:space:]]*=' "${WG_CONF}" | head -1 | cut -d= -f2- | tr -d ' ' || true)"
  if [[ -z "${endpoint}" || -z "${peer_key}" || "${endpoint}" != *:* ]]; then
    echo "Конфиг ${WG_CONF} не содержит корректные peer endpoint/public key." >&2
    exit 1
  fi
  endpoint_host="${endpoint%:*}"
  endpoint_port="${endpoint##*:}"
  resolved_endpoint="$(getent ahostsv4 "${endpoint_host}" | awk 'NR == 1 {print $1}' || true)"
  if [[ -z "${resolved_endpoint}" ]]; then
    echo "Не удалось разрешить WireGuard endpoint ${endpoint_host} на хосте." >&2
    exit 1
  fi
  ip netns exec "${NETNS}" wg set "${WG_IF}" peer "${peer_key}" endpoint "${resolved_endpoint}:${endpoint_port}"
}

ensure_tunnel() {
  if ip -n "${NETNS}" link show "${WG_IF}" >/dev/null 2>&1; then
    refresh_peer_endpoint
    return
  fi
  if ! ip link show "${WG_IF}" >/dev/null 2>&1; then
    ip link add "${WG_IF}" type wireguard
  fi
  wg setconf "${WG_IF}" <(wg-quick strip "${WG_CONF}")
  ip link set "${WG_IF}" netns "${NETNS}"
  ip -n "${NETNS}" addr add "${WG_ADDR}" dev "${WG_IF}"
  ip -n "${NETNS}" link set "${WG_IF}" up
  ip -n "${NETNS}" route add default dev "${WG_IF}"
}

ensure_management_link() {
  if ip -n "${NETNS}" link show "${VETH_NS}" >/dev/null 2>&1; then
    return
  fi
  ip link add "${VETH_HOST}" type veth peer name "${VETH_NS}"
  ip link set "${VETH_NS}" netns "${NETNS}"
  ip addr add 169.254.77.1/30 dev "${VETH_HOST}"
  ip link set "${VETH_HOST}" up
  ip -n "${NETNS}" addr add 169.254.77.2/30 dev "${VETH_NS}"
  ip -n "${NETNS}" link set "${VETH_NS}" up
}

disable_ipv6() {
  # Резолвер отдаёт AAAA, а маршрута наружу по IPv6 в туннеле нет: каждое
  # соединение тратит попытку в никуда, а запрос, жёстко предпочитающий IPv6,
  # зависает — по симптомам неотличимо от антибот-блокировки.
  ip netns exec "${NETNS}" sysctl -qw net.ipv6.conf.all.disable_ipv6=1
  ip netns exec "${NETNS}" sysctl -qw net.ipv6.conf.default.disable_ipv6=1
}

block_private_networks() {
  # Правило на Firewalla отрезает соседние хосты, но не сам роутер: блокировка
  # «доступа в локальную сеть» ложится на форвардинг, а трафик, адресованный
  # самому устройству, идёт другой цепочкой. Это наблюдалось живьём —
  # 192.168.1.1 отвечал на HTTP, ICMP и tcp/22 при, казалось бы, настроенном
  # правиле. Второй слой ставится там, где мы управляем всем сами.
  #
  # Порядок правил значим: разрешение подсети туннеля идёт первым, иначе
  # запрет на 10.0.0.0/8 унесёт вместе с LAN и собственный резолвер.
  ip netns exec "${NETNS}" iptables -F OUTPUT
  ip netns exec "${NETNS}" iptables -A OUTPUT -d "${GATEWAY_NET}" -j ACCEPT
  ip netns exec "${NETNS}" iptables -A OUTPUT -d 169.254.77.0/30 -j ACCEPT
  for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16; do
    ip netns exec "${NETNS}" iptables -A OUTPUT -d "${net}" -j REJECT
  done
}

assert_lan_unreachable() {
  # Изоляция проверяется, а не предполагается. Пробник целится в адрес, для
  # которого запрет обязан сработать; успешный ответ означает, что правила
  # собраны неправильно, и запускать браузер нельзя.
  if ip netns exec "${NETNS}" timeout 3 bash -c "echo > /dev/tcp/192.168.1.1/80" 2>/dev/null; then
    echo "LAN достижим из ${NETNS} несмотря на правила — браузер не запускается." >&2
    exit 1
  fi
}

assert_fail_closed() {
  local routes
  routes="$(ip -n "${NETNS}" route show default)"
  if [[ "$(echo "${routes}" | wc -l)" -ne 1 ]] || [[ "${routes}" != *"dev ${WG_IF}"* ]]; then
    echo "Маршрут по умолчанию в ${NETNS} не единственный или не через ${WG_IF}:" >&2
    echo "${routes}" >&2
    exit 1
  fi
}

require_root
require_wireguard_tools
ensure_netns
ensure_tunnel
ensure_management_link
ensure_resolver
disable_ipv6
block_private_networks
assert_fail_closed
assert_lan_unreachable

echo "netns ${NETNS} поднят: выход через ${WG_IF}, управление через ${VETH_HOST} (169.254.77.1)"
