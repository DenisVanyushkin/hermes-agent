import json
from datetime import datetime, timezone

import pytest

from fam import audit, cli, diag


def test_audit_tick_error_records_exception_type(db):
    cli._audit_tick_error("reminders", KeyError("No item with that key"))
    row = db.execute(
        "SELECT payload FROM audit_log WHERE kind='tick.error'").fetchone()
    payload = json.loads(row["payload"])
    assert payload["where"] == "reminders"
    assert payload["exc_type"] == "KeyError"


def test_audit_tick_error_accepts_plain_string(db):
    # cli.py:1245 passes a joined string, not an exception -- exc_type is
    # None there rather than "str", which would be meaningless noise.
    cli._audit_tick_error("offsite", "backup failed; disk full")
    payload = json.loads(db.execute(
        "SELECT payload FROM audit_log WHERE kind='tick.error'").fetchone()["payload"])
    assert payload["exc_type"] is None
    assert "disk full" in payload["error"]


def test_normalize_signature_collapses_numbers_and_hex():
    assert diag.normalize_signature("row 4211 of abcdef1234567890") == \
        "row <n> of <hex>"


def test_identical_errors_collapse_into_one_finding(db):
    for _ in range(113):
        audit.log(db, "tick.error",
                  {"where": "meds_row", "intake_id": 10, "exc_type": "KeyError",
                   "error": "No item with that key"}, actor="tick")
    db.commit()
    findings = diag.collect_errors(db, "1970-01-01T00:00:00+00:00")
    assert len(findings) == 1
    assert findings[0]["count"] == 113
    assert findings[0]["kind"] == "tick.error"
    assert findings[0]["p_where"] == "meds_row"
    assert findings[0]["p_exc_type"] == "KeyError"
    assert findings[0]["context"]["intake_id"] == [10]


def test_same_defect_on_several_doses_stays_one_finding(db):
    for intake in (10, 11, 12):
        audit.log(db, "tick.error",
                  {"where": "meds_row", "intake_id": intake, "exc_type": "KeyError",
                   "error": "No item with that key"}, actor="tick")
    db.commit()
    findings = diag.collect_errors(db, "1970-01-01T00:00:00+00:00")
    assert len(findings) == 1, "intake_id must not split the signature"
    assert findings[0]["context"]["intake_id"] == [10, 11, 12]


def test_road_error_without_error_text_is_not_lost(db):
    # road.py writes {"event_id": ...} with no "error" key at all.
    audit.log(db, "road.error", {"event_id": 7})
    db.commit()
    findings = diag.collect_errors(db, "1970-01-01T00:00:00+00:00")
    assert len(findings) == 1
    assert findings[0]["kind"] == "road.error"
    assert findings[0]["context"]["event_id"] == [7]


def test_gate_error_never_exposes_message_text(db):
    audit.log(db, "gate.error",
              {"kind": "reminder", "attempt": 2,
               "raw": {"text": "прими лекарство"}, "final": "Прими лекарство"})
    db.commit()
    findings = diag.collect_errors(db, "1970-01-01T00:00:00+00:00")
    blob = json.dumps(findings, ensure_ascii=False)
    assert "лекарство" not in blob
    assert "final" not in blob and "raw" not in blob
    assert findings[0]["kind"] == "gate.error"
    assert findings[0]["p_kind"] == "reminder"


def test_unparseable_json_payload_still_yields_finding(db):
    # A malformed error row (non-JSON) must produce a finding, not silently
    # disappear -- it's exactly the kind of breakage this digest must surface.
    db.execute(
        "INSERT INTO audit_log (ts_utc, kind, payload, actor) "
        "VALUES (datetime('now'), 'tick.error', 'not json', 'test')")
    db.commit()
    findings = diag.collect_errors(db, "1970-01-01T00:00:00+00:00")
    assert len(findings) == 1
    assert findings[0]["kind"] == "tick.error"
    assert findings[0]["count"] == 1


def test_mail_error_event_id_in_context_and_text_in_signature(db):
    audit.log(db, "mail.error",
              {"event_id": 42, "error": "DNS resolution failed"}, actor="mail")
    db.commit()
    findings = diag.collect_errors(db, "1970-01-01T00:00:00+00:00")
    assert len(findings) == 1
    assert findings[0]["kind"] == "mail.error"
    assert findings[0]["context"]["event_id"] == [42]
    assert "DNS resolution failed" in findings[0]["examples"]


