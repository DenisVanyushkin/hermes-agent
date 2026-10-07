"""A closed occurrence must stay closed when its reminder times change."""

import json
import sqlite3

import pytest

from fam import audit, cal, db as famdb, gate, places, rem, resolve, series, tick


NOW = "2099-10-07T04:06:00+00:00"
START = "2099-10-07T05:00:00+00:00"


def _event(db):
    event = cal.add(db, "Training", START, prep_min=45)
    db.execute("UPDATE events SET travel_min_road=10 WHERE id=?", (event["id"],))
    rem.regenerate(db, event["id"], now_utc=NOW)
    db.commit()
    return event["id"]


def _pending(db, event_id):
    return db.execute(
        "SELECT kind FROM reminders WHERE event_id=? AND status='pending' "
        "ORDER BY fire_at_utc",
        (event_id,),
    ).fetchall()


def _change_road(db, event_id, now="2099-10-07T04:20:00+00:00"):
    db.execute("UPDATE events SET travel_min_road=3 WHERE id=?", (event_id,))
    rem.regenerate(db, event_id, now_utc=now)


def test_ack_all_survives_regeneration_after_database_reopen(db):
    event_id = _event(db)
    assert rem.ack_chain(db, event_id, scope="all") > 0
    db.execute("DELETE FROM audit_log")  # retention is not acknowledgement loss
    db.commit()
    path = db.execute("PRAGMA database_list").fetchone()[2]
    with sqlite3.connect(path) as reopened:
        reopened.row_factory = sqlite3.Row
        _change_road(reopened, event_id)
        assert _pending(reopened, event_id) == []
        assert cal.get(reopened, event_id)["status"] == "active"


def test_prepare_ack_survives_regeneration_but_keeps_leave_stages(db):
    event_id = _event(db)
    assert rem.ack_chain(db, event_id, scope="prepare") > 0
    _change_road(db, event_id, now="2099-10-07T04:07:00+00:00")
    assert [r["kind"] for r in _pending(db, event_id)] == ["leave"] * 3


def test_prepare_ack_cannot_downgrade_a_closed_all_chain(db):
    event_id = _event(db)
    rem.ack_chain(db, event_id, scope="all")
    rem.ack_chain(db, event_id, scope="prepare")
    _change_road(db, event_id)
    assert _pending(db, event_id) == []


def test_all_ack_with_no_pending_rows_still_closes_future_regeneration(db):
    event_id = _event(db)
    db.execute("DELETE FROM reminders WHERE event_id=?", (event_id,))
    assert rem.ack_chain(db, event_id, scope="all") == 0
    _change_road(db, event_id)
    assert _pending(db, event_id) == []


def test_explicit_cancel_survives_regeneration(db):
    event_id = _event(db)
    assert rem.cancel_chain(db, event_id) > 0
    _change_road(db, event_id)
    assert _pending(db, event_id) == []


def test_next_series_occurrence_keeps_its_own_reminders(db):
    plan = series.add(db, "Training", "mon,wed,fri", "10:00", prep_min=45)
    series.generate(db, now_utc="2099-10-07T00:00:00+00:00")
    occurrences = db.execute(
        "SELECT id FROM events WHERE series_id=? ORDER BY start_utc",
        (plan["id"],),
    ).fetchall()
    assert len(occurrences) >= 2
    first, second = occurrences[0]["id"], occurrences[1]["id"]
    rem.ack_chain(db, first, scope="all")
    rem.regenerate(db, first, now_utc=NOW)
    rem.regenerate(db, second, now_utc=NOW)
    assert _pending(db, first) == []
    assert len(_pending(db, second)) > 0


