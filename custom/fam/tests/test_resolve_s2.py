import json

import pytest

from fam import cal, meds, react, resolve


NOW = "2026-09-04T10:00:00+00:00"
INCIDENT_TEXT = "Сегодня пропущу тренировку\nМисол приняла"


def _fixture(db):
    event = cal.add(db, "Тренировка", "2099-09-04T12:00:00+00:00")
    db.execute(
        "INSERT INTO reminders(event_id,kind,fire_at_utc,status,created_at,sent_at) "
        "VALUES(?,?,?,?,?,?)",
        (event["id"], "leave", "2026-09-04T09:30:00+00:00", "sent", NOW, NOW),
    )
    db.execute(
        "INSERT INTO reminders(event_id,kind,fire_at_utc,status,created_at,sent_at) "
        "VALUES(?,?,?,?,?,?)",
        (event["id"], "leave", "2099-09-04T12:00:00+00:00", "pending", NOW, None),
    )
    db.execute(
        "INSERT INTO meds(name,times,created_at,updated_at) VALUES(?,?,?,?)",
        ("Мисол", json.dumps(["10:00"]), NOW, NOW),
    )
    med_id = db.execute("SELECT id FROM meds ORDER BY id DESC LIMIT 1").fetchone()[0]
    db.execute(
        "INSERT INTO med_intakes(med_id,plan_ts_utc,status,created_at) VALUES(?,?,?,?)",
        (med_id, NOW, "pending", NOW),
    )
    intake_id = db.execute(
        "SELECT id FROM med_intakes ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    react.record_sent(db, "wa-rem-s2", "reminder", event["id"], event_id=event["id"], now_utc=NOW)
    react.record_sent(db, "wa-med-s2", "med", intake_id, event_id=event["id"], now_utc=NOW)
    db.commit()
    return event["id"], intake_id


def _config(tmp_path, dispositions):
    classifier = tmp_path / "classifier.py"
    payload = json.dumps({"dispositions": dispositions}, ensure_ascii=False)
    classifier.write_text(
        "import json\n"
        f"print({payload!r})\n",
        encoding="utf-8",
    )
    return {
        "gate_model": "test-model",
        "gate_provider": "test-provider",
        "classifier_command": ["/usr/bin/env", "python3", str(classifier)],
    }


def _request(event_id, intake_id):
    return {
        "platform": "whatsapp",
        "canonical_target": "whatsapp:+77011102626",
        "inbound_message_id": "wa-in-s2",
        "reply_to_message_id": "wa-rem-s2",
        "user_text": INCIDENT_TEXT,
        "quoted_text": "Напоминание о тренировке",
        "candidates": [
            {
                "kind": "event", "ref_id": event_id, "event_id": event_id,
                "current_state": "active", "wa_message_ids": ["wa-rem-s2"],
            },
            {
                "kind": "med_intake", "ref_id": intake_id,
                "current_state": "pending", "wa_message_ids": ["wa-med-s2"],
            },
        ],
    }


def test_incident_turn_applies_event_and_med_independently(db, tmp_path):
    event_id, intake_id = _fixture(db)
    request = _request(event_id, intake_id)
    cfg = _config(tmp_path, [
        {"kind": "event", "ref_id": event_id, "disposition": "cancel_occurrence"},
        {"kind": "med_intake", "ref_id": intake_id, "disposition": "taken"},
    ])

    result = resolve.resolve_turn(db, request, cfg=cfg)

    assert result["status"] == "applied"
    assert db.execute("SELECT status FROM events WHERE id=?", (event_id,)).fetchone()[0] == "cancelled"
    assert db.execute("SELECT status FROM med_intakes WHERE id=?", (intake_id,)).fetchone()[0] == "taken"
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='resolve.turn'"
    ).fetchone()[0] == 2
    assert db.execute(
        "SELECT COUNT(DISTINCT json_extract(payload, '$.idempotency_key')) "
        "FROM audit_log WHERE kind='resolve.turn'"
    ).fetchone()[0] == 2
    assert db.execute(
        "SELECT COUNT(*) FROM reminders WHERE event_id=? AND status='pending'",
        (event_id,),
    ).fetchone()[0] == 0