def test_context_overflow_is_counted_not_silently_dropped(db):
    # 40 distinct broken intake_ids capped at MAX_CONTEXT_VALUES (10) would
    # otherwise be indistinguishable from exactly 10 -- the cap must say
    # how much it dropped, mirroring the reporter's findings_truncated.
    for intake in range(40):
        audit.log(db, "tick.error",
                  {"where": "meds_row", "intake_id": intake, "exc_type": "KeyError",
                   "error": "No item with that key"}, actor="tick")
    db.commit()
    findings = diag.collect_errors(db, "1970-01-01T00:00:00+00:00")
    assert len(findings) == 1
    finding = findings[0]
    assert len(finding["context"]["intake_id"]) == diag.MAX_CONTEXT_VALUES
    assert finding["context_truncated"] == {"intake_id": 40 - diag.MAX_CONTEXT_VALUES}


def test_context_under_cap_gets_no_truncated_marker(db):
    for intake in (10, 11, 12):
        audit.log(db, "tick.error",
                  {"where": "meds_row", "intake_id": intake, "exc_type": "KeyError",
                   "error": "No item with that key"}, actor="tick")
    db.commit()
    findings = diag.collect_errors(db, "1970-01-01T00:00:00+00:00")
    assert "context_truncated" not in findings[0], \
        "a marker present when nothing was dropped would be its own false signal"


def test_examples_overflow_is_counted_not_silently_dropped(db):
    # Same mechanism, mirrored for MAX_EXAMPLES (3): distinct example
    # strings beyond the cap must be counted, not silently discarded.
    for i in range(6):
        audit.log(db, "mail.error",
                  {"event_id": i, "error": f"unique failure {i}"}, actor="mail")
    db.commit()
    findings = diag.collect_errors(db, "1970-01-01T00:00:00+00:00")
    assert len(findings) == 1, "digits normalize to <n> so all six share one signature"
    finding = findings[0]
    assert len(finding["examples"]) == diag.MAX_EXAMPLES
    assert finding["examples_truncated"] == 6 - diag.MAX_EXAMPLES


def test_first_sighting_is_new_then_known(db):
    now1 = datetime(2026, 8, 1, 22, 30, tzinfo=timezone.utc)
    findings = [{"signature": "tick.error|where=meds_row", "count": 3}]
    annotated, resolved, state = diag.diff_known_issues({}, findings, now1)
    assert annotated[0]["status"] == "new"
    assert annotated[0]["age_days"] == 0
    assert resolved == []

    now2 = datetime(2026, 8, 4, 22, 30, tzinfo=timezone.utc)
    annotated, resolved, state = diag.diff_known_issues(state, findings, now2)
    assert annotated[0]["status"] == "known"
    assert annotated[0]["age_days"] == 3


def test_disappeared_signature_becomes_resolved(db):
    now1 = datetime(2026, 8, 1, 22, 30, tzinfo=timezone.utc)
    _, _, state = diag.diff_known_issues(
        {}, [{"signature": "tick.error|where=digest", "count": 1}], now1)
    now2 = datetime(2026, 8, 2, 22, 30, tzinfo=timezone.utc)
    annotated, resolved, state = diag.diff_known_issues(state, [], now2)
    assert annotated == []
    assert [r["signature"] for r in resolved] == ["tick.error|where=digest"]
    assert state == {}, "a resolved signature must not linger in state forever"


def test_state_round_trips_through_meta(db):
    diag.save_state(db, {"sig": {"first_seen": "2026-08-01T00:00:00+00:00",
                                 "last_seen": "2026-08-01T00:00:00+00:00", "count": 2}})
    db.commit()
    loaded = diag.load_state(db)
    assert loaded["sig"]["count"] == 2
    assert loaded["sig"]["first_seen"] == "2026-08-01T00:00:00+00:00"
    assert loaded["sig"]["last_seen"] == "2026-08-01T00:00:00+00:00"


def test_corrupt_state_degrades_to_empty(db):
    from fam import db as famdb
    famdb.meta_set(db, diag.STATE_KEY, "{not json")
    db.commit()
    assert diag.load_state(db) == {}