def test_resolve_already_driving_then_road_tick_does_not_send(
    db, tmp_path, monkeypatch
):
    event_id = _event(db)
    place = places.add(db, "Gym", lat=43.2, lon=76.9)
    db.execute(
        "UPDATE events SET place_id=?, road_checked_at=NULL WHERE id=?",
        (place["id"], event_id),
    )
    db.commit()
    classifier = tmp_path / "classifier.py"
    reply = json.dumps(
        {
            "dispositions": [
                {"kind": "event", "ref_id": event_id, "disposition": "ack_chain_all"},
            ]
        }
    )
    classifier.write_text(f"print({reply!r})\n", encoding="utf-8")
    receipt = resolve.resolve_turn(
        db,
        {
            "platform": "whatsapp",
            "canonical_target": "whatsapp:+123456789",
            "inbound_message_id": "already-driving",
            "user_text": "Уже еду",
            "now_utc": NOW,
            "candidates": [
                {
                    "kind": "event",
                    "ref_id": event_id,
                    "event_id": event_id,
                    "current_state": "active",
                    "wa_message_ids": [],
                }
            ],
        },
        cfg={
            "gate_model": "test",
            "gate_provider": "test",
            "classifier_command": ["/usr/bin/env", "python3", str(classifier)],
            "pending_acks_path": str(tmp_path / "pending-acks.json"),
        },
    )
    assert receipt["status"] == "applied"
    # Only the external route estimate is substituted; tick, SQLite,
    # regeneration and resolution all run their production code.
    monkeypatch.setattr(tick.road, "compute_travel_min", lambda *a, **k: (3, "tomtom"))
    monkeypatch.setattr(gate, "_call_rewrite", lambda *a, **k: None)
    monkeypatch.setattr(gate, "_call_send", lambda *a, **k: (False, None))
    cfg = {
        "road_home_lat": 43.19,
        "road_home_lon": 76.87,
        "road_recompute_min": [120, 60],
        "daily_budget": 100,
        "max_len_reminder": 300,
        "max_len_digest": 900,
        "digest_retry_from": "00:00",
        "digest_retry_until": "00:00",
    }
    assert tick.road_recompute(db, now_utc="2099-10-07T04:20:00+00:00", cfg=cfg) == 1
    assert cal.get(db, event_id)["travel_min_road"] == 3
    assert _pending(db, event_id) == []
    result = tick.reminders(db, now_utc="2099-10-07T04:58:00+00:00", cfg=cfg)
    assert result["due"] == result["sent"] == result["error"] == 0


@pytest.mark.parametrize(
    "scope,count,pending_kinds",
    [
        ("all", 4, []),
        ("all", 0, []),
        ("prepare", 1, ["leave"] * 3),
        ("prepare", 0, ["leave"] * 3),
    ],
)
def test_legacy_migration_restores_only_successful_ack_scopes(
    db, scope, count, pending_kinds
):
    event_id = _event(db)
    # Before a no-op prepare ack, the prepare phase was already over.
    if count == 0:
        db.execute(
            "UPDATE reminders SET status='sent' WHERE event_id=? AND kind='prepare'",
            (event_id,),
        )
    columns = {r["name"] for r in db.execute("PRAGMA table_info(events)")}
    if "reminder_closed_scope" in columns:
        db.execute("ALTER TABLE events DROP COLUMN reminder_closed_scope")
    audit.log(db, "rem.ack", {"event_id": event_id, "scope": scope, "count": count})
    db.commit()
    famdb.init_db(db)
    # Already regenerated pending rows must also be closed by migration.
    assert [r["kind"] for r in _pending(db, event_id)] == pending_kinds
    _change_road(db, event_id)
    expected = ["leave"] * 3 if scope == "prepare" else []
    assert [r["kind"] for r in _pending(db, event_id)] == expected
    famdb.init_db(db)
    _change_road(db, event_id)
    assert [r["kind"] for r in _pending(db, event_id)] == expected


def test_migration_preserves_all_scope_after_later_noop_prepare(db):
    event_id = _event(db)
    db.execute("ALTER TABLE events DROP COLUMN reminder_closed_scope")
    audit.log(db, "rem.ack", {"event_id": event_id, "scope": "all", "count": 4})
    audit.log(db, "rem.ack", {"event_id": event_id, "scope": "prepare", "count": 0})
    db.commit()
    famdb.init_db(db)
    _change_road(db, event_id)
    assert _pending(db, event_id) == []


