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
