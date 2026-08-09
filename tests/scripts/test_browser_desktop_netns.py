from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "browser-desktop-bootstrap.sh"


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


def test_xvfb_and_chromium_share_the_same_prefix() -> None:
    """X-сервер и браузер обязаны жить в одном namespace: abstract unix
    sockets разделяются по netns, и X через них ходит. Разнесённые по разным
    namespace они дают симптом, неотличимый от мёртвого туннеля."""
    body = _body()
    assert body.count("${NETNS_PREFIX[@]}") >= 2


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