def test_leaf_corrupted_state_degrades_safely(db):
    # Hand-edited or partially-written state like {"sig1": "not-a-dict"}
    # would blow up diff_known_issues' prior.get() without leaf validation.
    from fam import db as famdb
    famdb.meta_set(db, diag.STATE_KEY, '{"sig1": "not-a-dict-value"}')
    db.commit()
    loaded = diag.load_state(db)
    assert loaded == {}, "leaf-corrupted entries must drop silently"
    # Must not raise AttributeError on prior.get()
    now = datetime(2026, 8, 1, 22, 30, tzinfo=timezone.utc)
    annotated, resolved, new_state = diag.diff_known_issues(
        loaded, [{"signature": "sig1", "count": 1}], now)
    assert len(annotated) == 1
    assert annotated[0]["status"] == "new"


def test_unparseable_first_seen_demotes_to_new(db):
    # A naive timestamp like "2026-08-01T00:00:00" (no timezone) raises
    # TypeError on datetime.fromisoformat() and subtraction. Must demote
    # to "new" status, not leave it as "known" with age_days=0.
    now = datetime(2026, 8, 4, 22, 30, tzinfo=timezone.utc)
    state = {
        "sig1": {
            "first_seen": "2026-08-01T00:00:00",  # naive, no timezone
            "last_seen": "2026-08-01T00:00:00",
            "count": 1
        }
    }
    annotated, resolved, new_state = diag.diff_known_issues(
        state, [{"signature": "sig1", "count": 1}], now)
    assert annotated[0]["status"] == "new", "unparseable first_seen must demote to new"
    assert annotated[0]["age_days"] == 0


def test_activity_counts_by_message_kind(db):
    audit.log(db, "gate.sent", {"kind": "reminder", "raw": {"text": "секрет"}})
    audit.log(db, "gate.sent", {"kind": "med", "raw": {"text": "секрет"}})
    audit.log(db, "gate.sent", {"kind": "med", "raw": {"text": "секрет"}})
    audit.log(db, "tick.med", {"intake_id": 1})
    audit.log(db, "meds.take", {"intake_id": 1})
    db.commit()
    now = datetime(2026, 8, 1, 22, 30, tzinfo=timezone.utc)
    activity = diag.collect_activity(db, {"daily_budget": 8},
                                     "1970-01-01T00:00:00+00:00", now)
    assert activity["sent_by_kind"] == {"reminder": 1, "med": 2}
    assert activity["meds_generated"] == 1
    assert activity["meds_taken"] == 1
    assert activity["budget_limit"] == 8
    assert "секрет" not in json.dumps(activity, ensure_ascii=False)


def test_calendar_reports_collisions_only(db):
    audit.log(db, "cal.ext.sync", {"collisions": 2, "title": "Врач в 15:00"})
    audit.log(db, "cal.ext.sync", {"collisions": 1})
    db.commit()
    calendar = diag.collect_calendar(db, "1970-01-01T00:00:00+00:00")
    assert calendar == {"collisions": 3}


def test_timers_split_failed_and_ok():
    calls = []

    class _Result:
        def __init__(self, stdout):
            self.stdout, self.returncode = stdout, 0

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "--failed" in cmd:
            return _Result("fam-car.service loaded failed failed Amina fam: car\n")
        return _Result("fam-reminders.timer loaded active waiting Amina fam\n"
                       "fam-car.timer loaded active waiting Amina fam\n")

    timers = diag.collect_timers(runner=fake_run)
    # SUB is the literal "failed" again here -- redundant with the ACTIVE
    # column -- so detail falls back to the unit's Description text
    # ("Amina fam: car") rather than repeating "failed".
    assert timers["failed"] == [{"unit": "fam-car.service", "detail": "Amina fam: car"}]
    assert timers["ok"] == ["fam-reminders.timer", "fam-car.timer"]


def test_timers_raises_on_nonzero_returncode():
    # A dead session bus (no live XDG_RUNTIME_DIR under a systemd timer,
    # for instance) answers with a non-zero code and EMPTY stdout -- which
    # would otherwise parse into a fake "no failed units, no timers"
    # all-clear. Must raise instead of reporting health it never observed.
    class _Result:
        def __init__(self):
            self.stdout, self.stderr, self.returncode = "", "Failed to connect to bus", 1

    def fake_run(cmd, **kwargs):
        return _Result()

    with pytest.raises(RuntimeError, match="systemctl"):
        diag.collect_timers(runner=fake_run)


