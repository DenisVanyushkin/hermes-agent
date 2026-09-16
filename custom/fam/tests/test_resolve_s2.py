import json

import pytest

from fam import cal, meds, react, resolve


def test_classifier_prompt_lists_allowed_dispositions_by_kind():
    prompt = resolve._classifier_prompt({
        "candidates": [
            {"kind": "event", "ref_id": 211},
            {"kind": "med_intake", "ref_id": 47},
        ],
        "user_text": "Сегодня пропущу тренировку",
        "quoted_text": "Напоминание о тренировке",
    })
    payload = json.loads(prompt)
    allowed = payload["allowed_dispositions"]

    for kind, dispositions in (
        ("event", resolve._EVENT_DISPOSITIONS),
        ("med_intake", resolve._MED_DISPOSITIONS),
    ):
        kind_prompt = json.dumps(allowed[kind], ensure_ascii=False)
        for disposition in dispositions:
            assert disposition in kind_prompt

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


def test_open_resolution_candidates_exclude_iphone_owned_event(db):
    from fam import rem

    event = cal.add(db, "iPhone event", "2099-09-07T12:00:00+00:00")
    db.execute("UPDATE events SET owner='iphone' WHERE id=?", (event["id"],))
    react.record_sent(
        db, "wa-iphone-rem", "reminder", event["id"],
        event_id=event["id"], now_utc=NOW,
    )
    db.commit()

    assert rem.open_resolution_candidates(db, now_utc=NOW) == []


def test_ack_then_next_projection_does_not_reemit_event_candidate(db, tmp_path):
    from fam import acks

    event_id, _ = _fixture(db)
    request = _request(event_id, 0)
    request["candidates"] = [request["candidates"][0]]
    result = resolve.resolve_turn(
        db,
        request,
        cfg=_config(tmp_path, [{
            "kind": "event", "ref_id": event_id,
            "disposition": "ack_chain_all",
        }]),
    )

    assert result["status"] == "applied"
    assert not any(
        item.get("kind") == "event" and item.get("ref_id") == event_id
        for item in acks.build(db, now_utc=NOW)["items"]
    )


def test_projection_is_written_after_effect_commit(db, tmp_path, monkeypatch):
    import sqlite3

    event_id, _ = _fixture(db)
    request = _request(event_id, 0)
    request["candidates"] = [request["candidates"][0]]
    observer = sqlite3.connect(str(tmp_path / "assistant.db"))
    observed = []

    def observe_projection(*_args, **_kwargs):
        observed.append(
            observer.execute(
                "SELECT status FROM events WHERE id=?", (event_id,)
            ).fetchone()[0]
        )

    monkeypatch.setattr(resolve.acks, "write", observe_projection)
    try:
        result = resolve.resolve_turn(
            db,
            request,
            cfg=_config(tmp_path, [{
                "kind": "event", "ref_id": event_id,
                "disposition": "cancel_occurrence",
            }]),
        )
    finally:
        observer.close()

    assert result["status"] == "applied"
    assert observed == ["cancelled"]


def test_text_resolver_does_not_accept_unsupported_snooze(db, tmp_path):
    event_id, intake_id = _fixture(db)
    request = _request(event_id, intake_id)
    request["candidates"] = [request["candidates"][1]]
    result = resolve.resolve_turn(
        db,
        request,
        cfg=_config(tmp_path, [{
            "kind": "med_intake", "ref_id": intake_id,
            "disposition": "snooze",
        }]),
    )

    assert "snooze" not in resolve._MED_DISPOSITIONS
    assert result["status"] == "unresolved"
    assert result["unresolved_refs"][0]["reason"] == "invalid_disposition"
    assert db.execute(
        "SELECT status FROM med_intakes WHERE id=?", (intake_id,)
    ).fetchone()[0] == "pending"


def test_applied_receipt_is_stored_in_unique_receipt_table(db, tmp_path):
    import sqlite3

    event_id, _ = _fixture(db)
    request = _request(event_id, 0)
    request["candidates"] = [request["candidates"][0]]
    result = resolve.resolve_turn(
        db,
        request,
        cfg=_config(tmp_path, [{
            "kind": "event", "ref_id": event_id,
            "disposition": "cancel_occurrence",
        }]),
    )

    assert result["status"] == "applied"
    row = db.execute(
        "SELECT idempotency_key, receipt FROM resolve_receipts"
    ).fetchone()
    assert row is not None
    assert json.loads(row["receipt"])["status"] == "applied"
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO resolve_receipts(idempotency_key, kind, ref_id, "
            "receipt, created_at) VALUES(?,?,?,?,?)",
            (row["idempotency_key"], "event", event_id,
             row["receipt"], NOW),
        )


def test_resolver_rejects_stale_iphone_event_candidate(db, tmp_path):
    event = cal.add(db, "iPhone event", "2099-09-08T12:00:00+00:00")
    db.execute("UPDATE events SET owner='iphone' WHERE id=?", (event["id"],))
    react.record_sent(
        db, "wa-stale-iphone-rem", "reminder", event["id"],
        event_id=event["id"], now_utc=NOW,
    )
    db.commit()
    request = _request(event["id"], 0)
    request["candidates"] = [request["candidates"][0]]
    result = resolve.resolve_turn(
        db,
        request,
        cfg=_config(tmp_path, [{
            "kind": "event", "ref_id": event["id"],
            "disposition": "cancel_occurrence",
        }]),
    )

    assert result["status"] == "unresolved"
    assert result["unresolved_refs"][0]["reason"] == "owner_not_hermes"
    assert db.execute(
        "SELECT status FROM events WHERE id=?", (event["id"],)
    ).fetchone()[0] == "active"
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='cal.cancel'"
    ).fetchone()[0] == 0


