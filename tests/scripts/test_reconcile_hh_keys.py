from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

from job_intel.dedup import canonical_vacancy_key
from job_intel.models import Evaluation, Vacancy
from job_intel.store import JobIntelStore
import scripts.job_intel_reconcile_hh_keys as reconcile_module
from scripts.job_intel_reconcile_hh_keys import reconcile_connection, reconcile_database


def _vacancy(url: str, description: str = "") -> Vacancy:
    return Vacancy(
        source="headhunter",
        source_id="123",
        company="Acme",
        title="Head of Product",
        location="Алматы",
        url=url,
        description=description,
    )


def _seed(store: JobIntelStore, vacancy: Vacancy, key: str) -> int:
    return store.upsert_vacancy(vacancy, key)


def test_recomputes_key_from_the_canonical_url(tmp_path):
    db = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db)
    store.bootstrap()
    vacancy = _vacancy("https://hh.ru/vacancy/123?query=Head+of+Product")
    old_key = "legacy-key"
    _seed(store, vacancy, old_key)

    report = reconcile_database(db, apply=True)

    assert report.rekeyed == 1
    with store.connect(read_only=True) as conn:
        row = conn.execute("SELECT vacancy_key FROM vacancies").fetchone()
    assert row[0] == canonical_vacancy_key(vacancy)


def test_rekey_moves_foreign_key_children_before_parent_commit(tmp_path):
    db = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db)
    store.bootstrap()
    vacancy = _vacancy("https://hh.ru/vacancy/123?query=Head+of+Product")
    desired_key = canonical_vacancy_key(vacancy)
    _seed(store, vacancy, "legacy-key")
    store.save_evaluation(
        "legacy-key", Evaluation(score=40, tier="weak_fit", recommendation="reject")
    )
    run_id = store.start_run("test")
    now = datetime.now(timezone.utc).isoformat()
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO user_feedback_opportunities (vacancy_key, run_id, updated_at) "
            "VALUES (?, ?, ?)",
            ("legacy-key", run_id, now),
        )
        conn.commit()

    report = reconcile_database(db, apply=True)

    assert report.rekeyed == 1
    with store.connect(read_only=True) as conn:
        assert conn.execute(
            "SELECT vacancy_key FROM vacancy_evaluations"
        ).fetchone()[0] == desired_key
        assert conn.execute(
            "SELECT vacancy_key FROM user_feedback_opportunities"
        ).fetchone()[0] == desired_key


def test_merges_into_the_existing_row_when_the_new_key_already_exists(tmp_path):
    db = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db)
    store.bootstrap()
    canonical = _vacancy("https://hh.ru/vacancy/123", description="")
    legacy = _vacancy(
        "https://hh.ru/vacancy/123?query=Head+of+Product",
        description="real description " + "x" * 300,
    )
    new_key = canonical_vacancy_key(canonical)
    survivor_id = _seed(store, canonical, new_key)
    loser_id = _seed(store, legacy, "legacy-key")
    store.save_evaluation("legacy-key", Evaluation(score=40, tier="weak_fit", recommendation="reject"))

    report = reconcile_database(db, apply=True)

    assert report.merged == 1
    with store.connect(read_only=True) as conn:
        rows = conn.execute("SELECT id, vacancy_key, description FROM vacancies").fetchall()
        evaluations = conn.execute(
            "SELECT vacancy_key FROM vacancy_evaluations ORDER BY id"
        ).fetchall()
    assert [(row[0], row[1]) for row in rows] == [(survivor_id, new_key)]
    assert rows[0][2].startswith("real description")
    assert [row[0] for row in evaluations] == [new_key]
    assert loser_id not in {row[0] for row in rows}