def test_timers_empty_stdout_with_ok_returncode_is_not_an_error():
    class _Result:
        def __init__(self):
            self.stdout, self.stderr, self.returncode = "", "", 0

    def fake_run(cmd, **kwargs):
        return _Result()

    assert diag.collect_timers(runner=fake_run) == {"failed": [], "ok": []}


def test_backups_report_verify_result(tmp_path):
    backup = tmp_path / "assistant-20260801.db"
    backup.write_bytes(b"x")
    now = datetime(2026, 8, 1, 22, 30, tzinfo=timezone.utc)
    result = diag.collect_backups(
        {"backup_dir": str(tmp_path), "offsite_enabled": False}, now,
        verify=lambda path: (True, {"integrity": "ok", "schema_version": "12"}))
    assert result["last_path"] == "assistant-20260801.db"
    assert result["verify"] == "ok"
    assert result["schema_version"] == "12"


def test_backups_missing_reports_no_backup_found(tmp_path):
    now = datetime(2026, 8, 1, 22, 30, tzinfo=timezone.utc)
    result = diag.collect_backups(
        {"backup_dir": str(tmp_path), "offsite_enabled": False}, now,
        verify=lambda path: (True, {"integrity": "ok", "schema_version": "12"}))
    assert result == {"last_path": None, "verify": "missing", "schema_version": None,
                      "offsite_age_days": None}


def test_backups_reports_verify_failure_detail(tmp_path):
    backup = tmp_path / "assistant-20260801.db"
    backup.write_bytes(b"x")
    now = datetime(2026, 8, 1, 22, 30, tzinfo=timezone.utc)
    result = diag.collect_backups(
        {"backup_dir": str(tmp_path), "offsite_enabled": False}, now,
        verify=lambda path: (False, {"integrity": "corrupt", "schema_version": None}))
    assert result["verify"] == "corrupt"
    assert result["schema_version"] is None


def test_backups_offsite_age_days_computed_from_newest_dump(tmp_path):
    backup_dir = tmp_path / "local"
    backup_dir.mkdir()
    (backup_dir / "assistant-20260801.db").write_bytes(b"x")
    offsite_dir = tmp_path / "offsite"
    offsite_dir.mkdir()
    (offsite_dir / "assistant-20260728.db.age").write_bytes(b"x")
    now = datetime(2026, 8, 1, 22, 30, tzinfo=timezone.utc)
    result = diag.collect_backups(
        {"backup_dir": str(backup_dir), "offsite_enabled": True,
         "offsite_dir": str(offsite_dir)}, now,
        verify=lambda path: (True, {"integrity": "ok", "schema_version": "12"}))
    assert result["offsite_age_days"] == 4


def test_build_digest_truncates_probe_detail_to_120_chars(db, monkeypatch):
    # health.py's probe detail is unowned free text straight from
    # gateway.log -- no allow-list, no cap in health.py itself (its other
    # callers need the full line). build_digest's probes section must
    # re-impose that discipline by length before the digest leaves the
    # host, for every probe, not just unhealthy ones.
    long_line = "✓ whatsapp connected " + ("bridge-detail-" * 20)
    assert len(long_line) > diag.PROBE_DETAIL_MAX
    monkeypatch.setattr(diag.health, "all_probes",
                        lambda conn, cfg, now=None: [
                            {"name": "bridge_readiness", "status": "ok",
                             "detail": long_line, "last_ok_ts": None}])
    now = datetime(2026, 8, 1, 22, 30, tzinfo=timezone.utc)
    digest, _ = diag.build_digest(
        db, {"daily_budget": 8, "backup_dir": "/nonexistent"},
        "1970-01-01T00:00:00+00:00", now,
        delivery={"previous_report_ok": True}, state={},
        verify=lambda p: (True, {}))
    probe = digest["sections"]["probes"][0]
    assert probe["name"] == "bridge_readiness"
    assert probe["status"] == "ok"
    assert len(probe["detail"]) == diag.PROBE_DETAIL_MAX
    assert probe["detail"] == long_line[:diag.PROBE_DETAIL_MAX]


