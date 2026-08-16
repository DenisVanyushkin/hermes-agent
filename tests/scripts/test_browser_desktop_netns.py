from __future__ import annotations

import re
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "browser-desktop-bootstrap.sh"
STOP_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "browser-desktop-stop.sh"


def _body() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_script_is_syntactically_valid() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_linkedin_profile_runs_inside_the_namespace() -> None:
    body = _body()
    assert "NETNS_PREFIX" in body
    assert 'ip netns exec "${LINKEDIN_NETNS:-ln-eg}"' in body


def test_doh_is_disabled_for_the_linkedin_profile() -> None:
    """Включённый Secure DNS уносит резолв мимо выбранного резолвера. Адрес
    при этом не утекает — трафик всё равно в туннеле — но геосогласованность
    ответов GeoDNS теряется, и снаружи это ничем не проявляется."""
    body = _body()
    assert "--disable-features=DnsOverHttps" in body
    assert "--dns-over-https-mode=off" in body


def test_namespace_is_entered_before_privileges_are_dropped() -> None:
    """ip netns exec требует CAP_SYS_ADMIN, а runuser роняет права до browser.
    Префикс ВНУТРИ runuser даёт "setting the network namespace failed:
    Operation not permitted", Xvfb не стартует, и запуск падает на таймауте
    ожидания дисплея. Проверять надо позицию, а не факт наличия: прошлая
    версия этого теста считала вхождения и потому пропустила ошибку."""
    body = _body()
    start = body.index("start_as_browser() {")
    end = body.index("\n}", start)
    function = body[start:end]
    prefix = function.index("${NETNS_PREFIX[@]}")
    runuser = function.index("runuser -u")
    assert prefix < runuser, "netns exec обязан стоять перед runuser"


def test_call_sites_do_not_pass_the_prefix_themselves() -> None:
    """Префикс применяется в одном месте — в start_as_browser. На пяти
    вызовах эту ошибку иначе можно повторить пять раз."""
    body = _body()
    launches = [line for line in body.splitlines() if "start_as_browser " in line and "()" not in line]
    assert launches, "вызовы start_as_browser не найдены"
    for line in launches:
        assert "${NETNS_PREFIX[@]}" not in line, f"префикс на месте вызова: {line.strip()}"


def test_display_wait_observes_the_namespace_where_the_server_lives() -> None:
    """Проверка дисплея должна смотреть туда, где X-сервер запущен."""
    body = _body()
    start = body.index("wait_for_display() {")
    end = body.index("\n}", start)
    assert "${NETNS_PREFIX[@]}" in body[start:end]


def test_non_linkedin_profiles_are_untouched() -> None:
    body = _body()
    assert 'if [[ "${PROFILE}" == "linkedin" ]]; then' in body
    assert "NETNS_PREFIX=()" in body


def test_novnc_binds_an_address_reachable_from_the_host() -> None:
    """Внутри netns 127.0.0.1 — это его собственный loopback, и SSH-туннель
    оператора туда не попадёт. noVNC обязан слушать на veth-адресе, иначе
    ручной вход по ранбуку физически невозможен."""
    body = _body()
    assert "NOVNC_BIND" in body
    assert 'NOVNC_BIND="169.254.77.2"' in body
    assert '"${NOVNC_BIND}:${NOVNC_PORT}"' in body


def test_linkedin_cdp_is_relayed_over_the_management_veth() -> None:
    """Chrome 149 ignores a non-loopback remote-debugging bind.

    Keep Chromium on the namespace loopback and expose it only on the
    management-veth address.  Chrome builds the returned WebSocket URL from
    the HTTP Host header, so Playwright can connect directly through one relay.
    """
    body = _body()
    assert 'CDP_RELAY_SOURCE="${SCRIPT_DIR}/browser-desktop-cdp-relay.py"' in body
    assert 'CDP_RELAY="/usr/local/libexec/browser-desktop-cdp-relay.py"' in body
    assert 'install -o root -g root -m 0755 "${CDP_RELAY_SOURCE}" "${CDP_RELAY}"' in body
    assert 'CDP_RELAY_PORT="19222"' in body
    assert '--listen-host 169.254.77.2' in body
    assert '--target-host 127.0.0.1' in body
    assert '--remote-debugging-address=127.0.0.1' in body
    assert 'CDP_ENDPOINT="http://169.254.77.2:${CDP_RELAY_PORT}"' in body
    assert "start_as_browser_host" not in body