def test_migration_restores_cancel_and_skips_malformed_audit(db):
    event_id = _event(db)
    db.execute("ALTER TABLE events DROP COLUMN reminder_closed_scope")
    audit.log(db, "rem.cancel_chain", {"event_id": event_id, "count": 4})
    audit.log(db, "rem.ack", ["bad"])
    db.execute(
        "INSERT INTO audit_log(ts_utc,kind,payload) VALUES(?,?,?)",
        (NOW, "rem.ack", "invalid json"),
    )
    db.commit()
    famdb.init_db(db)
    assert _pending(db, event_id) == []
    _change_road(db, event_id)
    assert _pending(db, event_id) == []


def test_noop_prepare_does_not_close_a_future_prepare_stage(db):
    event_id = _event(db)
    db.execute(
        "UPDATE reminders SET status='sent' WHERE event_id=? AND kind='prepare'",
        (event_id,),
    )
    assert rem.ack_chain(db, event_id, scope="prepare") == 0
    _change_road(db, event_id, now=NOW)
    assert [r["kind"] for r in _pending(db, event_id)] == ["prepare"] * 2 + [
        "leave"
    ] * 3


def test_rolled_back_ack_does_not_close_the_occurrence(db):
    event_id = _event(db)
    rem.ack_chain(db, event_id, scope="all")
    db.rollback()
    _change_road(db, event_id)
    assert [r["kind"] for r in _pending(db, event_id)] == ["leave"] * 3


@pytest.mark.parametrize(
    "new_stamp,keep_creation",
    [
        ("2026-10-07T00:00:00+00:00", True),
        ("2026-10-07T00:00:00+00:00", False),
        ("2026-10-07T00:00:01+00:00", False),
    ],
)
def test_migration_does_not_apply_old_ack_to_reused_id(
    db, monkeypatch, new_stamp, keep_creation
):
    # Timestamp precision alone cannot identify the new occurrence.
    stamp = "2026-10-07T00:00:00+00:00"
    monkeypatch.setattr(cal, "_now", lambda: stamp)
    monkeypatch.setattr(audit, "_now", lambda: stamp)
    plan = series.add(db, "Old training", "mon,wed,fri", "10:00", prep_min=45)
    series.generate(db, now_utc="2099-10-07T00:00:00+00:00")
    old_id = db.execute("SELECT MIN(id) FROM events").fetchone()[0]
    rem.ack_chain(db, old_id, scope="all")
    series.cancel(db, plan["id"])
    assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    monkeypatch.setattr(cal, "_now", lambda: new_stamp)
    monkeypatch.setattr(audit, "_now", lambda: new_stamp)
    fresh = cal.add(db, "New unrelated event", START, prep_min=45)
    assert fresh["id"] == old_id
    if not keep_creation:
        db.execute("DELETE FROM audit_log WHERE kind='cal.add'")
    db.execute("ALTER TABLE events DROP COLUMN reminder_closed_scope")
    db.commit()
    famdb.init_db(db)
    assert len(_pending(db, fresh["id"])) == 5
    assert cal.get(db, fresh["id"])["reminder_closed_scope"] == "none"


def test_migration_recovers_later_ack_when_creation_audit_was_pruned(db, monkeypatch):
    monkeypatch.setattr(cal, "_now", lambda: "2026-01-01T00:00:00+00:00")
    event_id = _event(db)
    rem.ack_chain(db, event_id, scope="all")
    db.execute("DELETE FROM audit_log WHERE kind='cal.add'")
    db.execute("ALTER TABLE events DROP COLUMN reminder_closed_scope")
    db.commit()
    famdb.init_db(db)
    _change_road(db, event_id)
    assert _pending(db, event_id) == []
