from __future__ import annotations

from job_intel import cli
from job_intel.store import JobIntelStore


class FakeDelivery:
    def __init__(self):
        self.messages: list[tuple[str, str | None]] = []

    def __call__(self, message, channel=None, *, retries=3, prefer_gateway=False, thread_ts=None):
        from job_intel.cli import SlackDeliveryResult

        self.messages.append((message, channel))
        return SlackDeliveryResult(success=True, attempts=1, error=None, status="sent", message_ts="1.1")


def seed_classified_feedback(store, count):
    for index in range(count):
        event_id = store.create_feedback_event(
            slack_channel_id="C123",
            slack_message_ts=f"1760000000.{index}",
            user_id="U1",
            reaction_type="-1",
        )
        store.update_feedback_event(
            event_id,
            status="classified",
            reason_detail_codes_json=["no_pnl_ownership"],
            reason_category_codes_json=["seniority_scope_mismatch"],
            attribution_targets_json=["seniority_scope"],
        )


def run_weekly(tmp_path, monkeypatch, feedback_count):
    db_path = tmp_path / "job_intel.sqlite3"
    monkeypatch.setattr(cli, "resolve_db_path", lambda: db_path)
    monkeypatch.setattr(cli, "assert_runtime_contract", lambda: None)
    fake = FakeDelivery()
    monkeypatch.setattr(cli, "_deliver_to_slack", fake)
    store = JobIntelStore(db_path)
    store.bootstrap()
    seed_classified_feedback(store, feedback_count)
    cli.run_weekly_kpi_report()
    return store, fake


def test_weekly_kpi_sends_full_feedback_digest(tmp_path, monkeypatch):
    store, fake = run_weekly(tmp_path, monkeypatch, feedback_count=6)
    digest_messages = [m for m, _ in fake.messages if "Negative feedback review" in m]
    assert len(digest_messages) == 1
    assert "no_pnl_ownership" in digest_messages[0]
    with store.connect(read_only=True) as conn:
        kinds = [row[0] for row in conn.execute("SELECT notification_kind FROM notifications").fetchall()]
    assert "negative_feedback_weekly" in kinds


def test_weekly_kpi_sends_short_note_when_little_feedback(tmp_path, monkeypatch):
    store, fake = run_weekly(tmp_path, monkeypatch, feedback_count=1)
    short = [m for m, _ in fake.messages if "полный разбор пропускаю" in m]
    assert len(short) == 1


def test_digest_failure_does_not_break_kpi_run(tmp_path, monkeypatch):
    db_path = tmp_path / "job_intel.sqlite3"
    monkeypatch.setattr(cli, "resolve_db_path", lambda: db_path)
    monkeypatch.setattr(cli, "assert_runtime_contract", lambda: None)
    fake = FakeDelivery()
    monkeypatch.setattr(cli, "_deliver_to_slack", fake)
    store = JobIntelStore(db_path)
    store.bootstrap()

    import job_intel.calibration as calibration

    def boom(*args, **kwargs):
        raise RuntimeError("digest exploded")

    monkeypatch.setattr(calibration, "build_weekly_digest", boom)
    message = cli.run_weekly_kpi_report()  # must not raise
    assert message
    with store.connect(read_only=True) as conn:
        status = conn.execute("SELECT status FROM runs ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert status == "ok"