def test_build_digest_isolates_a_failing_section(db, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("systemctl gone")
    monkeypatch.setattr(diag, "collect_timers", boom)
    now = datetime(2026, 8, 1, 22, 30, tzinfo=timezone.utc)
    digest, _ = diag.build_digest(
        db, {"daily_budget": 8, "backup_dir": "/nonexistent"},
        "1970-01-01T00:00:00+00:00", now,
        delivery={"previous_report_ok": True}, state={},
        verify=lambda p: (True, {}))
    assert "timers" not in digest["sections"]
    assert "RuntimeError" in digest["section_errors"]["timers"]
    assert "activity" in digest["sections"], "other sections must survive"


def test_build_digest_keeps_state_when_error_section_fails(db, monkeypatch):
    monkeypatch.setattr(diag, "collect_errors",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("db gone")))
    now = datetime(2026, 8, 1, 22, 30, tzinfo=timezone.utc)
    prior = {"sig": {"first_seen": "2026-07-01T00:00:00+00:00",
                     "last_seen": "2026-07-31T00:00:00+00:00", "count": 1}}
    digest, new_state = diag.build_digest(
        db, {"daily_budget": 8, "backup_dir": "/nonexistent"},
        "1970-01-01T00:00:00+00:00", now,
        delivery={"previous_report_ok": True}, state=prior,
        verify=lambda p: (True, {}))
    assert new_state == prior, "a failed collection must not wipe age tracking"


def test_write_digest_is_atomic_and_rotates(tmp_path):
    now = datetime(2026, 8, 1, 22, 30, tzinfo=timezone.utc)
    for day in range(1, 17):
        old = tmp_path / f"fam-digest-202607{day:02d}.json"
        old.write_text("{}", encoding="utf-8")
    path = diag.write_digest({"generated_at": now.isoformat()}, tmp_path, now)
    assert path.name == "fam-digest-latest.json"
    assert json.loads(path.read_text(encoding="utf-8"))["generated_at"]
    assert (tmp_path / "fam-digest-20260801.json").exists()
    dated = sorted(tmp_path.glob("fam-digest-????????.json"))
    assert len(dated) == diag.ROTATE_DAYS
    assert not list(tmp_path.glob("*.tmp"))


def _jobs_file(tmp_path, **overrides):
    job = {"name": "fam-nightly-report", "last_status": "ok",
           "last_run_at": "2026-08-02T03:00:12+00:00", "last_delivery_error": None}
    job.update(overrides)
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps([job]), encoding="utf-8")
    return path


def test_delivery_ok_when_job_ran_after_digest(tmp_path):
    ok, detail = diag.report_delivery_status(
        _jobs_file(tmp_path), "fam-nightly-report", "2026-08-01T22:30:01+00:00")
    assert ok is True
    assert "2026-08-02" in detail


def test_delivery_not_ok_on_delivery_error(tmp_path):
    ok, detail = diag.report_delivery_status(
        _jobs_file(tmp_path, last_delivery_error="telegram 502"),
        "fam-nightly-report", "2026-08-01T22:30:01+00:00")
    assert ok is False
    assert "telegram 502" in detail


def test_delivery_not_ok_when_run_predates_digest(tmp_path):
    ok, detail = diag.report_delivery_status(
        _jobs_file(tmp_path, last_run_at="2026-07-30T03:00:00+00:00"),
        "fam-nightly-report", "2026-08-01T22:30:01+00:00")
    assert ok is False
    assert "predates" in detail


def test_delivery_not_ok_when_job_missing_or_file_unreadable(tmp_path):
    ok, _ = diag.report_delivery_status(
        _jobs_file(tmp_path), "no-such-job", "2026-08-01T22:30:01+00:00")
    assert ok is False
    ok, detail = diag.report_delivery_status(
        tmp_path / "absent.json", "fam-nightly-report", "2026-08-01T22:30:01+00:00")
    assert ok is False
    assert "unreadable" in detail


def test_first_run_is_not_confirmed(tmp_path):
    # No previous digest means the job did not exist yet: treating this as
    # confirmed would advance the watermark having sent nothing at all.
    ok, detail = diag.report_delivery_status(
        _jobs_file(tmp_path), "fam-nightly-report", None)
    assert ok is False
    assert "first run" in detail