def test_child_rows_follow_the_merge(tmp_path):
    db = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db)
    store.bootstrap()
    canonical = _vacancy("https://hh.ru/vacancy/123", description="x" * 400)
    legacy = _vacancy("https://hh.ru/vacancy/123?query=old")
    new_key = canonical_vacancy_key(canonical)
    survivor_id = _seed(store, canonical, new_key)
    loser_id = _seed(store, legacy, "legacy-key")
    run_id = store.start_run("test")
    now = datetime.now(timezone.utc).isoformat()
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO vacancy_observability "
            "(run_id, vacancy_key, source, role_bucket, geo_bucket, industry_bucket, score, score_band, url, created_at) "
            "VALUES (?, ?, 'headhunter', 'product', 'kz', 'tech', 50, 'potential_fit', ?, ?)",
            (run_id, "legacy-key", legacy.url, now),
        )
        conn.execute(
            "INSERT INTO vacancy_rejection_summary "
            "(run_id, vacancy_key, source, score, score_band, created_at) VALUES (?, ?, 'headhunter', 1, 'reject', ?)",
            (run_id, "legacy-key", now),
        )
        conn.execute(
            "INSERT INTO vacancy_rejection_events "
            "(run_id, vacancy_key, source, role_bucket, geo_bucket, industry_bucket, score, score_band, rejection_reason, created_at) "
            "VALUES (?, ?, 'headhunter', 'product', 'kz', 'tech', 1, 'reject', 'closed', ?)",
            (run_id, "legacy-key", now),
        )
        conn.execute(
            "INSERT INTO vacancy_slack_messages "
            "(vacancy_id, vacancy_key, slack_channel, slack_message_ts, company, title, score, recommendation, url, created_at) "
            "VALUES (?, ?, 'C1', '1.1', 'Acme', 'Head of Product', 1, 'reject', ?, ?)",
            (loser_id, "legacy-key", legacy.url, now),
        )
        conn.execute(
            "INSERT INTO vacancy_feedback "
            "(vacancy_id, vacancy_key, slack_message_ts, feedback_type, event_type, event_timestamp, user_id, created_at) "
            "VALUES (?, ?, '1.1', 'interesting', 'reaction', ?, 'U1', ?)",
            (loser_id, "legacy-key", now, now),
        )
        conn.execute(
            "INSERT INTO vacancy_feedback_state "
            "(vacancy_id, vacancy_key, slack_message_ts, user_id, feedback_type, updated_at) "
            "VALUES (?, ?, '1.1', 'U1', 'interesting', ?)",
            (loser_id, "legacy-key", now),
        )
        conn.execute(
            "INSERT INTO user_feedback_opportunities (vacancy_key, run_id, updated_at) VALUES (?, ?, ?)",
            ("legacy-key", run_id, now),
        )
        conn.commit()

    report = reconcile_database(db, apply=True)

    assert report.child_rows_moved >= 7
    with store.connect(read_only=True) as conn:
        for table in (
            "vacancy_observability",
            "vacancy_rejection_summary",
            "vacancy_rejection_events",
            "vacancy_slack_messages",
            "vacancy_feedback",
            "vacancy_feedback_state",
            "user_feedback_opportunities",
        ):
            if table in {"vacancy_slack_messages", "vacancy_feedback", "vacancy_feedback_state"}:
                assert conn.execute(
                    f"SELECT count(*) FROM {table} WHERE vacancy_id = ? AND vacancy_key = ?",
                    (survivor_id, new_key),
                ).fetchone()[0] == 1
            else:
                assert conn.execute(
                    f"SELECT count(*) FROM {table} WHERE vacancy_key = ?",
                    (new_key,),
                ).fetchone()[0] == 1


def test_is_idempotent(tmp_path):
    db = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db)
    store.bootstrap()
    vacancy = _vacancy("https://hh.ru/vacancy/123?old=1")
    _seed(store, vacancy, "legacy-key")

    first = reconcile_database(db, apply=True)
    second = reconcile_database(db, apply=True)

    assert first.rekeyed == 1
    assert second.rekeyed == 0
    assert second.merged == 0
    assert second.already_canonical == 1


def test_never_discards_a_non_empty_description_for_an_empty_one(tmp_path):
    db = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db)
    store.bootstrap()
    canonical = _vacancy("https://hh.ru/vacancy/123", description="full text " + "x" * 300)
    legacy = _vacancy("https://hh.ru/vacancy/123?old=1", description="")
    new_key = canonical_vacancy_key(canonical)
    _seed(store, canonical, new_key)
    _seed(store, legacy, "legacy-key")

    reconcile_database(db, apply=True)

    with store.connect(read_only=True) as conn:
        assert conn.execute(
            "SELECT description FROM vacancies WHERE vacancy_key = ?", (new_key,)
        ).fetchone()[0].startswith("full text")


def test_collision_refuses_without_deleting_either_child_or_vacancy(tmp_path):
    db = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db)
    store.bootstrap()
    canonical = _vacancy("https://hh.ru/vacancy/123")
    legacy = _vacancy("https://hh.ru/vacancy/123?old=1")
    new_key = canonical_vacancy_key(canonical)
    survivor_id = _seed(store, canonical, new_key)
    loser_id = _seed(store, legacy, "legacy-key")
    run_id = store.start_run("test")
    now = datetime.now(timezone.utc).isoformat()
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO vacancy_observability "
            "(run_id, vacancy_key, source, role_bucket, geo_bucket, industry_bucket, score, score_band, url, created_at) "
            "VALUES (?, ?, 'headhunter', 'product', 'kz', 'tech', 50, 'potential_fit', ?, ?)",
            (run_id, new_key, canonical.url, now),
        )
        conn.execute(
            "INSERT INTO vacancy_observability "
            "(run_id, vacancy_key, source, role_bucket, geo_bucket, industry_bucket, score, score_band, url, created_at) "
            "VALUES (?, ?, 'headhunter', 'product', 'kz', 'tech', 40, 'reject', ?, ?)",
            (run_id, "legacy-key", canonical.url, now),
        )
        conn.execute(
            "INSERT INTO vacancy_feedback_state "
            "(vacancy_id, vacancy_key, slack_message_ts, user_id, feedback_type, updated_at) "
            "VALUES (?, ?, '1.1', 'U1', 'interesting', ?)",
            (survivor_id, new_key, now),
        )
        conn.execute(
            "INSERT INTO vacancy_feedback_state "
            "(vacancy_id, vacancy_key, slack_message_ts, user_id, feedback_type, updated_at) "
            "VALUES (?, ?, '1.2', 'U1', 'interesting', ?)",
            (loser_id, "legacy-key", now),
        )
        conn.commit()

    report = reconcile_database(db, apply=True)

    assert report.merged == 0
    assert report.collisions_refused == 1
    assert report.collision_child_rows_refused == 2
    with store.connect(read_only=True) as conn:
        assert conn.execute("SELECT count(*) FROM vacancies").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM vacancy_observability").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM vacancy_feedback_state").fetchone()[0] == 2


