"""S4 review coverage: hash transition, safe 412s, and quarantine exit."""
import json
import types

import pytest

from fam import cal, cli, extcal, gate


WRITE_URL = "https://caldav.icloud.com/1/calendars/hermes/"
NOW = "2037-07-15T00:00:00+00:00"


def _cfg(**over):
    cfg = dict(gate.CONFIG_DEFAULTS)
    cfg.update({
        "extcal_enabled": True,
        "extcal_username": "amina@example.com",
        "extcal_write_calendar": WRITE_URL,
        "extcal_horizon_weeks": 8,
    })
    cfg.update(over)
    return cfg


def _event(db, title="Local", start="2037-07-20T13:00:00+00:00"):
    event = cal.add(db, title, start, end_utc="2037-07-20T14:00:00+00:00")
    db.commit()
    return event


def _seed_export(db, event, body_hash, etag='"e0"'):
    db.execute(
        "INSERT INTO ext_exports(event_id, href, etag, body_hash, synced_at) "
        "VALUES (?,?,?,?,?)",
        (event["id"], f"{WRITE_URL}fam-{event['id']}@hermes-home.ics",
         etag, body_hash, NOW),
    )
    db.commit()


def _ics(event_id, title="Remote", uid=None, start="20370720T130000Z",
         end="20370720T140000Z", location=None):
    uid = uid or f"fam-{event_id}@hermes-home"
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "BEGIN:VEVENT",
        f"UID:{uid}", "DTSTAMP:20370715T000000Z",
        f"DTSTART:{start}", f"DTEND:{end}", f"SUMMARY:{title}",
    ]
    if location is not None:
        lines.append(f"LOCATION:{location}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def _snapshot(db, tmp_path, name):
    db.commit()
    target = tmp_path / f"{name}.sqlite"
    db.execute("VACUUM INTO ?", (str(target),))
    return target.read_bytes()


def _make_conflict(db, event):
    def request(method, url, **kwargs):
        if method == "PUT":
            return extcal.Response(412, b"", {})
        return extcal.Response(
            200, _ics(event["id"], "Phone").encode(), {"ETag": '"phone"'}
        )

    counts = extcal.export_own(db, _cfg(), request=request, now_utc=NOW)
    assert counts["conflicts"]
    return counts


def test_marked_legacy_equivalent_rebaselines_without_network(db):
    event = _event(db)
    legacy = extcal._export_body_hash_v1(event, "", [])
    _seed_export(db, event, "v1:" + legacy)

    counts = extcal.export_own(
        db, _cfg(),
        request=lambda *a, **k: pytest.fail("equivalent v1 must not use network"),
        now_utc=NOW,
    )

    assert counts["unchanged"] == 1
    assert db.execute(
        "SELECT body_hash FROM ext_exports WHERE event_id=?", (event["id"],)
    ).fetchone()["body_hash"].startswith("v2:")


def test_marked_legacy_mismatch_uses_one_uid_proved_get_and_no_put(db):
    event = _event(db)
    _seed_export(db, event, "v1:" + "0" * 64)
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        return extcal.Response(
            200, _ics(event["id"], uid="foreign@phone").encode(),
            {"ETag": '"foreign"'},
        )

    counts = extcal.export_own(db, _cfg(), request=request, now_utc=NOW)

    assert calls == ["GET"]
    assert len(counts["errors"]) == 1
    assert counts["errors"][0]["reason_code"] == "invalid_response"
    assert db.execute(
        "SELECT body_hash FROM ext_exports WHERE event_id=?", (event["id"],)
    ).fetchone()["body_hash"] == "v1:" + "0" * 64


def test_retained_divergent_legacy_row_stays_v1_without_get_or_put(db):
    event = _event(db, start="2037-07-10T13:00:00+00:00")
    stored = "v1:" + "0" * 64
    _seed_export(db, event, stored)
    calls = []

    counts = extcal.export_own(
        db, _cfg(),
        request=lambda method, *a, **k: calls.append(method),
        now_utc=NOW,
    )

    assert counts["retained"] == 1
    assert calls == []
    assert db.execute(
        "SELECT body_hash FROM ext_exports WHERE event_id=?", (event["id"],)
    ).fetchone()["body_hash"] == stored


def test_mixed_v1_and_v2_rows_reconcile_without_network(db):
    first = _event(db, "v1")
    second = _event(db, "v2", start="2037-07-21T13:00:00+00:00")
    _seed_export(db, first, "v1:" + extcal._export_body_hash_v1(first, "", []))
    _seed_export(db, second, extcal._export_body_hash(second, "", []))

    counts = extcal.export_own(
        db, _cfg(),
        request=lambda *a, **k: pytest.fail("mixed equivalent rows must be local"),
        now_utc=NOW,
    )

    assert counts["unchanged"] == 2
    hashes = db.execute(
        "SELECT body_hash FROM ext_exports ORDER BY event_id"
    ).fetchall()
    assert all(row["body_hash"].startswith("v2:") for row in hashes)


def test_eligible_legacy_mismatch_bad_get_is_fail_closed_and_preserves_journal(db):
    event = _event(db)
    _seed_export(db, event, "v1:" + "1" * 64)
    before = dict(db.execute(
        "SELECT * FROM ext_exports WHERE event_id=?", (event["id"],)
    ).fetchone())
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        if method == "GET":
            return extcal.Response(500, b"server failure", {})
        pytest.fail("legacy mismatch must not PUT before a valid GET")

    counts = extcal.export_own(db, _cfg(), request=request, now_utc=NOW)

    assert calls == ["GET"]
    assert counts["errors"][0]["reason_code"] == "invalid_response"
    assert dict(db.execute(
        "SELECT * FROM ext_exports WHERE event_id=?", (event["id"],)
    ).fetchone()) == before


@pytest.mark.parametrize("bad_response", [
    None,
    extcal.Response(500, b"", {}),
    extcal.Response(200, b"not an ics", {"ETag": '"bad"'}),
    extcal.Response(200, _ics(1, uid="foreign@phone").encode(), {"ETag": '"bad"'}),
])
def test_put_412_bad_get_variants_preserve_journal_and_do_not_retry(
    db, bad_response
):
    event = _event(db)
    old_hash = extcal._export_body_hash(event, "", [])
    _seed_export(db, event, old_hash)
    db.execute("UPDATE events SET title='Changed' WHERE id=?", (event["id"],))
    db.commit()
    before = dict(db.execute(
        "SELECT * FROM ext_exports WHERE event_id=?", (event["id"],)
    ).fetchone())
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        if method == "PUT":
            return extcal.Response(412, b"", {})
        return bad_response

    counts = extcal.export_own(db, _cfg(), request=request, now_utc=NOW)

    assert calls == ["PUT", "GET"]
    assert len(counts["errors"]) == 1
    assert dict(db.execute(
        "SELECT * FROM ext_exports WHERE event_id=?", (event["id"],)
    ).fetchone()) == before


def test_initial_put_412_remote_desired_creates_journal_without_put_retry(db):
    event = _event(db)
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        if method == "PUT":
            return extcal.Response(412, b"", {})
        return extcal.Response(
            200, _ics(event["id"], "Local").encode(), {"ETag": '"remote"'}
        )

    counts = extcal.export_own(db, _cfg(), request=request, now_utc=NOW)

    assert calls == ["PUT", "GET"]
    assert counts["exported"] == 1
    assert db.execute(
        "SELECT etag FROM ext_exports WHERE event_id=?", (event["id"],)
    ).fetchone()["etag"] == '"remote"'


@pytest.mark.parametrize("status", [404, 410])
def test_delete_initial_404_or_410_finishes_journal(db, status):
    event = _event(db)
    _seed_export(db, event, extcal._export_body_hash(event, "", []))
    cal.cancel(db, event["id"])
    db.commit()
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        return extcal.Response(status, b"", {})

    counts = extcal.export_own(db, _cfg(), request=request, now_utc=NOW)

    assert counts["deleted"] == 1
    assert calls == ["DELETE"]
    assert db.execute(
        "SELECT 1 FROM ext_exports WHERE event_id=?", (event["id"],)
    ).fetchone() is None


def test_remote_only_move_beyond_horizon_is_conflict_and_never_delete(db):
    event = _event(db, start="2037-12-20T13:00:00+00:00")
    _seed_export(db, event, extcal._export_body_hash(event, "", []))
    db.execute("UPDATE events SET title='Local changed' WHERE id=?", (event["id"],))
    db.commit()
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        if method == "PUT":
            return extcal.Response(412, b"", {})
        return extcal.Response(
            200,
            _ics(event["id"], "Phone moved", start="20371221T130000Z").encode(),
            {"ETag": '"phone"'},
        )

    counts = extcal.export_own(db, _cfg(), request=request, now_utc=NOW)

    assert calls == ["PUT", "GET"]
    assert counts["conflicts"] and counts["deleted"] == 0
    assert db.execute(
        "SELECT action FROM extcal_export_issues WHERE event_id=?", (event["id"],)
    ).fetchone()["action"] == "put"


def test_resolve_target_omission_selects_the_only_conflict(db):
    event = _event(db)
    _make_conflict(db, event)
    calls = []

    def request(method, url, **kwargs):
        calls.append(method)
        if method == "GET":
            return extcal.Response(
                200, _ics(event["id"], "Phone accepted").encode(),
                {"ETag": '"phone"'},
            )
        pytest.fail("keep-remote with one target must not PUT")

    result = extcal.resolve_conflict(
        db, event["id"], target=None, decision="keep-remote",
        cfg=_cfg(), request=request, now_utc=NOW,
    )

    assert result["target"] == "hermes"
    assert calls == ["GET"]


def test_resolve_target_omission_rejects_ambiguous_conflict_without_db_change(
    db, tmp_path
):
    event = _event(db)
    _make_conflict(db, event)
    db.execute(
        "INSERT INTO extcal_export_issues("
        "target,event_id,action,kind,http_status,reason_code,"
        "first_seen_utc,last_seen_utc) VALUES(?,?,?,?,?,?,?,?)",
        ("taya", event["id"], "put", "conflict", 412, "conflict", NOW, NOW),
    )
    db.commit()
    before = _snapshot(db, tmp_path, "ambiguous-before")
    calls = []

    with pytest.raises(extcal._ExportFailure, match="target required"):
        extcal.resolve_conflict(
            db, event["id"], target=None, decision="keep-remote",
            cfg=_cfg(), request=lambda *a, **k: calls.append(a[0]),
            now_utc=NOW,
        )

    after = _snapshot(db, tmp_path, "ambiguous-after")
    assert calls == []
    assert before == after


def test_resolve_invalid_remote_is_byte_identical_noop(db, tmp_path):
    event = _event(db)
    _make_conflict(db, event)
    before = _snapshot(db, tmp_path, "invalid-before")

    def request(method, url, **kwargs):
        return extcal.Response(
            200, _ics(event["id"], location="unknown place").encode(),
            {"ETag": '"phone"'},
        )

    with pytest.raises(extcal._ExportFailure, match="known place"):
        extcal.resolve_conflict(
            db, event["id"], decision="keep-remote", cfg=_cfg(),
            request=request, now_utc=NOW,
        )

    after = _snapshot(db, tmp_path, "invalid-after")
    assert before == after


def test_resolve_audits_both_commands_without_gate_deliver(
    db, monkeypatch, capsys
):
    event = _event(db)
    _make_conflict(db, event)
    monkeypatch.setattr(cli.famdb, "connect", lambda: db)
    monkeypatch.setattr(cli.gate, "load_config", lambda: _cfg())
    monkeypatch.setattr(
        cli.gate, "deliver",
        lambda *a, **k: pytest.fail("cal-ext operator commands must not deliver"),
    )

    assert cli.cmd_cal_ext_conflicts(
        types.SimpleNamespace(json=True)
    ) == 0
    json.loads(capsys.readouterr().out)
    assert db.execute(
        "SELECT 1 FROM audit_log WHERE kind='cal.ext.conflicts'"
    ).fetchone() is not None

    def request(method, url, **kwargs):
        if method == "GET":
            return extcal.Response(
                200, _ics(event["id"], "Phone").encode(), {"ETag": '"phone"'}
            )
        return extcal.Response(204, b"", {"ETag": '"hermes"'})

    monkeypatch.setattr(cli.extcal, "_request", request)
    assert cli.cmd_cal_ext_resolve(types.SimpleNamespace(
        event_id=event["id"], target=None, force_push=True, json=True
    )) == 0
    json.loads(capsys.readouterr().out)
    assert db.execute(
        "SELECT 1 FROM audit_log WHERE kind='cal.ext.resolve'"
    ).fetchone() is not None


def test_resolve_then_next_tick_reconciles_again(db):
    event = _event(db)
    _make_conflict(db, event)
    calls = []

    def keep_remote(method, url, **kwargs):
        calls.append(method)
        if method == "GET":
            return extcal.Response(
                200, _ics(event["id"], "Phone").encode(), {"ETag": '"phone"'}
            )
        pytest.fail("keep remote must not PUT")

    extcal.resolve_conflict(
        db, event["id"], decision="keep-remote", cfg=_cfg(),
        request=keep_remote, now_utc=NOW,
    )
    db.execute("UPDATE events SET title='Hermes again' WHERE id=?", (event["id"],))
    db.commit()

    def put_again(method, url, **kwargs):
        calls.append(method)
        return extcal.Response(204, b"", {"ETag": '"new"'})

    counts = extcal.export_own(db, _cfg(), request=put_again, now_utc=NOW)
    assert counts["updated"] == 1
    assert calls == ["GET", "PUT"]


def test_force_push_commit_crash_is_repaired_without_second_put(db, monkeypatch):
    event = _event(db)
    _make_conflict(db, event)
    real_record = extcal._export_record
    state = {"failed": False}

    def record_then_crash(*args, **kwargs):
        real_record(*args, **kwargs)
        if not state["failed"]:
            state["failed"] = True
            raise RuntimeError("simulated journal commit window")

    monkeypatch.setattr(extcal, "_export_record", record_then_crash)
    calls = []

    def first_request(method, url, **kwargs):
        calls.append(method)
        if method == "GET":
            return extcal.Response(
                200, _ics(event["id"], "Phone").encode(), {"ETag": '"phone"'}
            )
        return extcal.Response(204, b"", {"ETag": '"hermes-new"'})

    with pytest.raises(RuntimeError):
        extcal.resolve_conflict(
            db, event["id"], decision="force-push", cfg=_cfg(),
            request=first_request, now_utc=NOW,
        )
    assert calls == ["GET", "PUT"]
    assert db.execute(
        "SELECT kind FROM extcal_export_issues WHERE event_id=?", (event["id"],)
    ).fetchone()["kind"] == "conflict"

    monkeypatch.setattr(extcal, "_export_record", real_record)
    repair_calls = []

    def repair_request(method, url, **kwargs):
        repair_calls.append(method)
        if method == "GET":
            return extcal.Response(
                200, extcal._build_export_vevent(
                    event, "", [], extcal._coerce_utc_dt(NOW)
                ).encode(),
                {"ETag": '"hermes-new"'},
            )
        pytest.fail("repair must not repeat force-push PUT")

    extcal.resolve_conflict(
        db, event["id"], decision="force-push", cfg=_cfg(),
        request=repair_request, now_utc=NOW,
    )
    assert repair_calls == ["GET"]
    assert db.execute(
        "SELECT 1 FROM extcal_export_issues WHERE event_id=?", (event["id"],)
    ).fetchone() is None
