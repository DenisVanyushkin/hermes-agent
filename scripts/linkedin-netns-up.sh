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

NETNS="${LINKEDIN_NETNS:-ln-eg}"
WG_IF="${LINKEDIN_WG_IF:-wg0-ln}"
WG_CONF="${LINKEDIN_WG_CONF:-/etc/wireguard/wg0-ln.conf}"
WG_ADDR="${LINKEDIN_WG_ADDR:?LINKEDIN_WG_ADDR не задан (адрес пира из Firewalla)}"
VETH_HOST="veth-ln-host"
VETH_NS="veth-ln-ns"

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    echo "Нужны права root: netns и wg настраиваются от root." >&2
    exit 1
  fi
}

ensure_netns() {
  if ! ip netns list | awk '{print $1}' | grep -qx "${NETNS}"; then
    ip netns add "${NETNS}"
  fi
  ip -n "${NETNS}" link set lo up
}

ensure_tunnel() {
  if ip -n "${NETNS}" link show "${WG_IF}" >/dev/null 2>&1; then
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
ensure_netns
ensure_tunnel
ensure_management_link
assert_fail_closed

echo "netns ${NETNS} поднят: выход через ${WG_IF}, управление через ${VETH_HOST} (169.254.77.1)"