def test_legacy_audit_receipt_replays_when_new_store_row_is_missing(
    db, tmp_path, monkeypatch
):
    event_id, _ = _fixture(db)
    request = _request(event_id, 0)
    request["candidates"] = [request["candidates"][0]]
    cfg = _config(tmp_path, [{
        "kind": "event", "ref_id": event_id,
        "disposition": "cancel_occurrence",
    }])
    first = resolve.resolve_turn(db, request, cfg=cfg)
    key = resolve._turn_key(request, request["candidates"][0])
    db.execute(
        "DELETE FROM resolve_receipts WHERE idempotency_key=?", (key,)
    )
    db.commit()
    monkeypatch.setattr(
        resolve, "_call_classifier",
        lambda *_args, **_kwargs: pytest.fail("legacy receipt was not reused"),
    )

    second = resolve.resolve_turn(db, request, cfg=cfg)

    assert first["status"] == second["status"] == "applied"
    assert second["dispositions"] == first["dispositions"]
    assert db.execute(
        "SELECT status FROM events WHERE id=?", (event_id,)
    ).fetchone()[0] == "cancelled"



def test_concurrent_same_ref_persists_one_effect_and_one_receipt(
    db, tmp_path
):
    from concurrent.futures import ThreadPoolExecutor
    from fam import db as famdb

    event_id, _ = _fixture(db)
    request = _request(event_id, 0)
    request["candidates"] = [request["candidates"][0]]
    cfg = _config(tmp_path, [{
        "kind": "event", "ref_id": event_id,
        "disposition": "cancel_occurrence",
    }])
    path = str(tmp_path / "assistant.db")

    def resolve_from_separate_connection():
        conn = famdb.connect(path)
        try:
            return resolve.resolve_turn(conn, request, cfg=cfg)
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda _unused: resolve_from_separate_connection(), (1, 2)
        ))

    assert [result["status"] for result in results] == ["applied", "applied"]
    assert db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE kind='cal.cancel'"
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM resolve_receipts"
    ).fetchone()[0] == 1


def test_committed_ref_receipt_survives_projection_crash(
    db, tmp_path, monkeypatch
):
    """A crash after one commit can replay that ref without reapplying it."""
    event_id, intake_id = _fixture(db)
    request = _request(event_id, intake_id)
    cfg = _config(tmp_path, [
        {"kind": "event", "ref_id": event_id,
         "disposition": "cancel_occurrence"},
        {"kind": "med_intake", "ref_id": intake_id, "disposition": "taken"},
    ])
    real_write = resolve.acks.write

    def crash_after_first_commit(*_args, **_kwargs):
        monkeypatch.setattr(resolve.acks, "write", real_write)
        raise RuntimeError("projection crash")

    monkeypatch.setattr(resolve.acks, "write", crash_after_first_commit)
    with pytest.raises(RuntimeError, match="projection crash"):
        resolve.resolve_turn(db, request, cfg=cfg)

    assert db.execute(
        "SELECT status FROM events WHERE id=?", (event_id,)
    ).fetchone()[0] == "cancelled"
    assert db.execute(
        "SELECT COUNT(*) FROM resolve_receipts"
    ).fetchone()[0] == 1

    replayed = resolve.resolve_turn(db, request, cfg=cfg)

    assert replayed["status"] == "applied"
    assert db.execute(
        "SELECT status FROM med_intakes WHERE id=?", (intake_id,)
    ).fetchone()[0] == "taken"


def test_unresolved_receipt_keeps_failure_provenance(db, tmp_path, monkeypatch):
    event_id, _ = _fixture(db)
    monkeypatch.setattr(resolve, "_call_classifier", lambda *_args, **_kwargs: None)

    result = resolve.resolve_turn(
        db, _request(event_id, 0), cfg=_config(tmp_path, [])
    )

    assert result["status"] == "unresolved"
    payload = next(
        json.loads(row["payload"])
        for row in db.execute(
            "SELECT payload FROM audit_log WHERE kind='resolve.turn' ORDER BY id"
        )
    )
    assert payload["model"] == "test-model"
    assert payload["prompt_version"]
    assert payload["input_sha256"]
    assert payload["output_sha256"] is None


def test_classifier_timeout_kills_each_process_group(monkeypatch):
    import signal
    import subprocess

    processes = []
    killed = []

    class FakeProcess:
        pid = 41001
        returncode = None

        def communicate(self, timeout):
            raise subprocess.TimeoutExpired("classifier", timeout)

    def fake_popen(*_args, **_kwargs):
        processes.append(_kwargs)
        return FakeProcess()

    monkeypatch.setattr(resolve.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(resolve.os, "killpg", lambda pid, sig: killed.append((pid, sig)))

    assert resolve._call_classifier("prompt", {
        "gate_model": "m", "gate_provider": "p",
        "classifier_command": ["classifier"],
        "resolve_classifier_timeout_seconds": 0.01,
    }) is None
    assert len(processes) == 2
    assert all(item["start_new_session"] is True for item in processes)
    assert killed == [(41001, signal.SIGKILL), (41001, signal.SIGKILL)]
