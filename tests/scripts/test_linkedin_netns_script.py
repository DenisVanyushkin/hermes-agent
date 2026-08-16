from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "linkedin-netns-up.sh"


def _body() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_script_is_syntactically_valid() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_default_route_goes_only_through_the_tunnel() -> None:
    """Единственный маршрут по умолчанию — в wg. Маршрут по умолчанию через
    veth означал бы, что падение туннеля выпускает браузер напрямую, то есть
    ровно ту утечку на датацентровый адрес, ради предотвращения которой
    netns и выбран."""
    body = _body()
    assert 'route add default dev "${WG_IF}"' in body
    assert 'route add default dev "${VETH_NS}"' not in body
    assert "default via" not in body


def test_veth_carries_only_the_management_prefix() -> None:
    body = _body()
    assert "169.254.77.1/30" in body
    assert "169.254.77.2/30" in body


def test_bring_up_is_idempotent() -> None:
    body = _body()
    assert "ip netns list" in body
    assert 'ip -n "${NETNS}" link show "${WG_IF}"' in body


def test_existing_tunnel_refreshes_the_dynamic_peer_endpoint() -> None:
    """Firewalla publishes a DDNS hostname whose address can change.

    Returning merely because wg0-ln exists leaves WireGuard pinned to the old
    numeric endpoint forever.  Resolution must happen in the host namespace,
    because the namespace DNS itself depends on the tunnel being repaired.
    """
    body = _body()
    start = body.index("ensure_tunnel() {")
    end = body.index("\n}", start)
    function = body[start:end]
    refresh = function.index("refresh_peer_endpoint")
    early_return = function.index("return", refresh)
    assert refresh < early_return
    assert 'getent ahostsv4 "${endpoint_host}"' in body
    assert 'ip netns exec "${NETNS}" wg set "${WG_IF}" peer' in body


def test_script_verifies_fail_closed_before_returning() -> None:
    """Проверка обязана быть в самом скрипте: конструкция, чья корректность
    держится на том, что оператор не забыл посмотреть глазами, однажды
    окажется собрана неправильно и промолчит."""
    body = _body()
    assert "assert_fail_closed" in body
    assert "exit 1" in body


def test_no_secrets_inline() -> None:
    body = _body()
    assert "PrivateKey" not in body
    assert "Endpoint =" not in body


# --- Дополнение: резолвер и предусловия --------------------------------


def test_resolver_is_written_into_the_namespace() -> None:
    """wg-quick strip выкидывает строки Address и DNS, поэтому резолвер внутри
    namespace не появится сам. Оставлять его ручным шагом нельзя: забытый
    резолвер даёт namespace без разрешения имён — отказ того же молчаливого
    класса, который вся конструкция и устраняет."""
    body = _body()
    assert "/etc/netns/${NETNS}/resolv.conf" in body
    assert "ensure_resolver" in body


def test_resolver_is_taken_from_the_same_config_as_the_tunnel() -> None:
    """Единственный источник правды — конфиг Firewalla: там DNS уже лежит
    рядом с ключами, и второй ручной ввод того же адреса означал бы
    возможность их расхождения."""
    body = _body()
    assert 'grep -iE "^[[:space:]]*DNS[[:space:]]*=" "${WG_CONF}"' in body


def test_missing_resolver_fails_closed() -> None:
    body = _body()
    assert "не содержит строки DNS" in body


def test_script_checks_for_wireguard_tools() -> None:
    """Модуль ядра на VPS есть, а wg и wg-quick не установлены. Без явной
    проверки скрипт падает на невнятном 'command not found' посреди работы."""
    body = _body()
    assert "require_wireguard_tools" in body
    assert "wireguard-tools" in body


# --- Дополнение: изоляция от LAN и IPv6 ---------------------------------


def test_private_networks_are_blocked_inside_the_namespace() -> None:
    """Правило на Firewalla отрезало соседние хосты, но не сам роутер: блок
    «доступа в локальную сеть» ложится на форвардинг, а трафик к самому
    устройству идёт другой цепочкой. Полагаться на механизм, которым мы не
    управляем, здесь нельзя — тот же критерий, по которому выбран netns."""
    body = _body()
    assert "block_private_networks" in body
    for prefix in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"):
        assert prefix in body


def test_tunnel_subnet_is_allowed_before_the_private_block() -> None:
    """Шлюз туннеля лежит внутри 10.0.0.0/8 и обслуживает DNS. Разрешающее
    правило обязано стоять раньше запрещающего, иначе namespace останется без
    разрешения имён.

    Порядок проверяется внутри самой функции: сравнивать первые вхождения по
    всему файлу бесполезно, потому что оба адреса упоминаются ещё и в
    комментариях."""
    body = _body()
    start = body.index("block_private_networks() {")
    end = body.index("\n}", start)
    function = body[start:end]
    allow = function.index('-d "${GATEWAY_NET}" -j ACCEPT')
    deny = function.index('for net in 10.0.0.0/8')
    assert allow < deny


def test_ipv6_is_disabled_inside_the_namespace() -> None:
    """Резолвер отдаёт AAAA, а маршрута наружу по IPv6 в туннеле нет. Попытка
    в никуда на каждом соединении, а при жёстком предпочтении IPv6 — зависание,
    по симптомам неотличимое от антибот-блокировки."""
    body = _body()
    assert "disable_ipv6" in body
    assert "net.ipv6.conf.all.disable_ipv6=1" in body


def test_isolation_is_asserted_not_assumed() -> None:
    body = _body()
    assert "assert_lan_unreachable" in body


def test_script_sources_its_env_file_itself() -> None:
    """browser-desktop-bootstrap.sh зовёт этот скрипт без окружения, а адрес
    пира обязателен. Без самостоятельной загрузки env-файла запуск десктопа
    падает на первой строке — то есть ручной вход по ранбуку невозможен."""
    body = _body()
    assert "/etc/job-intel/linkedin-netns.env" in body
    assert "ENV_FILE" in body


def test_env_file_is_loaded_before_the_required_variable_is_read() -> None:
    body = _body()
    load = body.index("ENV_FILE=")
    require = body.index("LINKEDIN_WG_ADDR:?")
    assert load < require