def test_partial_second_ref_keeps_first_applied_and_second_open(db, tmp_path, monkeypatch):
    event_id, intake_id = _fixture(db)
    real_apply = resolve._apply

    def fail_med(conn, candidate, disposition, request):
        if candidate["kind"] == "med_intake":
            raise RuntimeError("med executor failed")
        return real_apply(conn, candidate, disposition, request)

    monkeypatch.setattr(resolve, "_apply", fail_med)
    result = resolve.resolve_turn(
        db,
        _request(event_id, intake_id),
        cfg=_config(tmp_path, [
            {"kind": "event", "ref_id": event_id, "disposition": "cancel_occurrence"},
            {"kind": "med_intake", "ref_id": intake_id, "disposition": "taken"},
        ]),
    )

    assert result["status"] == "partial"
    assert result["residual"] is True
    assert "event" in result["trusted_sidecar"]
    assert db.execute("SELECT status FROM events WHERE id=?", (event_id,)).fetchone()[0] == "cancelled"
    assert db.execute("SELECT status FROM med_intakes WHERE id=?", (intake_id,)).fetchone()[0] == "pending"
    assert db.execute(
        "SELECT COUNT(*) FROM reminders WHERE event_id=? AND status='pending'",
        (event_id,),
    ).fetchone()[0] == 0


def test_partial_receipt_exposes_each_unresolved_ref_for_post_correlation(
    db, tmp_path, monkeypatch
):
    event_id, intake_id = _fixture(db)
    real_apply = resolve._apply

    def fail_med(conn, candidate, disposition, request):
        if candidate["kind"] == "med_intake":
            raise RuntimeError("med executor failed")
        return real_apply(conn, candidate, disposition, request)

    monkeypatch.setattr(resolve, "_apply", fail_med)
    result = resolve.resolve_turn(
        db,
        _request(event_id, intake_id),
        cfg=_config(tmp_path, [
            {"kind": "event", "ref_id": event_id, "disposition": "cancel_occurrence"},
            {"kind": "med_intake", "ref_id": intake_id, "disposition": "taken"},
        ]),
    )

    assert result["status"] == "partial"
    assert result["unresolved_refs"] == [
        {"kind": "med_intake", "ref_id": intake_id,
         "reason": "effect_failed:RuntimeError",
         "wa_message_ids": ["wa-med-s2"]}
    ]


@pytest.mark.parametrize("disposition", ["ambiguous", "unrelated"])
def test_nonterminal_second_ref_does_not_block_confident_first(db, tmp_path, disposition):
    event_id, intake_id = _fixture(db)
    result = resolve.resolve_turn(
        db,
        _request(event_id, intake_id),
        cfg=_config(tmp_path, [
            {"kind": "event", "ref_id": event_id, "disposition": "cancel_occurrence"},
            {"kind": "med_intake", "ref_id": intake_id, "disposition": disposition},
        ]),
    )

    assert result["status"] == "partial"
    assert db.execute("SELECT status FROM events WHERE id=?", (event_id,)).fetchone()[0] == "cancelled"
    assert db.execute("SELECT status FROM med_intakes WHERE id=?", (intake_id,)).fetchone()[0] == "pending"
    assert db.execute(
        "SELECT COUNT(*) FROM reminders WHERE event_id=? AND status='pending'",
        (event_id,),
    ).fetchone()[0] == 0


