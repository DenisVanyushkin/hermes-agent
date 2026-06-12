from __future__ import annotations

import json

from job_intel.crm_constants import TERMINAL_GUARDED_STATUSES
from job_intel.crm_reconciler import CRMReconciler
from job_intel.store import JobIntelStore


def make_store(tmp_path):
    db_path = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db_path)
    store.bootstrap()
    return store


def seed_vacancy(store: JobIntelStore, *, vacancy_id_key: str = "1", url: str = "https://example.com/jobs/1") -> int:
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO vacancies (
                vacancy_key, source, source_id, company, title, location, url, description,
                posted_at, scraped_at, salary, company_url, metadata_json, first_seen_at,
                last_seen_at, repost_count, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"linkedin:example:{vacancy_id_key}",
                "linkedin",
                f"example-{vacancy_id_key}",
                "Acme",
                "Head of Product",
                "Remote",
                url,
                "Acme is hiring a Head of Product.",
                None,
                None,
                None,
                None,
                json.dumps({"ats": "linkedin", "ats_job_id": f"example-{vacancy_id_key}"}),
                "2026-06-01T00:00:00+00:00",
                "2026-06-01T00:00:00+00:00",
                0,
                "active",
            ),
        )
        return int(conn.execute("SELECT id FROM vacancies WHERE vacancy_key=?", (f"linkedin:example:{vacancy_id_key}",)).fetchone()[0])


def seed_message(store: JobIntelStore, *, vacancy_id: int, ts: str = "1760000000.123456", notification_id: int = 700) -> None:
    store.record_vacancy_slack_message(
        vacancy_id=vacancy_id,
        run_id=None,
        vacancy_key=f"linkedin:example:{vacancy_id}",
        canonical_url="https://example.com/jobs/1",
        card_key=f"card:{vacancy_id}",
        notification_id=None,
        slack_channel="C123",
        slack_message_ts=ts,
        message_type="vacancy_card",
        company="Acme",
        title="Head of Product",
        score=95,
        recommendation="strong_fit",
        url="https://example.com/jobs/1",
        sent_at="2026-06-05T10:00:00+00:00",
    )


def seed_feedback(
    store: JobIntelStore,
    *,
    vacancy_id: int,
    ts: str,
    feedback_type: str,
    event_type: str,
    user_id: str = "U_REAL",
    event_timestamp: str = "1760001000.000001",
) -> None:
    store.record_vacancy_feedback_event(
        vacancy_id=vacancy_id,
        run_id=None,
        notification_id=None,
        vacancy_key=f"linkedin:example:{vacancy_id}",
        canonical_url="https://example.com/jobs/1",
        card_key=f"card:{vacancy_id}",
        slack_channel="C123",
        slack_message_ts=ts,
        feedback_type=feedback_type,
        event_type=event_type,
        event_timestamp=event_timestamp,
        user_id=user_id,
        raw_event_json={"reaction": feedback_type},
    )


def count_rows(store: JobIntelStore, table: str) -> int:
    with store.connect(read_only=True) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_reconciler_backfills_delivered_card_without_crm_mapping(tmp_path):
    store = make_store(tmp_path)
    vacancy_id = seed_vacancy(store)
    seed_message(store, vacancy_id=vacancy_id)
    reconciler = CRMReconciler(store)

    dry_run = reconciler.run(days=3650, dry_run=True, apply=False)
    assert any(action["action"] == "backfill_mapping" and action["would_create_opportunity"] for action in dry_run["actions"])

    apply_result = CRMReconciler(store).run(days=3650, dry_run=False, apply=True)
    assert apply_result["counts"]["mappings_to_create"] == 1

    with store.connect(read_only=True) as conn:
        assert int(conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0]) == 1
        assert int(conn.execute("SELECT COUNT(*) FROM slack_message_map").fetchone()[0]) == 1
        assert conn.execute("SELECT status FROM opportunities LIMIT 1").fetchone()[0] == "notified"


def test_reconciler_applies_active_save_for_later_to_watchlist(tmp_path):
    store = make_store(tmp_path)
    vacancy_id = seed_vacancy(store)
    seed_message(store, vacancy_id=vacancy_id)
    seed_feedback(store, vacancy_id=vacancy_id, ts="1760000000.123456", feedback_type="save_for_later", event_type="reaction_added")

    CRMReconciler(store).run(days=3650, dry_run=False, apply=True)

    with store.connect(read_only=True) as conn:
        status = conn.execute("SELECT status FROM opportunities LIMIT 1").fetchone()[0]
        task_type = conn.execute("SELECT task_type FROM opportunity_tasks LIMIT 1").fetchone()[0]
    assert status == "watchlist"
    assert task_type == "review_opportunity"