def test_delivery_not_ok_on_none_jobs_path():
    # Critical: cfg.get("report_jobs_path", DEFAULT) substitutes the default
    # only when the key is absent, not when it is present with value null.
    # Must return False (cost: one redundant raw message) not raise.
    ok, detail = diag.report_delivery_status(None, "fam-nightly-report", "2026-08-01T22:30:01+00:00")
    assert ok is False
    assert "unreadable" in detail
    assert "TypeError" in detail


def test_delivery_with_dict_keyed_by_job_id(tmp_path):
    # Production jobs.json shape: a dict keyed by job id, not a list.
    job = {"name": "fam-nightly-report", "last_status": "ok",
           "last_run_at": "2026-08-02T03:00:12+00:00", "last_delivery_error": None}
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps({"job-123": job, "job-456": {}}), encoding="utf-8")
    ok, detail = diag.report_delivery_status(path, "fam-nightly-report", "2026-08-01T22:30:01+00:00")
    assert ok is True
    assert "2026-08-02" in detail


def test_delivery_with_jobs_wrapper(tmp_path):
    # Alternative shape: {"jobs": [...]}
    job = {"name": "fam-nightly-report", "last_status": "ok",
           "last_run_at": "2026-08-02T03:00:12+00:00", "last_delivery_error": None}
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps({"jobs": [job]}), encoding="utf-8")
    ok, detail = diag.report_delivery_status(path, "fam-nightly-report", "2026-08-01T22:30:01+00:00")
    assert ok is True
    assert "2026-08-02" in detail


def test_delivery_not_ok_on_garbage_top_level(tmp_path):
    # Bare string, number, or other non-dict/list shape.
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps("garbage"), encoding="utf-8")
    ok, detail = diag.report_delivery_status(path, "fam-nightly-report", "2026-08-01T22:30:01+00:00")
    assert ok is False
    assert "unexpected shape" in detail


def test_delivery_naive_vs_aware_datetimes(tmp_path):
    # Naive last_run_at (no timezone) compared against aware previous_digest_at,
    # and vice versa. Neither may raise.
    job_naive = {"name": "fam-nightly-report", "last_status": "ok",
                 "last_run_at": "2026-08-02T03:00:12", "last_delivery_error": None}
    job_aware = {"name": "fam-nightly-report", "last_status": "ok",
                 "last_run_at": "2026-08-02T03:00:12+00:00", "last_delivery_error": None}
    path_naive = tmp_path / "jobs-naive.json"
    path_naive.write_text(json.dumps([job_naive]), encoding="utf-8")
    path_aware = tmp_path / "jobs-aware.json"
    path_aware.write_text(json.dumps([job_aware]), encoding="utf-8")

    # Naive job vs aware digest (previous_digest_at is aware by default from isoformat on aware datetime)
    ok, _ = diag.report_delivery_status(path_naive, "fam-nightly-report", "2026-08-01T22:30:01+00:00")
    assert ok is True, "naive last_run_at should compare successfully (treated as UTC)"

    # Aware job vs naive digest reference
    ok, _ = diag.report_delivery_status(path_aware, "fam-nightly-report", "2026-08-01T22:30:01")
    assert ok is True, "naive previous_digest_at should be treated as UTC"


def test_delivery_not_ok_on_unparseable_last_run_at(tmp_path):
    job = {"name": "fam-nightly-report", "last_status": "ok",
           "last_run_at": "not a valid timestamp", "last_delivery_error": None}
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps([job]), encoding="utf-8")
    ok, detail = diag.report_delivery_status(path, "fam-nightly-report", "2026-08-01T22:30:01+00:00")
    assert ok is False
    assert "unparseable" in detail


def test_delivery_not_ok_on_status_not_ok(tmp_path):
    job = {"name": "fam-nightly-report", "last_status": "failed",
           "last_run_at": "2026-08-02T03:00:12+00:00", "last_delivery_error": None}
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps([job]), encoding="utf-8")
    ok, detail = diag.report_delivery_status(path, "fam-nightly-report", "2026-08-01T22:30:01+00:00")
    assert ok is False
    assert "last_status" in detail
