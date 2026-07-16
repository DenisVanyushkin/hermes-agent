"""Suppress infra status chatter (fallback switches, provider errors) on
user-facing platforms listed in gateway.suppress_infra_status_platforms
(spec 2026-07-16 turn-error alerts, §8а). Absent key => prior behavior."""
import gateway.run as run


CFG_ON = {"gateway": {"suppress_infra_status_platforms": ["whatsapp"]}}

INFRA_STATUSES = [
    "🔐 Authentication failed and could not be refreshed — switching to fallback provider...",
    "🔄 Primary model failed — switching to fallback: nvidia/nemotron-3-super-120b-a12b:free via openrouter",
    "⚠️ Billing or credits exhausted — switching to fallback provider...",
    "⚠️ Provider unreachable — switching to fallback provider...",
    "⚠️ Rate limited — switching to fallback provider...",
    "⚠️ Empty/malformed response — switching to fallback...",
]

PROVIDER_ERROR_SHAPED = "API call failed: HTTP 401: Missing Authentication header"


def test_suppressed_platform_drops_infra_statuses(monkeypatch):
    monkeypatch.setattr(run, "_load_gateway_config", lambda: CFG_ON)
    for status in INFRA_STATUSES:
        assert run._prepare_gateway_status_message("whatsapp", "status", status) is None, status


def test_suppressed_platform_drops_provider_error_shaped_status(monkeypatch):
    monkeypatch.setattr(run, "_load_gateway_config", lambda: CFG_ON)
    # без подавления этот текст конвертится в заглушку — с подавлением молчим
    assert run._prepare_gateway_status_message("whatsapp", "status", PROVIDER_ERROR_SHAPED) is None


def test_suppressed_platform_keeps_normal_statuses(monkeypatch):
    monkeypatch.setattr(run, "_load_gateway_config", lambda: CFG_ON)
    out = run._prepare_gateway_status_message("whatsapp", "status", "⏳ Working — 2 min")
    assert out == "⏳ Working — 2 min"


def test_other_platform_unaffected(monkeypatch):
    monkeypatch.setattr(run, "_load_gateway_config", lambda: CFG_ON)
    out = run._prepare_gateway_status_message("telegram", "status", INFRA_STATUSES[0])
    assert out  # телеграм-админка не в списке — статус проходит


def test_absent_key_prior_behavior(monkeypatch):
    monkeypatch.setattr(run, "_load_gateway_config", lambda: {})
    out = run._prepare_gateway_status_message("whatsapp", "status", INFRA_STATUSES[0])
    assert out  # без ключа поведение прежнее


def test_malformed_list_fails_open(monkeypatch):
    monkeypatch.setattr(
        run, "_load_gateway_config",
        lambda: {"gateway": {"suppress_infra_status_platforms": 123}},
    )
    out = run._prepare_gateway_status_message("whatsapp", "status", INFRA_STATUSES[0])
    assert out


def test_case_insensitive_platform_match(monkeypatch):
    monkeypatch.setattr(
        run, "_load_gateway_config",
        lambda: {"gateway": {"suppress_infra_status_platforms": [" WhatsApp "]}},
    )
    assert run._prepare_gateway_status_message("whatsapp", "status", INFRA_STATUSES[1]) is None