def test_reconciler_applies_active_interesting_to_evaluation_requested(tmp_path):
    store = make_store(tmp_path)
    vacancy_id = seed_vacancy(store)
    seed_message(store, vacancy_id=vacancy_id)
    seed_feedback(store, vacancy_id=vacancy_id, ts="1760000000.123456", feedback_type="interesting", event_type="reaction_added")

    CRMReconciler(store).run(days=3650, dry_run=False, apply=True)

    with store.connect(read_only=True) as conn:
        status = conn.execute("SELECT status FROM opportunities LIMIT 1").fetchone()[0]
    assert status == "evaluation_requested"


def test_reconciler_applies_active_not_interesting_to_declined_by_me(tmp_path):
    store = make_store(tmp_path)
    vacancy_id = seed_vacancy(store)
    seed_message(store, vacancy_id=vacancy_id)
    seed_feedback(store, vacancy_id=vacancy_id, ts="1760000000.123456", feedback_type="interesting", event_type="reaction_added")
    seed_feedback(store, vacancy_id=vacancy_id, ts="1760000000.123456", feedback_type="not_interesting", event_type="reaction_added")

    CRMReconciler(store).run(days=3650, dry_run=False, apply=True)

    with store.connect(read_only=True) as conn:
        status = conn.execute("SELECT status FROM opportunities LIMIT 1").fetchone()[0]
    assert status == "declined_by_me"


def test_reconciler_preserves_reaction_add_remove_history(tmp_path):
    store = make_store(tmp_path)
    vacancy_id = seed_vacancy(store)
    seed_message(store, vacancy_id=vacancy_id)
    seed_feedback(store, vacancy_id=vacancy_id, ts="1760000000.123456", feedback_type="save_for_later", event_type="reaction_added")
    seed_feedback(
        store,
        vacancy_id=vacancy_id,
        ts="1760000000.123456",
        feedback_type="save_for_later",
        event_type="reaction_removed",
        event_timestamp="1760002000.000001",
    )

    CRMReconciler(store).run(days=3650, dry_run=False, apply=True)

    with store.connect(read_only=True) as conn:
        rows = conn.execute("SELECT event_type, payload_json FROM opportunity_events ORDER BY id").fetchall()
    event_types = [row[0] for row in rows]
    assert "reaction_added" in event_types
    assert "reaction_removed" in event_types


def test_reconciler_idempotent(tmp_path):
    store = make_store(tmp_path)
    vacancy_id = seed_vacancy(store)
    seed_message(store, vacancy_id=vacancy_id)
    seed_feedback(store, vacancy_id=vacancy_id, ts="1760000000.123456", feedback_type="save_for_later", event_type="reaction_added")

    CRMReconciler(store).run(days=3650, dry_run=False, apply=True)
    first_counts = {
        table: count_rows(store, table)
        for table in ("opportunities", "slack_message_map", "opportunity_events", "opportunity_tasks", "opportunity_artifacts")
    }
    CRMReconciler(store).run(days=3650, dry_run=False, apply=True)
    second_counts = {
        table: count_rows(store, table)
        for table in ("opportunities", "slack_message_map", "opportunity_events", "opportunity_tasks", "opportunity_artifacts")
    }
    assert first_counts == second_counts


def test_reconciler_does_not_overwrite_terminal_status(tmp_path):
    store = make_store(tmp_path)
    vacancy_id = seed_vacancy(store)
    seed_message(store, vacancy_id=vacancy_id)
    seed_feedback(store, vacancy_id=vacancy_id, ts="1760000000.123456", feedback_type="save_for_later", event_type="reaction_added")
    CRMReconciler(store).run(days=3650, dry_run=False, apply=True)

    with store.connect() as conn:
        conn.execute("UPDATE opportunities SET status='closed'")

    CRMReconciler(store).run(days=3650, dry_run=False, apply=True)
    with store.connect(read_only=True) as conn:
        status = conn.execute("SELECT status FROM opportunities LIMIT 1").fetchone()[0]
    assert status in TERMINAL_GUARDED_STATUSES


def test_reconciler_dry_run_does_not_write(tmp_path):
    store = make_store(tmp_path)
    vacancy_id = seed_vacancy(store)
    seed_message(store, vacancy_id=vacancy_id)
    seed_feedback(store, vacancy_id=vacancy_id, ts="1760000000.123456", feedback_type="save_for_later", event_type="reaction_added")

    before = {
        table: count_rows(store, table)
        for table in ("opportunities", "slack_message_map", "opportunity_events", "opportunity_tasks", "opportunity_artifacts")
    }
    CRMReconciler(store).run(days=3650, dry_run=True, apply=False)
    after = {
        table: count_rows(store, table)
        for table in ("opportunities", "slack_message_map", "opportunity_events", "opportunity_tasks", "opportunity_artifacts")
    }
    assert before == after