def test_direct_dry_run_connection_does_not_persist_changes(tmp_path):
    db = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db)
    store.bootstrap()
    vacancy = _vacancy("https://hh.ru/vacancy/123?old=1")
    _seed(store, vacancy, "legacy-key")

    with sqlite3.connect(db) as conn:
        report = reconcile_connection(conn, apply=False)
        conn.commit()

    assert report.rekeyed == 1
    with store.connect(read_only=True) as conn:
        assert conn.execute("SELECT vacancy_key FROM vacancies").fetchone()[0] == "legacy-key"


def test_database_dry_run_snapshots_a_read_only_source(tmp_path, monkeypatch):
    db = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db)
    store.bootstrap()
    _seed(store, _vacancy("https://hh.ru/vacancy/123?old=1"), "legacy-key")
    real_connect = reconcile_module.sqlite3.connect
    calls = []

    def _connect(database, *args, **kwargs):
        calls.append((database, kwargs))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(reconcile_module.sqlite3, "connect", _connect)

    reconcile_module.reconcile_database(db, apply=False)

    assert any(
        kwargs.get("uri") is True and "mode=ro" in str(database)
        for database, kwargs in calls
    )
    assert all(str(database) != ":memory:" for database, _ in calls)


def test_merge_preserves_richer_vacancy_fields(tmp_path):
    db = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db)
    store.bootstrap()
    canonical = _vacancy("https://hh.ru/vacancy/123")
    legacy = _vacancy("https://hh.ru/vacancy/123?old=1")
    new_key = canonical_vacancy_key(canonical)
    survivor_id = _seed(store, canonical, new_key)
    loser_id = _seed(store, legacy, "legacy-key")
    with store.connect() as conn:
        conn.execute(
            "UPDATE vacancies SET salary = '1000 USD', posted_at = '2026-08-20', "
            "text_backfill_state = 'ok', text_backfill_at = '2026-08-20T00:00:00Z' WHERE id = ?",
            (loser_id,),
        )
        conn.commit()

    reconcile_database(db, apply=True)

    with store.connect(read_only=True) as conn:
        row = conn.execute(
            "SELECT salary, posted_at, text_backfill_state, text_backfill_at FROM vacancies WHERE id = ?",
            (survivor_id,),
        ).fetchone()
    assert tuple(row) == ("1000 USD", "2026-08-20", "ok", "2026-08-20T00:00:00Z")


def _merge_with_backfill_states(tmp_path, survivor_state, loser_state):
    db = tmp_path / "job_intel.sqlite3"
    store = JobIntelStore(db)
    store.bootstrap()
    canonical = _vacancy("https://hh.ru/vacancy/123")
    legacy = _vacancy("https://hh.ru/vacancy/123?old=1")
    new_key = canonical_vacancy_key(canonical)
    survivor_id = _seed(store, canonical, new_key)
    loser_id = _seed(store, legacy, "legacy-key")
    with store.connect() as conn:
        conn.execute(
            "UPDATE vacancies SET text_backfill_state = ? WHERE id = ?",
            (survivor_state, survivor_id),
        )
        conn.execute(
            "UPDATE vacancies SET text_backfill_state = ? WHERE id = ?",
            (loser_state, loser_id),
        )
        conn.commit()

    reconcile_database(db, apply=True)

    with store.connect(read_only=True) as conn:
        return conn.execute(
            "SELECT text_backfill_state FROM vacancies WHERE id = ?",
            (survivor_id,),
        ).fetchone()[0]


def test_merge_prefers_retryable_failed_over_terminal_unavailable(tmp_path):
    assert _merge_with_backfill_states(tmp_path, "unavailable", "failed") == "failed"


def test_merge_does_not_turn_unattempted_text_into_terminal_unavailable(tmp_path):
    assert _merge_with_backfill_states(tmp_path, None, "unavailable") is None
