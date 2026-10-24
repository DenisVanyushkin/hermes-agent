"""Fork cron delivery on upstream v0.22.5's split delivery module.

Moved from the fork's additions to tests/cron/test_scheduler.py, which the
v0.22.5 merge replaced with upstream's file: named private Telegram topics
(text and media), fail-closed delivery targets, the ``wrap`` override, and the
job title reaching the role router.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
from unittest.mock import MagicMock, patch

import pytest

from cron.scheduler import _build_job_prompt, _deliver_result, _resolve_delivery_target


def _safe_media_path(tmp_path, monkeypatch, name, data=b"media"):
    root = tmp_path / "media-cache"
    media_file = root / name
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_bytes(data)
    monkeypatch.setattr("gateway.platforms.base.MEDIA_DELIVERY_SAFE_ROOTS", (root,))
    return media_file.resolve()


def _run_now(coro, _loop):
    future = Future()
    try:
        future.set_result(asyncio.run(coro))
    except BaseException as exc:  # noqa: BLE001
        future.set_exception(exc)
    return future


class _TopicAdapter:
    def __init__(self, topic_ids=("38049",)):
        # One id per ensure_dm_topic call; the last one repeats.
        self.topic_ids = list(topic_ids)
        self.ensure_calls = []
        self.text_calls = []
        self.document_calls = []

    async def ensure_dm_topic(self, chat_id, topic_name, force_create=False):
        self.ensure_calls.append((chat_id, topic_name, force_create))
        return self.topic_ids.pop(0) if len(self.topic_ids) > 1 else self.topic_ids[0]

    async def send(self, chat_id, content, metadata=None, **_kw):
        self.text_calls.append((chat_id, content, dict(metadata or {})))
        return {"success": True, "message_id": "m1"}

    async def send_document(self, chat_id, file_path, metadata=None, **_kw):
        self.document_calls.append((chat_id, file_path, dict(metadata or {})))
        return {"success": True, "message_id": "m2"}


def _deliver_named_topic(tmp_path, monkeypatch, adapter):
    from gateway.config import GatewayConfig, Platform, PlatformConfig

    media_path = _safe_media_path(tmp_path, monkeypatch, "digest.txt")
    config = GatewayConfig(platforms={Platform.TELEGRAM: PlatformConfig(enabled=True)})
    loop = MagicMock()
    loop.is_running.return_value = True
    with (
        patch("gateway.config.load_gateway_config", return_value=config),
        patch("cron.scheduler.load_config", return_value={"cron": {"wrap_response": False}}),
        patch("asyncio.run_coroutine_threadsafe", side_effect=_run_now),
    ):
        result = _deliver_result(
            {"id": "named-topic-cron", "deliver": "telegram:79564752:Погода Алматы"},
            f"forecast\nMEDIA:{media_path}",
            adapters={Platform.TELEGRAM: adapter},
            loop=loop,
        )
    return result, media_path


def test_named_telegram_private_topic_target_preserves_name():
    target = _resolve_delivery_target({"deliver": "telegram:79564752:Погода Алматы"})

    assert (target["platform"], target["chat_id"], target["thread_id"]) == (
        "telegram", "79564752", "Погода Алматы")


def test_named_topic_routes_text_and_media_via_the_resolved_forum_thread(tmp_path, monkeypatch):
    """The topic NAME reaches the DeliveryRouter (which creates/reuses the
    forum topic), and the attachment reuses the numeric thread id instead of
    falling back to General."""
    adapter = _TopicAdapter()

    result, media_path = _deliver_named_topic(tmp_path, monkeypatch, adapter)

    assert result is None
    ((chat, text, text_meta),) = adapter.text_calls
    assert (chat, text) == ("79564752", "forecast")
    assert text_meta["thread_id"] == "38049"
    assert text_meta.get("telegram_dm_topic_created_for_send") is True
    ((doc_chat, doc_path, doc_meta),) = adapter.document_calls
    assert (doc_chat, doc_path) == ("79564752", str(media_path))
    assert doc_meta["thread_id"] == "38049"
    assert not any(k.startswith("_hm_") for k in doc_meta)
    assert all(call[1] == "Погода Алматы" for call in adapter.ensure_calls)


def test_named_topic_media_is_not_sent_to_general_when_the_topic_cannot_resolve(tmp_path, monkeypatch):
    """The text send resolved the topic; the media-time lookup fails. The attachment
    must be reported undelivered, not sent without a thread (= General)."""
    adapter = _TopicAdapter(topic_ids=("38049", None))

    result, _ = _deliver_named_topic(tmp_path, monkeypatch, adapter)

    assert len(adapter.text_calls) == 1
    assert adapter.document_calls == []
    assert result is not None and "could not be resolved" in result


def test_no_targets_when_gateway_config_fails(monkeypatch):
    from cron.scheduler_delivery import cron_delivery_targets
    from gateway import config as gateway_config

    def _boom(*_a, **_kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(gateway_config, "load_gateway_config", _boom)

    assert cron_delivery_targets() == []


def test_gateway_config_failure_lists_no_bot_chat_targets_either(monkeypatch):
    from cron.scheduler_delivery import cron_delivery_targets
    from gateway import config as gateway_config

    def _boom(*_a, **_kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(gateway_config, "load_gateway_config", _boom)
    monkeypatch.setattr("hermes_cli.profiles.list_profile_names", lambda: ["default", "work"])

    assert cron_delivery_targets() == []


def _origin_delivery(wrap_cfg, **kw):
    from gateway.config import Platform

    pconfig = MagicMock()
    pconfig.enabled = True
    mock_cfg = MagicMock()
    mock_cfg.platforms = {Platform.TELEGRAM: pconfig}
    with patch("gateway.config.load_gateway_config", return_value=mock_cfg), \
         patch("tools.send_message_tool._send_to_platform", new=MagicMock()) as send_mock, \
         patch("cron.scheduler.load_config", return_value={"cron": {"wrap_response": wrap_cfg}}):
        async def _ok(*_a, **_k):
            return {"success": True}
        send_mock.side_effect = _ok
        job = {"id": "test-job", "name": "daily-report", "deliver": "origin",
               "origin": {"platform": "telegram", "chat_id": "123"}}
        _deliver_result(job, "Here is today's summary.", **kw)
    send_mock.assert_called_once()
    return send_mock.call_args.kwargs.get("content") or send_mock.call_args[0][-1]


def test_delivery_wraps_by_default_when_wrap_argument_unset():
    """Every real cron delivery omits ``wrap`` and must keep the header/footer."""
    sent = _origin_delivery(True)

    assert "Cronjob Response: daily-report" in sent
    assert "Here is today's summary." in sent


def test_wrap_false_skips_the_header_even_when_config_wraps():
    """The synthetic operator alert must not be framed as a job response."""
    sent = _origin_delivery(True, wrap=False)

    assert sent == "Here is today's summary."


class TestScheduledJobRoleRouting:
    """The job title reaches the role router as metadata."""

    def _classify(self, job: dict):
        from hermes_cli.profile_context import build_role_context_for_task

        return build_role_context_for_task(_build_job_prompt(job))

    def test_weather_job_title_routes_to_researcher_or_general_operator_not_engineer(self):
        result = self._classify({
            "id": "weather123abc",
            "name": "Ежедневный прогноз погоды Алматы",
            "prompt": "Дай краткий прогноз погоды в Алматы на сегодня.",
        })

        assert result.selected_role in {"researcher", "general_operator"}

    def test_nightly_digest_job_title_routes_to_researcher_not_engineer(self):
        result = self._classify({
            "id": "digest123abcd",
            "name": "Nightly interesting-message digest",
            "prompt": "Summarize the most interesting messages and themes from today into a concise digest.",
        })

        assert result.selected_role == "researcher"

    def test_repo_diagnostics_job_stays_engineer(self):
        result = self._classify({
            "id": "engine123abcd",
            "name": "Repo test and diagnostics",
            "prompt": "Run repo diagnostics, inspect failing tests, and summarize what needs fixing.",
        })

        assert result.selected_role == "engineer"

    def test_docs_memory_job_stays_scribe(self):
        result = self._classify({
            "id": "scribe123abcd",
            "name": "Docs handoff refresh",
            "prompt": "Update the handoff and record durable memory for today's Hermes work.",
        })

        assert result.selected_role == "scribe"

    def test_cloudflare_mutation_job_preserves_security_hard_stop(self):
        result = self._classify({
            "id": "secure123abcd",
            "name": "Cloudflare exposure mutation",
            "prompt": "Update Cloudflare tunnel and firewall rules for public exposure.",
        })

        assert result.selected_role == "engineer"
        assert result.reviewer_profile == "security_auditor"
        assert result.requires_explicit_approval is True
        assert result.critical_approval_required is True

    def test_job_title_is_in_the_prompt(self):
        prompt = _build_job_prompt({"id": "t1", "name": "Вечерний прогноз", "prompt": "x"})

        assert "## Scheduled Task Metadata\nTitle: Вечерний прогноз" in prompt


def test_the_cron_banner_is_stripped_from_role_routing_text():
    """The router must see the job's task, never the scheduler's banner: an
    unstripped v0.22.5 banner ("create or update a cron job", "FAILURE") routed
    weather jobs to engineer. Pins the banner regex to the text the scheduler
    actually writes, so the next upstream rewording turns this red."""
    from cron.scheduler_prompt import _CRON_HINT
    from hermes_cli.profile_request_context import routing_request_text

    assert routing_request_text(_CRON_HINT + "Дай прогноз погоды") == "Дай прогноз погоды"


def test_telegram_target_parser_reads_a_named_private_topic():
    from plugins.platforms.telegram.telegram_ids import parse_send_target

    assert parse_send_target("79564752:Погода Алматы") == ("79564752", "Погода Алматы")
    # Numeric topics and supergroups keep their meaning.
    assert parse_send_target("79564752:17585") == ("79564752", "17585")
    assert parse_send_target("-1001234567890:17585") == ("-1001234567890", "17585")


def test_an_explicit_failure_lane_is_not_overridden_by_the_audience():
    """failure_deliver is the operator's own choice of where failures go; the
    audience policy withholds only a failure that would reach the reader."""
    from cron.local_cron import route_failure_for_audience

    job = {"id": "j", "name": "t", "audience": "end_user", "deliver": "telegram:123",
           "failure_deliver": "slack:C0B3X1E5SJZ"}

    assert route_failure_for_audience(
        job, success=False, deliver_content="⚠️ failed", error="boom") == ("⚠️ failed", None)


def test_an_end_user_failure_is_withheld_with_an_operator_alert():
    from cron.local_cron import route_failure_for_audience

    job = {"id": "j", "name": "t", "audience": "end_user", "deliver": "telegram:123"}

    content, alert = route_failure_for_audience(
        job, success=False, deliver_content="⚠️ failed", error="ImportError: /opt/x/y.py")

    assert content == ""
    assert "WITHHELD" in alert and "/opt/x/y.py" not in alert