def test_multi_ref_replay_returns_both_saved_receipts_without_second_effect(db, tmp_path):
    event_id, intake_id = _fixture(db)
    request = _request(event_id, intake_id)
    cfg = _config(tmp_path, [
        {"kind": "event", "ref_id": event_id, "disposition": "cancel_occurrence"},
        {"kind": "med_intake", "ref_id": intake_id, "disposition": "taken"},
    ])

    first = resolve.resolve_turn(db, request, cfg=cfg)
    second = resolve.resolve_turn(db, request, cfg=cfg)

    assert first["status"] == second["status"] == "applied"
    assert {
        (item["kind"], item["ref_id"], item["disposition"])
        for item in second["dispositions"]
    } == {
        ("event", event_id, "cancel_occurrence"),
        ("med_intake", intake_id, "taken"),
    }
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='cal.cancel'"
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='meds.take'"
    ).fetchone()[0] == 1


def test_unrelated_leaves_no_quote_resolution_candidate_open(db, tmp_path):
    from fam import acks

    event_id, intake_id = _fixture(db)
    request = _request(event_id, intake_id)
    request["candidates"] = [request["candidates"][0]]
    result = resolve.resolve_turn(
        db,
        request,
        cfg=_config(tmp_path, [
            {"kind": "event", "ref_id": event_id, "disposition": "unrelated"},
        ]),
    )

    assert result["status"] == "unresolved"
    assert db.execute(
        "SELECT status FROM events WHERE id=?", (event_id,)
    ).fetchone()[0] == "active"
    assert any(
        item["ref_id"] == event_id
        for item in acks.build(db, now_utc=NOW)["items"]
    )


def test_multiple_open_candidates_apply_only_explicit_ref(db, tmp_path):
    event_id, intake_id = _fixture(db)
    other = __import__("fam.cal", fromlist=["add"]).add(
        db, "Другое событие", "2099-09-05T12:00:00+00:00"
    )
    react.record_sent(
        db, "wa-rem-s3-other", "reminder", other["id"],
        event_id=other["id"], now_utc=NOW,
    )
    db.commit()
    request = _request(event_id, intake_id)
    request["candidates"] = [
        request["candidates"][0],
        {
            "kind": "event", "ref_id": other["id"], "event_id": other["id"],
            "current_state": "active", "wa_message_ids": ["wa-rem-s3-other"],
        },
    ]

    result = resolve.resolve_turn(
        db,
        request,
        cfg=_config(tmp_path, [
            {"kind": "event", "ref_id": other["id"],
             "disposition": "cancel_occurrence"},
        ]),
    )

    assert result["status"] == "partial"
    assert [(item["kind"], item["ref_id"]) for item in result["applied"]] == [
        ("event", other["id"])
    ]
    assert db.execute(
        "SELECT status FROM events WHERE id=?", (event_id,)
    ).fetchone()[0] == "active"
    assert db.execute(
        "SELECT status FROM events WHERE id=?", (other["id"],)
    ).fetchone()[0] == "cancelled"


def test_ambiguous_multiple_open_candidates_mutates_none(db, tmp_path):
    event_id, intake_id = _fixture(db)
    other = __import__("fam.cal", fromlist=["add"]).add(
        db, "Ещё событие", "2099-09-06T12:00:00+00:00"
    )
    request = _request(event_id, intake_id)
    request["candidates"] = [
        request["candidates"][0],
        {
            "kind": "event", "ref_id": other["id"], "event_id": other["id"],
            "current_state": "active", "wa_message_ids": [],
        },
    ]

    result = resolve.resolve_turn(
        db,
        request,
        cfg=_config(tmp_path, [
            {"kind": "event", "ref_id": event_id, "disposition": "ambiguous"},
            {"kind": "event", "ref_id": other["id"], "disposition": "ambiguous"},
        ]),
    )

    assert result["status"] == "unresolved"
    assert db.execute(
        "SELECT status FROM events WHERE id=?", (event_id,)
    ).fetchone()[0] == "active"
    assert db.execute(
        "SELECT status FROM events WHERE id=?", (other["id"],)
    ).fetchone()[0] == "active"