def test_stop_script_stops_the_linkedin_cdp_relay() -> None:
    body = STOP_SCRIPT.read_text(encoding="utf-8")
    assert 'browser-desktop-cdp-relay.py.*--listen-port ${CDP_RELAY_PORT}' in body
    assert 'browser-desktop-cdp-relay.py.*--listen-port ${CDP_PORT}' not in body
    assert 'pgrep -u "$USER_NAME" -f -- "$pattern"' in body


def test_stop_script_matches_x11vnc_started_without_wrapper_arguments() -> None:
    """The managed x11vnc command starts directly with ``-display``.

    A pattern that requires an extra argument between ``x11vnc`` and
    ``-display`` leaves the VNC server running after the browser is stopped.
    """
    body = STOP_SCRIPT.read_text(encoding="utf-8")
    configured = re.search(
        r'kill_pattern "(x11vnc[^\"]+)" "x11vnc \(\$\{profile\}\)"', body
    )
    assert configured is not None
    pattern = (
        configured.group(1)
        .replace("${DISPLAY_NUM}", "100")
        .replace("${VNC_PORT}", "5902")
    )
    command = (
        "x11vnc -display :100 -localhost "
        "-rfbauth /var/lib/browser-desktop/.vnc/passwd "
        "-rfbport 5902 -forever -shared"
    )
    assert re.search(pattern, command)


def test_printed_tunnel_hint_matches_the_actual_bind_address() -> None:
    """Скрипт печатает оператору команду туннеля. Для профиля linkedin noVNC
    слушает на veth-адресе, и статичная подсказка про 127.0.0.1 отправляет
    оператора в тупик, который выглядит как поломка носителя, а не как
    неверная инструкция."""
    body = _body()
    assert "${NOVNC_BIND}:${NOVNC_PORT}" in body
    hint = body[body.index("Connect securely via SSH tunnel"):]
    assert "-L ${NOVNC_PORT}:${NOVNC_BIND}:${NOVNC_PORT}" in hint


def test_linkedin_opens_the_profile_that_holds_the_session() -> None:
    """--profile-directory был зашит в Default, а сессия LinkedIn живёт в
    Profile 1. Ночной перезапуск 2026-08-12 открыл Default и показал
    разлогиненный LinkedIn — симптом, неотличимый от потери сессии."""
    body = _body()
    assert "PROFILE_DIRECTORY" in body
    assert '--profile-directory="${PROFILE_DIRECTORY}"' in body
    # Ярлык на рабочем столе тоже: иначе иконка открывает Default, пока
    # автоматика ходит в профиль с сессией.
    assert "--profile-directory=${PROFILE_DIRECTORY} --new-window https://www.linkedin.com/" in body
    # Профиль hh к этому не относится и остаётся на Default.
    assert body.count("--profile-directory=Default") == 1


def test_profile_resolution_falls_back_to_default() -> None:
    """Резолвер может не отработать — тогда поведение прежнее, а не пустая
    строка в аргументе Chromium."""
    body = _body()
    assert 'PROFILE_DIRECTORY="Default"' in body


def test_profile_resolution_accepts_the_pinned_browser_python() -> None:
    """Exported experiment runtimes do not contain runtime/venv.

    The bootstrap must use the experiment's pinned Python supplied by the
    worker; otherwise it silently falls back to Default instead of the
    authenticated Profile 1.
    """
    body = _body()
    assert 'resolver_python="${JOB_INTEL_BROWSER_PYTHON:-}"' in body
