"""Channel gate for the pipeline router/orchestrator (_pipeline_platform_allowed).

WhatsApp (Amina) must bypass the engineering/recruiter pipeline machinery and
go straight to the default conversation agent; Telegram (Denis admin) keeps it.
The gate is config-driven so the shared fork branch stays VPS-safe (empty/absent
allowlist => no restriction).
"""
import gateway.run as run


def test_no_restriction_when_key_absent():
    # backward compat / VPS: no allowed_platforms => every platform allowed
    assert run._pipeline_platform_allowed({}, "whatsapp") is True
    assert run._pipeline_platform_allowed({"pipelines": {}}, "whatsapp") is True
    assert run._pipeline_platform_allowed({"pipelines": {"allowed_platforms": []}}, "whatsapp") is True


def test_whatsapp_blocked_when_only_telegram_allowed():
    cfg = {"pipelines": {"allowed_platforms": ["telegram"]}}
    assert run._pipeline_platform_allowed(cfg, "whatsapp") is False


def test_telegram_allowed_when_in_list():
    cfg = {"pipelines": {"allowed_platforms": ["telegram"]}}
    assert run._pipeline_platform_allowed(cfg, "telegram") is True


def test_case_and_whitespace_insensitive():
    cfg = {"pipelines": {"allowed_platforms": [" Telegram ", "CLI"]}}
    assert run._pipeline_platform_allowed(cfg, "telegram") is True
    assert run._pipeline_platform_allowed(cfg, "cli") is True
    assert run._pipeline_platform_allowed(cfg, "whatsapp") is False


def test_malformed_allowlist_fails_open():
    # a non-iterable-of-strings value must not crash the turn -> allow
    cfg = {"pipelines": {"allowed_platforms": 123}}
    assert run._pipeline_platform_allowed(cfg, "whatsapp") is True
