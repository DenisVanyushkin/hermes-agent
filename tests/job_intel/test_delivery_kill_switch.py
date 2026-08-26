"""Delivery kill-switch: shadow collection must never reach Slack.

Every test runs with a non-empty webhook, Slack tokens present, and a
production-like run type — the exact configuration in which the pre-existing
suppression in ``_deliver_to_slack`` does *not* protect us.

Both transports are replaced by counting spies rather than by raising fakes:
``_deliver_to_slack`` swallows transport exceptions inside its retry loop, so a
raising fake would be absorbed and the test would pass while a real send had
been attempted. The assertion is therefore on the call counters.
"""

from __future__ import annotations

import pytest

from job_intel import cli


class Spy:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("transport must not be reached")


@pytest.fixture
def production_like(monkeypatch):
    monkeypatch.setenv("JOB_INTEL_RUN_TYPE", "production")
    monkeypatch.setenv("JOB_INTEL_SLACK_WEBHOOK_URL", "https://hooks.slack.test/example")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-token")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test-token")

    webhook_spy, gateway_spy = Spy(), Spy()
    monkeypatch.setattr("job_intel.cli.requests.post", webhook_spy)
    monkeypatch.setattr("job_intel.cli.send_message_tool", gateway_spy)
    monkeypatch.webhook_spy = webhook_spy
    monkeypatch.gateway_spy = gateway_spy
    return monkeypatch


@pytest.mark.parametrize("prefer_gateway", [False, True])
def test_kill_switch_suppresses_every_branch(production_like, prefer_gateway) -> None:
    production_like.setenv("JOB_INTEL_DELIVERY_DISABLED", "1")

    result = cli._deliver_to_slack(
        "must never be sent", channel="C0B4MM6D52A", prefer_gateway=prefer_gateway
    )

    assert result.status == "suppressed"
    assert result.success is False
    assert result.attempts == 0
    assert production_like.webhook_spy.calls == 0
    assert production_like.gateway_spy.calls == 0


@pytest.mark.parametrize("raw", ["1", "true", "yes", "shadow", "unexpected"])
def test_unknown_values_fail_closed(production_like, raw) -> None:
    """Anything but an explicit opt-in to delivery must suppress."""
    production_like.setenv("JOB_INTEL_DELIVERY_DISABLED", raw)

    result = cli._deliver_to_slack("must never be sent", channel="C0B4MM6D52A")

    assert result.status == "suppressed"
    assert result.attempts == 0
    assert production_like.webhook_spy.calls == 0


@pytest.mark.parametrize("raw", ["", "0", "false"])
def test_explicit_off_keeps_delivery_enabled(production_like, raw) -> None:
    """The switch must be a switch: turned off, the old behaviour returns."""
    production_like.setenv("JOB_INTEL_DELIVERY_DISABLED", raw)

    cli._deliver_to_slack("delivery is expected here", channel="C0B4MM6D52A")

    assert production_like.webhook_spy.calls > 0


def test_kill_switch_blocks_the_advisory_path(production_like) -> None:
    """post_advisory bypasses _deliver_to_slack entirely."""
    from job_intel import shadow_advisory

    spy = Spy()
    production_like.setenv("JOB_INTEL_DELIVERY_DISABLED", "1")
    production_like.setattr(shadow_advisory, "_gateway_send", spy)

    outcome = shadow_advisory.post_advisory("must never be sent", dry_run=False)

    assert spy.calls == 0
    assert outcome.get("posted") is False


def test_kill_switch_blocks_the_operator_notification(production_like) -> None:
    """notify_operator defaults to dry_run=False and reuses the advisory path."""
    from job_intel import linkedin_otp_recovery, shadow_advisory

    spy = Spy()
    production_like.setenv("JOB_INTEL_DELIVERY_DISABLED", "1")
    production_like.setattr(shadow_advisory, "_gateway_send", spy)

    outcome = linkedin_otp_recovery.notify_operator("must never be sent")

    assert spy.calls == 0
    assert outcome.get("posted") is False


def test_advisory_still_sends_when_the_switch_is_off(production_like) -> None:
    """Control group: without the switch the advisory path does reach the gateway."""
    from job_intel import shadow_advisory

    spy = Spy()
    production_like.setenv("JOB_INTEL_DELIVERY_DISABLED", "0")
    production_like.setattr(shadow_advisory, "_gateway_send", spy)

    shadow_advisory.post_advisory("delivery is expected here", dry_run=False)

    assert spy.calls > 0
