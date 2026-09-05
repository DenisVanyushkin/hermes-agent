import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from fam import audit, cal, cli, extcal, gate, mail, meds, people, places, rem

def _future_start(hours=0):
    """A start comfortably in the future so cal-add's past guard never trips.
    Fixed-offset from real now keeps these mail/update tests deterministic in
    behaviour (always future) without rotting like a hardcoded date."""
    return (datetime.now(timezone.utc) + timedelta(days=30, hours=hours)).strftime("%Y-%m-%dT%H:%M:%S+00:00")

def test_json_flag_works_before_and_after_subcommand(db, capsys, monkeypatch):
    # db fixture sets FAM_DB to tmp DB; init writes to it
    assert cli.main(["--json", "init"]) == 0
    out1 = json.loads(capsys.readouterr().out)
    assert cli.main(["init", "--json"]) == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out1["ok"] and out2["ok"]

# --- Finding 2 (Important): exercise the new `people list` SUPPRESS call sites ---

def test_people_list_json_before_and_after_subcommand(db, capsys):
    people.add(db, "Тестова")
    db.commit()

    assert cli.main(["--json", "people", "list"]) == 0
    out1 = json.loads(capsys.readouterr().out)
    assert cli.main(["people", "list", "--json"]) == 0
    out2 = json.loads(capsys.readouterr().out)

    assert isinstance(out1, list) and any(p["name"] == "Тестова" for p in out1)
    assert isinstance(out2, list) and any(p["name"] == "Тестова" for p in out2)

# --- Finding 3 (Important): CLI-level ValueError contract check ---

def test_people_add_duplicate_cli_exit_code_2(db, capsys):
    assert cli.main(["people", "add", "X"]) == 0
    capsys.readouterr()  # discard first call's stdout/stderr
    rc = cli.main(["people", "add", "X"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""

# --- Fix round 1, Finding 3: cal show unknown id -> exit 2 ---

def test_cal_show_unknown_id_exit_2(db, capsys):
    rc = cli.main(["cal", "show", "999"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""

# --- Task 6: `fam cal grid` wiring ---

def test_cal_grid_month_json_writes_png(db, capsys, tmp_path):
    cal.add(db, "Врач", "2026-07-15T05:00:00+00:00"); db.commit()
    out_path = str(tmp_path / "july.png")
    rc = cli.main(["cal", "grid", "--month", "2026-07", "-o", out_path, "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out == {"ok": True, "path": out_path}
    import os
    assert os.path.getsize(out_path) > 5000

def test_cal_grid_week_writes_png(db, capsys, tmp_path):
    out_path = str(tmp_path / "week.png")
    rc = cli.main(["cal", "grid", "--week", "2026-07-13", "-o", out_path])
    captured = capsys.readouterr()
    assert rc == 0
    assert out_path in captured.out
    import os
    assert os.path.getsize(out_path) > 5000

def test_cal_grid_requires_exactly_one_of_month_or_week(db, tmp_path):
    out_path = str(tmp_path / "x.png")
    with pytest.raises(SystemExit) as exc_neither:
        cli.main(["cal", "grid", "-o", out_path])
    assert exc_neither.value.code == 2

    with pytest.raises(SystemExit) as exc_both:
        cli.main(["cal", "grid", "--month", "2026-07", "--week", "2026-07-13",
                   "-o", out_path])
    assert exc_both.value.code == 2

def test_cal_grid_bad_month_format_exit_2(db, tmp_path):
    out_path = str(tmp_path / "x.png")
    with pytest.raises(SystemExit) as exc:
        cli.main(["cal", "grid", "--month", "2026-7", "-o", out_path])
    assert exc.value.code == 2

def test_cal_grid_bad_week_format_exit_2(db, tmp_path):
    out_path = str(tmp_path / "x.png")
    with pytest.raises(SystemExit) as exc:
        cli.main(["cal", "grid", "--week", "2026-13-99", "-o", out_path])
    assert exc.value.code == 2

# --- day view: `fam cal grid --day` wiring ---

def test_cal_grid_day_writes_png(db, capsys, tmp_path):
    cal.add(db, "Врач", "2026-07-11T05:00:00+00:00"); db.commit()
    out_path = str(tmp_path / "day.png")
    rc = cli.main(["cal", "grid", "--day", "2026-07-11", "-o", out_path])
    captured = capsys.readouterr()
    assert rc == 0
    assert out_path in captured.out
    import os
    assert os.path.getsize(out_path) > 5000

def test_cal_grid_day_json_writes_png(db, capsys, tmp_path):
    out_path = str(tmp_path / "day.png")
    rc = cli.main(["cal", "grid", "--day", "2026-07-11", "-o", out_path, "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out == {"ok": True, "path": out_path}

def test_cal_grid_bad_day_format_exit_2(db, tmp_path):
    out_path = str(tmp_path / "x.png")
    with pytest.raises(SystemExit) as exc:
        cli.main(["cal", "grid", "--day", "2026-13-99", "-o", out_path])
    assert exc.value.code == 2

def test_cal_grid_requires_exactly_one_of_day_week_or_month(db, tmp_path):
    out_path = str(tmp_path / "x.png")

    # None of the three.
    with pytest.raises(SystemExit) as exc_none:
        cli.main(["cal", "grid", "-o", out_path])
    assert exc_none.value.code == 2

    # day + week.
    with pytest.raises(SystemExit) as exc_day_week:
        cli.main(["cal", "grid", "--day", "2026-07-11", "--week", "2026-07-13",
                   "-o", out_path])
    assert exc_day_week.value.code == 2

    # day + month.
    with pytest.raises(SystemExit) as exc_day_month:
        cli.main(["cal", "grid", "--day", "2026-07-11", "--month", "2026-07",
                   "-o", out_path])
    assert exc_day_month.value.code == 2

    # all three.
    with pytest.raises(SystemExit) as exc_all_three:
        cli.main(["cal", "grid", "--day", "2026-07-11", "--week", "2026-07-13",
                   "--month", "2026-07", "-o", out_path])
    assert exc_all_three.value.code == 2

# --- Final-review hardening: audit.query limit guard, CLI-level ---

def test_log_rejects_negative_limit_exit_2(db, capsys):
    rc = cli.main(["log", "--limit", "-1"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""

def test_log_rejects_zero_limit_exit_2(db, capsys):
    rc = cli.main(["log", "--limit", "0"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""

# --- Final-review hardening: naive-ISO datetimes rejected, CLI-level ---

def test_cal_add_naive_start_exit_2(db, capsys):
    rc = cli.main(["cal", "add", "--title", "X", "--start", "2026-07-15T10:00:00"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""

# --- Task 13: past-start guardrail (`cal add`/`cal update`), CLI-level ---

_PAST_HINT_RE = re.compile(
    r"start is in the past \(now: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+05:00\)\. "
    r"If the user means a past event, retry with --allow-past; otherwise "
    r"re-derive the date \(run date\)\."
)

def test_cal_add_start_in_past_exit_2(db, capsys):
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    rc = cli.main(["cal", "add", "--title", "X", "--start", past])
    captured = capsys.readouterr()
    assert rc == 2
    assert _PAST_HINT_RE.search(captured.err)

def test_cal_add_start_within_grace_period_succeeds(db, capsys):
    within = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="seconds")
    rc = cli.main(["cal", "add", "--title", "X", "--start", within, "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["title"] == "X"

def test_cal_add_start_just_past_grace_period_exit_2(db, capsys):
    just_past = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat(timespec="seconds")
    rc = cli.main(["cal", "add", "--title", "X", "--start", just_past])
    captured = capsys.readouterr()
    assert rc == 2
    assert _PAST_HINT_RE.search(captured.err)

def test_cal_add_allow_past_bypasses_guardrail(db, capsys):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    rc = cli.main(["cal", "add", "--title", "X", "--start", past, "--allow-past", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["title"] == "X"

def test_cal_add_rejected_past_start_writes_no_audit_or_event(db, capsys):
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    rc = cli.main(["cal", "add", "--title", "X", "--start", past])
    capsys.readouterr()
    assert rc == 2
    assert db.execute("SELECT COUNT(*) c FROM audit_log WHERE kind='cal.add'").fetchone()["c"] == 0
    assert db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"] == 0

def test_cal_add_end_before_start_now_rejected_by_overlap_guardrail(db, capsys):
    """Was test_cal_add_past_end_without_past_start_is_not_validated: end
    before start used to slip through untouched because
    _check_start_not_past only looks at --start. Since the 2026-08-01
    occupancy guardrail, cmd_cal_add always calls _check_no_overlap first,
    which always calls cal.overlaps() (Task 1) -- and that rejects
    end < start outright. So this nonsensical interval is now caught as a
    side effect, regardless of --allow-overlap: it is not an overlap, so
    there is nothing for that flag to override."""
    end_in_past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    rc = cli.main(["cal", "add", "--title", "X", "--start",
                   "2099-01-01T05:00:00+00:00", "--end", end_in_past, "--json"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "end is before start" in captured.err
    assert db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"] == 0

def test_cal_update_start_in_past_exit_2(db, capsys):
    e = cal.add(db, "Т", "2099-01-01T05:00:00+00:00")
    db.commit()
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    rc = cli.main(["cal", "update", str(e["id"]), "--start", past])
    captured = capsys.readouterr()
    assert rc == 2
    assert _PAST_HINT_RE.search(captured.err)

def test_cal_update_other_field_on_past_event_without_allow_past_succeeds(db, capsys):
    past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
    e = cal.add(db, "Т", past)  # domain call bypasses the CLI-only guardrail
    db.commit()
    rc = cli.main(["cal", "update", str(e["id"]), "--notes", "hello", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["notes"] == "hello"

def test_cal_update_allow_past_bypasses_guardrail(db, capsys):
    e = cal.add(db, "Т", "2099-01-01T05:00:00+00:00")
    db.commit()
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    rc = cli.main(["cal", "update", str(e["id"]), "--start", past, "--allow-past", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["start_utc"] == cal._to_utc_iso(past)

def test_cal_update_rejected_past_start_writes_no_audit(db, capsys):
    e = cal.add(db, "Т", "2099-01-01T05:00:00+00:00")
    db.commit()
    capsys.readouterr()
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    rc = cli.main(["cal", "update", str(e["id"]), "--start", past])
    capsys.readouterr()
    assert rc == 2
    rows = db.execute("SELECT COUNT(*) c FROM audit_log WHERE kind='cal.update'").fetchone()
    assert rows["c"] == 0

# --- Task 3: fam init also seeds default reminder rules ---

def test_init_seeds_default_rules(db, capsys):
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    rows = db.execute("SELECT scope FROM reminder_rules ORDER BY scope").fetchall()
    assert [r["scope"] for r in rows] == ["default", "slug:amina", "slug:taya"]

    # rerun: idempotent, no duplicates
    assert cli.main(["init"]) == 0
    rows2 = db.execute("SELECT scope FROM reminder_rules ORDER BY scope").fetchall()
    assert len(rows2) == 3

# --- Task 3: `fam cal add/update --travel-min` ---

def test_cal_add_travel_min_zero_overrides_place(db, capsys):
    pl = places.add(db, "Клиника")
    db.execute("UPDATE places SET travel_min=20 WHERE id=?", (pl["id"],))
    db.commit()

    rc = cli.main(["cal", "add", "--title", "Т", "--start",
                   "2099-01-01T05:00:00+00:00", "--place", "Клиника",
                   "--transport", "car", "--travel-min", "0", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["travel_min"] == 0

def test_cal_update_travel_min_flag(db, capsys):
    e = cal.add(db, "Т", "2099-01-01T05:00:00+00:00")
    db.commit()

    rc = cli.main(["cal", "update", str(e["id"]), "--travel-min", "15", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["travel_min"] == 15

# --- Task 3: `fam rem` subcommands ---

def _seed_rem(db):
    people.add(db, "Тая", slug="taya")
    rem.seed_default_rules(db)
    db.commit()

def test_rem_list_json_and_plain(db, capsys):
    _seed_rem(db)
    e = cal.add(db, "Событие", "2099-01-01T05:00:00+00:00")
    db.commit()

    assert cli.main(["rem", "list", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 4  # default (2c) = build_stages(30), 4 stages
    assert all(r["event_id"] == e["id"] for r in out)

    assert cli.main(["rem", "list"]) == 0
    text = capsys.readouterr().out
    assert str(e["id"]) in text

def test_rem_list_event_filter(db, capsys):
    _seed_rem(db)
    e1 = cal.add(db, "Раз", "2099-01-01T05:00:00+00:00")
    cal.add(db, "Два", "2099-01-02T05:00:00+00:00")
    db.commit()

    assert cli.main(["rem", "list", "--event", str(e1["id"]), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 4  # default (2c) = build_stages(30), 4 stages
    assert all(r["event_id"] == e1["id"] for r in out)

def test_rem_list_due_filter(db, capsys):
    _seed_rem(db)
    e = cal.add(db, "Событие", "2099-01-01T05:00:00+00:00")
    db.commit()
    row = db.execute(
        "SELECT id FROM reminders WHERE event_id=? ORDER BY fire_at_utc LIMIT 1",
        (e["id"],)).fetchone()
    db.execute("UPDATE reminders SET fire_at_utc='2000-01-01T00:00:00+00:00' "
               "WHERE id=?", (row["id"],))
    db.commit()

    assert cli.main(["rem", "list", "--due", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [r["id"] for r in out] == [row["id"]]

def test_rem_ack_json_and_exit_code(db, capsys):
    _seed_rem(db)
    e = cal.add(db, "Событие", "2099-01-01T05:00:00+00:00")
    db.commit()

    assert cli.main(["rem", "ack", str(e["id"]), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["acked"] == 4  # default (2c) = build_stages(30), 4 stages

    statuses = {r["status"] for r in db.execute(
        "SELECT status FROM reminders WHERE event_id=?", (e["id"],))}
    assert statuses == {"acked"}

def test_rem_ack_unknown_event_exit_2(db, capsys):
    rc = cli.main(["rem", "ack", "999"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""

def test_cli_rem_ack_scope_prepare(db, capsys):
    people.add(db, "Тая", slug="taya")
    rem.seed_default_rules(db)
    db.commit()
    e = cal.add(db, "С Таей", "2099-01-01T05:00:00+00:00", participants=["Тая"])
    db.commit()

    assert cli.main(["rem", "ack", str(e["id"]), "--scope", "prepare", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"event_id": e["id"], "acked": 3, "scope": "prepare"}

def test_rem_ack_unknown_event_scope_prepare_exit_2(db, capsys):
    rc = cli.main(["rem", "ack", "999", "--scope", "prepare"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""

def test_rem_cancel_json_and_exit_code(db, capsys):
    _seed_rem(db)
    e = cal.add(db, "Событие", "2099-01-01T05:00:00+00:00")
    db.commit()

    assert cli.main(["rem", "cancel", str(e["id"]), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["cancelled"] == 4  # default (2c) = build_stages(30), 4 stages

    statuses = {r["status"] for r in db.execute(
        "SELECT status FROM reminders WHERE event_id=?", (e["id"],))}
    assert statuses == {"cancelled"}

def test_rem_cancel_unknown_event_exit_2(db, capsys):
    rc = cli.main(["rem", "cancel", "999"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""

def test_rem_rules_json_lists_seeded_scopes(db, capsys):
    rem.seed_default_rules(db)
    db.commit()

    assert cli.main(["rem", "rules", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    scopes = {r["scope"] for r in out}
    assert scopes == {"default", "slug:taya", "slug:amina"}

def test_rem_rules_plain_output(db, capsys):
    rem.seed_default_rules(db)
    db.commit()

    assert cli.main(["rem", "rules"]) == 0
    text = capsys.readouterr().out
    assert "default" in text and "slug:taya" in text

# --- Task 11: `fam rem active` CLI wiring (reminder-reaction ack fix) ---

def test_rem_active_json_shape(db, capsys):
    _seed_rem(db)
    e = cal.add(db, "Событие", "2099-01-01T05:00:00+00:00")
    db.commit()

    assert cli.main(["rem", "active", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 1
    assert out[0]["event_id"] == e["id"]
    assert out[0]["title"] == "Событие"
    assert out[0]["pending_count"] == 4  # default (2c) = build_stages(30), 4 stages
    assert out[0]["sent_count"] == 0
    assert set(out[0]) == {"event_id", "title", "start_local",
                            "next_fire_local", "pending_count", "sent_count"}

def test_rem_active_plain_output(db, capsys):
    _seed_rem(db)
    e = cal.add(db, "Событие", "2099-01-01T05:00:00+00:00")
    db.commit()

    assert cli.main(["rem", "active"]) == 0
    text = capsys.readouterr().out
    assert str(e["id"]) in text and "Событие" in text

def test_rem_active_empty_when_nothing_pending(db, capsys):
    _seed_rem(db)

    assert cli.main(["rem", "active", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []

def test_rem_active_excludes_fully_acked_event(db, capsys):
    _seed_rem(db)
    e = cal.add(db, "Событие", "2099-01-01T05:00:00+00:00")
    db.commit()
    assert cli.main(["rem", "ack", str(e["id"]), "--json"]) == 0
    capsys.readouterr()

    assert cli.main(["rem", "active", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []

# --- Task 6: `fam tick reminders` CLI wiring ---
# gate.deliver is monkeypatched everywhere here -- these tests exercise
# the CLI's argument parsing/wiring/output-shape contract only, not
# gate's own subprocess pipeline (test_gate.py) or the tick orchestration
# logic itself (test_tick.py).

# fam tick reminders (cmd_tick_reminders) calls tick.reminders(conn,
# now_utc=args.now) with no cfg override, so an un-monkeypatched run falls
# through to gate.load_config()'s REAL CONFIG_PATH/CONFIG_EXAMPLE_PATH --
# i.e. the live /home/denis/.hermes/private/amina/fam-config.json on
# whatever machine runs the suite. Every test below must monkeypatch
# gate.CONFIG_PATH/CONFIG_EXAMPLE_PATH to tmp_path first (mirrors
# test_tick.py's test_reminders_loads_config_when_not_given) so the CLI
# suite can never write to that real path on a foreign machine.
_HERMETIC_CFG = {
    "target": "whatsapp:+77782110625",
    "quiet_start": "21:30",
    "quiet_end": "07:30",
    "daily_budget": 8,
    "gate_model": "gpt-5.4-mini",
    "gate_provider": "openai-codex",
    "max_len_reminder": 300,
    "max_len_digest": 900,
    "reminder_max_age_min": 120,
}

def _hermetic_gate_config(tmp_path, monkeypatch):
    example = tmp_path / "fam-config.example.json"
    example.write_text(json.dumps(_HERMETIC_CFG, ensure_ascii=False),
                        encoding="utf-8")
    target = tmp_path / "fam-config.json"
    monkeypatch.setattr(gate, "CONFIG_PATH", target)
    monkeypatch.setattr(gate, "CONFIG_EXAMPLE_PATH", example)

def _due_reminder(db, event_id, fire_at=None):
    if fire_at is None:
        # Fresh relative to the real wall clock -- Fix 1's stale-age guard
        # would otherwise cancel an old fixed timestamp before delivery.
        fire_at = (datetime.now(timezone.utc) - timedelta(minutes=10)
                   ).isoformat(timespec="seconds")
    cur = db.execute(
        "INSERT INTO reminders(event_id, label, anchor, fire_at_utc, "
        "status, created_at) VALUES (?,?,?,?,?,?)",
        (event_id, "проверка", "start", fire_at, "pending",
         "2000-01-01T00:00:00+00:00"),
    )
    return cur.lastrowid

def test_tick_reminders_json_shape(db, capsys, monkeypatch, tmp_path):
    _hermetic_gate_config(tmp_path, monkeypatch)
    e = cal.add(db, "Событие", "2099-01-01T05:00:00+00:00")
    db.commit()
    _due_reminder(db, e["id"])
    db.commit()
    monkeypatch.setattr(gate, "deliver", lambda *a, **k: "sent")

    rc = cli.main(["tick", "reminders", "--json"])
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert out == {"due": 1, "sent": 1, "quiet": 0, "budget": 0,
                    "error": 0, "cancelled": 0, "stale": 0,
                    "error_capped": 0, "road_recomputed": 0}

def test_tick_reminders_now_override(db, capsys, monkeypatch, tmp_path):
    _hermetic_gate_config(tmp_path, monkeypatch)
    e = cal.add(db, "Событие", "2099-01-01T05:00:00+00:00")
    db.commit()
    # fire_at is in the future relative to the real wall clock but in the
    # past relative to the --now override below -- pins that --now, not
    # real time, drives due-selection. Kept within reminder_max_age_min
    # (120 min default) of the override so Fix 1's stale-age guard doesn't
    # cancel it before delivery.
    _due_reminder(db, e["id"], fire_at="2030-06-01T23:50:00+00:00")
    db.commit()
    monkeypatch.setattr(gate, "deliver", lambda *a, **k: "sent")

    rc = cli.main(["tick", "reminders", "--now", "2030-06-02T00:00:00+00:00",
                   "--json"])
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert out["due"] == 1
    assert out["sent"] == 1

def test_tick_reminders_plain_output(db, capsys, monkeypatch, tmp_path):
    _hermetic_gate_config(tmp_path, monkeypatch)
    monkeypatch.setattr(gate, "deliver", lambda *a, **k: "sent")

    rc = cli.main(["tick", "reminders"])
    text = capsys.readouterr().out

    assert rc == 0
    assert "due=0" in text and "sent=0" in text and "cancelled=0" in text

# --- Task 7: `fam tick digest` CLI wiring ---
# Same hermetic-config discipline as the reminders section above, plus
# weather.fetch_almaty is also monkeypatched -- tick.digest() has no
# --fetch-weather CLI override, so an un-mocked run would hit the real
# Open-Meteo network from this test suite.

def _hermetic_weather(monkeypatch, wx=None):
    from fam import weather
    monkeypatch.setattr(weather, "fetch_almaty", lambda: wx)

def _must_not_be_called(*a, **k):
    raise AssertionError("gate.deliver must not be called")

def test_tick_digest_json_shape(db, capsys, monkeypatch, tmp_path):
    _hermetic_gate_config(tmp_path, monkeypatch)
    _hermetic_weather(monkeypatch)
    monkeypatch.setattr(gate, "deliver", lambda *a, **k: "sent")

    rc = cli.main(["tick", "digest", "--now", "2026-07-20T04:30:00+00:00",
                   "--json"])
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert out == {"status": "sent", "date_local": "2026-07-20",
                    "weather_present": False, "n_events": 0}

def test_tick_digest_now_override_drives_date_local(db, capsys, monkeypatch, tmp_path):
    _hermetic_gate_config(tmp_path, monkeypatch)
    _hermetic_weather(monkeypatch)
    monkeypatch.setattr(gate, "deliver", lambda *a, **k: "sent")

    rc = cli.main(["tick", "digest", "--now", "2030-06-02T00:00:00+00:00",
                   "--json"])
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert out["date_local"] == "2030-06-02"

def test_tick_digest_plain_output(db, capsys, monkeypatch, tmp_path):
    _hermetic_gate_config(tmp_path, monkeypatch)
    _hermetic_weather(monkeypatch)
    monkeypatch.setattr(gate, "deliver", lambda *a, **k: "sent")

    rc = cli.main(["tick", "digest", "--now", "2026-07-20T04:30:00+00:00"])
    text = capsys.readouterr().out

    assert rc == 0
    assert "status=sent" in text and "date_local=2026-07-20" in text

def test_tick_digest_skips_when_already_sent_today(db, capsys, monkeypatch, tmp_path):
    _hermetic_gate_config(tmp_path, monkeypatch)
    _hermetic_weather(monkeypatch)
    now = "2026-07-20T04:30:00+00:00"
    # Fix 3 (tick.py, digest wall-clock dup-guard decoupling): the guard's
    # day window is always anchored to the REAL wall clock -- audit.log()
    # stamps every row's ts_utc from datetime.now() regardless of any
    # --now override -- so the existing gate.sent row it must see here
    # has to be real-clock-stamped too, exactly as gate.deliver would
    # actually write it in production. A fake --now-aligned timestamp
    # (the old fixed "2026-07-20T02:00:00+00:00") would no longer be
    # inside the guard's real-clock window and the skip would never fire.
    real_now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.execute(
        "INSERT INTO audit_log(ts_utc, kind, actor, payload) VALUES(?,?,?,?)",
        (real_now, "gate.sent", "test", json.dumps({"kind": "digest"})),
    )
    db.commit()
    monkeypatch.setattr(gate, "deliver", _must_not_be_called)

    rc = cli.main(["tick", "digest", "--now", now, "--json"])
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert out == {"skipped": "already_sent", "date_local": "2026-07-20"}

# --- Task 10: mail hook on `cal add`/`cal update` (participant slug=="denis") ---
# Same hermetic-config discipline as the tick sections above --
# _hermetic_gate_config default-merges gate.CONFIG_DEFAULTS's email_enabled/
# email_from/email_to (see gate.py) into the tmp live config, so these
# tests get email_enabled=True for free without listing those keys in
# _HERMETIC_CFG. cli.mail.send_event_email is always monkeypatched --
# these tests exercise the CLI hook's wiring/conditions/audit contract
# only, never a real Gmail call (that's test_mail.py's job).

def _seed_denis(db):
    people.add(db, "Денис", slug="denis")
    db.commit()

def test_cal_add_with_denis_participant_sends_mail_and_audits(db, capsys, monkeypatch, tmp_path):
    _hermetic_gate_config(tmp_path, monkeypatch)
    _seed_denis(db)
    calls = []
    def fake_send(event, cfg, **kwargs):
        calls.append(event["id"])
        return {"ok": True, "id": "msg-1"}
    monkeypatch.setattr(cli.mail, "send_event_email", fake_send)

    rc = cli.main(["cal", "add", "--title", "Событие", "--start",
                    _future_start(), "--with", "Денис", "--json"])
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert calls == [out["id"]]
    rows = audit.query(db, since_utc=None, kind_prefix="mail.", grep=None, limit=10)
    sent = [r for r in rows if r["kind"] == "mail.sent"]
    assert len(sent) == 1
    assert sent[0]["payload"] == {"event_id": out["id"], "to": "hermes@vanyushk.in"}

def test_cal_add_without_denis_participant_does_not_send_mail(db, capsys, monkeypatch, tmp_path):
    _hermetic_gate_config(tmp_path, monkeypatch)
    people.add(db, "Тая", slug="taya"); db.commit()
    calls = []
    monkeypatch.setattr(cli.mail, "send_event_email", lambda *a, **k: calls.append(1))

    rc = cli.main(["cal", "add", "--title", "Событие", "--start",
                    _future_start(), "--with", "Тая"])

    assert rc == 0
    assert calls == []

def test_cal_add_denis_participant_but_email_disabled_does_not_send(db, capsys, monkeypatch, tmp_path):
    # Explicit email_enabled: false on disk overrides CONFIG_DEFAULTS's true.
    example = tmp_path / "fam-config.example.json"
    example.write_text(json.dumps(_HERMETIC_CFG, ensure_ascii=False), encoding="utf-8")
    target = tmp_path / "fam-config.json"
    target.write_text(
        json.dumps(dict(_HERMETIC_CFG, email_enabled=False,
                         email_from="germes@vanyushk.in",
                         email_to="hermes@vanyushk.in"),
                    ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "CONFIG_PATH", target)
    monkeypatch.setattr(gate, "CONFIG_EXAMPLE_PATH", example)
    _seed_denis(db)
    calls = []
    monkeypatch.setattr(cli.mail, "send_event_email", lambda *a, **k: calls.append(1))

    rc = cli.main(["cal", "add", "--title", "Событие", "--start",
                    _future_start(), "--with", "Денис"])

    assert rc == 0
    assert calls == []

def test_cal_add_mail_hook_config_load_failure_does_not_fail_cal_op(db, capsys, monkeypatch, tmp_path):
    # Fix round 1 hardening: _maybe_email_event's whole body (config
    # load, send, audit) is wrapped in try/except -- a raise from
    # gate.load_config() itself (e.g. a corrupt live config file) must
    # not propagate past the CLI operation, which has already committed
    # the calendar write by the time this hook runs.
    _seed_denis(db)
    monkeypatch.setattr(
        cli.gate, "load_config",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("corrupt config")),
    )

    rc = cli.main(["cal", "add", "--title", "Событие", "--start",
                    _future_start(), "--with", "Денис", "--json"])
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert cal.get(db, out["id"]) is not None  # event persisted regardless

    rows = audit.query(db, since_utc=None, kind_prefix="mail.", grep=None, limit=10)
    errors = [r for r in rows if r["kind"] == "mail.error"]
    assert len(errors) == 1
    assert errors[0]["payload"]["event_id"] == out["id"]
    assert "corrupt config" in errors[0]["payload"]["error"]

def test_cal_add_mail_failure_audits_error_and_does_not_fail_cal_op(db, capsys, monkeypatch, tmp_path):
    _hermetic_gate_config(tmp_path, monkeypatch)
    _seed_denis(db)
    monkeypatch.setattr(
        cli.mail, "send_event_email",
        lambda *a, **k: {"ok": False, "error": "unauthorized_client"},
    )

    rc = cli.main(["cal", "add", "--title", "Событие", "--start",
                    _future_start(), "--with", "Денис", "--json"])
    out = json.loads(capsys.readouterr().out)

    # The cal operation itself must succeed regardless of the mail failure.
    assert rc == 0
    rows = audit.query(db, since_utc=None, kind_prefix="mail.", grep=None, limit=10)
    errors = [r for r in rows if r["kind"] == "mail.error"]
    assert len(errors) == 1
    assert errors[0]["payload"] == {"event_id": out["id"], "error": "unauthorized_client"}

def test_cal_update_add_person_denis_triggers_mail(db, capsys, monkeypatch, tmp_path):
    _hermetic_gate_config(tmp_path, monkeypatch)
    _seed_denis(db)
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00")
    db.commit()
    calls = []
    def fake_send(event, cfg, **kwargs):
        calls.append(event["id"])
        return {"ok": True, "id": "m1"}
    monkeypatch.setattr(cli.mail, "send_event_email", fake_send)

    rc = cli.main(["cal", "update", str(e["id"]), "--add-person", "Денис"])

    assert rc == 0
    assert calls == [e["id"]]

def test_cal_update_without_denis_does_not_trigger_mail(db, capsys, monkeypatch, tmp_path):
    _hermetic_gate_config(tmp_path, monkeypatch)
    people.add(db, "Тая", slug="taya"); db.commit()
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00")
    db.commit()
    calls = []
    monkeypatch.setattr(cli.mail, "send_event_email", lambda *a, **k: calls.append(1))

    rc = cli.main(["cal", "update", str(e["id"]), "--add-person", "Тая"])

    assert rc == 0
    assert calls == []

# --- Fix round 1: cal-update mail hook fires only on a MATERIAL change ---
# (title/start_utc/end_utc/place/participants/travel_min -- title added
# by product decision, phase-2b final review Minor #7) -- see cal.py's
# _MAIL_TRIGGER_COLUMNS/update()'s "_material_changed" signal, which
# cmd_cal_update consults instead of re-deriving old-vs-new itself.

def test_cal_update_notes_only_on_denis_event_does_not_resend_mail(db, capsys, monkeypatch, tmp_path):
    _hermetic_gate_config(tmp_path, monkeypatch)
    _seed_denis(db)
    calls = []
    def fake_send(event, cfg, **kwargs):
        calls.append(event["id"])
        return {"ok": True, "id": "m1"}
    monkeypatch.setattr(cli.mail, "send_event_email", fake_send)

    rc_add = cli.main(["cal", "add", "--title", "Событие", "--start",
                        _future_start(), "--with", "Денис", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc_add == 0
    assert len(calls) == 1  # the add fired the hook once, unconditionally

    rc_update = cli.main(["cal", "update", str(out["id"]), "--notes", "просто заметка"])
    assert rc_update == 0

    # A notes-only update is not material -- call count must stay at 1.
    assert len(calls) == 1

def test_cal_update_start_utc_on_denis_event_resends_mail(db, capsys, monkeypatch, tmp_path):
    _hermetic_gate_config(tmp_path, monkeypatch)
    _seed_denis(db)
    calls = []
    def fake_send(event, cfg, **kwargs):
        calls.append(event["id"])
        return {"ok": True, "id": "m1"}
    monkeypatch.setattr(cli.mail, "send_event_email", fake_send)

    rc_add = cli.main(["cal", "add", "--title", "Событие", "--start",
                        _future_start(), "--with", "Денис", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc_add == 0
    assert len(calls) == 1

    rc_update = cli.main(["cal", "update", str(out["id"]), "--start",
                           _future_start(hours=1)])
    assert rc_update == 0

    # start_utc IS material -- the hook must fire again.
    assert len(calls) == 2

def test_cal_update_title_only_on_denis_event_resends_mail(db, capsys, monkeypatch, tmp_path):
    # Product decision (Denis, phase-2b final review Minor #7): a
    # title-only rename IS material -- the .ics's stable UID means the
    # admin's calendar entry updates its SUMMARY on the re-sent email.
    _hermetic_gate_config(tmp_path, monkeypatch)
    _seed_denis(db)
    calls = []
    def fake_send(event, cfg, **kwargs):
        calls.append(event["id"])
        return {"ok": True, "id": "m1"}
    monkeypatch.setattr(cli.mail, "send_event_email", fake_send)

    rc_add = cli.main(["cal", "add", "--title", "Событие", "--start",
                        _future_start(), "--with", "Денис", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc_add == 0
    assert len(calls) == 1

    rc_update = cli.main(["cal", "update", str(out["id"]), "--title",
                           "Новое название"])
    assert rc_update == 0
    assert len(calls) == 2

def test_cal_update_json_output_does_not_leak_material_changed_key(db, capsys, monkeypatch, tmp_path):
    # "_material_changed" is an internal signal between cal.update() and
    # cli.py's hook -- it must never appear in `fam cal update --json`'s
    # public output.
    _hermetic_gate_config(tmp_path, monkeypatch)
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00")
    db.commit()
    monkeypatch.setattr(cli.mail, "send_event_email", lambda *a, **k: {"ok": True, "id": "m1"})

    rc = cli.main(["cal", "update", str(e["id"]), "--notes", "x", "--json"])
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert "_material_changed" not in out

# --- `fam mail test EVENT_ID` (manual live-trigger command, T11) ---

def test_mail_test_command_success(db, capsys, monkeypatch, tmp_path):
    _hermetic_gate_config(tmp_path, monkeypatch)
    _seed_denis(db)
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00", participants=["Денис"])
    db.commit()
    monkeypatch.setattr(
        cli.mail, "send_event_email",
        lambda event, cfg, **k: {"ok": True, "id": "msg-42"},
    )

    rc = cli.main(["mail", "test", str(e["id"]), "--json"])
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert out == {"ok": True, "id": "msg-42"}
    rows = audit.query(db, since_utc=None, kind_prefix="mail.", grep=None, limit=10)
    assert any(r["kind"] == "mail.sent" and r["payload"]["event_id"] == e["id"] for r in rows)

def test_mail_test_command_failure_reports_error_and_audits(db, capsys, monkeypatch, tmp_path):
    _hermetic_gate_config(tmp_path, monkeypatch)
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00")
    db.commit()
    monkeypatch.setattr(
        cli.mail, "send_event_email",
        lambda event, cfg, **k: {"ok": False, "error": "access_denied"},
    )

    rc = cli.main(["mail", "test", str(e["id"]), "--json"])
    out = json.loads(capsys.readouterr().out)

    # `mail test` is a diagnostic command -- it reports a send failure in
    # its own JSON/audit output rather than treating it as a CLI error.
    assert rc == 0
    assert out == {"ok": False, "error": "access_denied"}
    rows = audit.query(db, since_utc=None, kind_prefix="mail.", grep=None, limit=10)
    assert any(r["kind"] == "mail.error" for r in rows)

def test_mail_test_unknown_event_exit_2(db, tmp_path, monkeypatch):
    _hermetic_gate_config(tmp_path, monkeypatch)
    rc = cli.main(["mail", "test", "999"])
    assert rc == 2

def test_mail_test_plain_output(db, capsys, monkeypatch, tmp_path):
    _hermetic_gate_config(tmp_path, monkeypatch)
    e = cal.add(db, "Событие", "2026-07-15T05:00:00+00:00")
    db.commit()
    monkeypatch.setattr(
        cli.mail, "send_event_email",
        lambda event, cfg, **k: {"ok": True, "id": "msg-9"},
    )

    rc = cli.main(["mail", "test", str(e["id"])])
    text = capsys.readouterr().out

    assert rc == 0
    assert "msg-9" in text

# --- Task 5 (3a): `fam road` + `fam places update` ---

ROAD_CFG_T5 = {
    "road_home_lat": 43.2220, "road_home_lon": 76.8512,
    "road_coef": 1.4, "road_speed_kmh": 30, "road_daily_cap": 100,
    "road_timeout_sec": 10,
}


def test_road_unknown_event_exit_2(db, capsys):
    rc = cli.main(["road", "999"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "unknown event" in captured.err


def test_road_coordless_event_source_none_exit_0(db, capsys):
    e = cal.add(db, "Без места", "2099-01-02T05:00:00+00:00")
    db.commit()
    rc = cli.main(["road", str(e["id"]), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out == {"event_id": e["id"], "travel_min_road": None,
                   "source": "none", "reason": "no_place_coords"}


def test_road_no_home_config_reason(db, capsys, monkeypatch):
    monkeypatch.setattr(cal.gate, "load_config",
                        lambda: {"road_home_lat": None, "road_home_lon": None})
    places.add(db, "Мега", lat=43.2298, lon=76.8823)
    db.commit()
    e = cal.add(db, "Кино", "2099-01-02T06:00:00+00:00", place="Мега")
    db.commit()
    rc = cli.main(["road", str(e["id"]), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["reason"] == "no_home_config"
    assert out["source"] == "none" and out["travel_min_road"] is None


def test_road_fallback_source_reason(db, capsys, monkeypatch):
    monkeypatch.setattr(cal.gate, "load_config", lambda: ROAD_CFG_T5)
    monkeypatch.setattr(cal.road, "compute_travel_min",
                        lambda conn, event, cfg, now_utc=None, **kw: (40, "manual"))
    places.add(db, "Мега", lat=43.2298, lon=76.8823)
    db.commit()
    e = cal.add(db, "Кино", "2099-01-02T06:00:00+00:00", place="Мега")
    db.commit()
    rc = cli.main(["road", str(e["id"]), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["reason"] == "fallback_source:manual"


def test_road_error_reason(db, capsys, monkeypatch):
    def boom(conn, event, cfg, now_utc=None, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(cal.gate, "load_config", lambda: ROAD_CFG_T5)
    places.add(db, "Мега", lat=43.2298, lon=76.8823)
    db.commit()
    monkeypatch.setattr(cal.road, "compute_travel_min",
                        lambda conn, event, cfg, now_utc=None, **kw: (26, "tomtom"))
    e = cal.add(db, "Кино", "2099-01-02T06:00:00+00:00", place="Мега")
    db.commit()
    monkeypatch.setattr(cal.road, "compute_travel_min", boom)
    rc = cli.main(["road", str(e["id"]), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["reason"] == "error"
    rows = audit.query(db, None, "road.hook_error", None)
    assert rows


def test_road_computes_writes_and_regenerates(db, capsys, monkeypatch):
    rem.seed_default_rules(db)
    rem.migrate_rules_2c(db)
    monkeypatch.setattr(cal.gate, "load_config", lambda: ROAD_CFG_T5)
    monkeypatch.setattr(cal.road, "compute_travel_min",
                        lambda conn, event, cfg, now_utc=None, **kw: (26, "tomtom"))
    places.add(db, "Мега", lat=43.2298, lon=76.8823)
    db.commit()
    e = cal.add(db, "Кино", "2099-01-02T06:00:00+00:00", place="Мега")
    db.commit()

    # a fresh recompute returns a different figure -- the command must
    # persist it AND move the chain (leave_at shifts 26 -> 31 min).
    monkeypatch.setattr(cal.road, "compute_travel_min",
                        lambda conn, event, cfg, now_utc=None, **kw: (31, "tomtom"))
    rc = cli.main(["--json", "road", str(e["id"])])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out == {"event_id": e["id"], "travel_min_road": 31,
                   "source": "tomtom",
                   "leave_at_local": "2099-01-02T10:29:00+05:00"}

    row = db.execute("SELECT travel_min_road, road_checked_at FROM events "
                     "WHERE id=?", (e["id"],)).fetchone()
    assert row["travel_min_road"] == 31
    assert row["road_checked_at"] is not None

    stage = db.execute(
        "SELECT fire_at_utc FROM reminders WHERE event_id=? AND status='pending' "
        "AND anchor='leave_at' AND label='пора выходить'", (e["id"],)).fetchone()
    assert stage["fire_at_utc"] == "2099-01-02T05:29:00+00:00"


def test_places_update_cli_json_before_and_after_subcommand(db, capsys):
    places.add(db, "Мега")
    db.commit()
    assert cli.main(["--json", "places", "update", "Мега",
                     "--travel-min", "40"]) == 0
    out1 = json.loads(capsys.readouterr().out)
    assert out1["travel_min"] == 40
    assert cli.main(["places", "update", "Мега", "--notes", "парковка P2",
                     "--json"]) == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out2["notes"] == "парковка P2"


def test_places_update_unknown_place_exit_2(db, capsys):
    rc = cli.main(["places", "update", "НетТакого", "--travel-min", "5"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""


def test_places_update_no_fields_exit_2(db, capsys):
    places.add(db, "Мега")
    db.commit()
    rc = cli.main(["places", "update", "Мега"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""


# --- transport-required guardrail for trips (events with a place) ---
# A place-bound event is a trip: its car/warmup departure hooks and road/
# leave_at math depend on knowing HOW Amina gets there. Leaving transport
# 'unknown' silently disables those hooks (the 15.07 "posyolok" incident:
# fuel at 16%%, flag set, yet no "zapravsya" because transport was unknown).
# So a place-bound add/series with no concrete transport is rejected at the
# CLI layer, forcing the skill to set it (asking Amina if unclear). Placeless
# events (calls, birthdays) never trip hooks -> unknown stays allowed there.
_TRANSPORT_HINT = "--transport car|walk|public"

def test_cal_add_place_without_transport_exit_2(db, capsys):
    places.add(db, "Поселок"); db.commit()
    rc = cli.main(["cal", "add", "--title", "Поездка", "--start",
                   _future_start(), "--place", "Поселок"])
    captured = capsys.readouterr()
    assert rc == 2
    assert _TRANSPORT_HINT in captured.err
    assert db.execute("SELECT COUNT(*) c FROM events").fetchone()["c"] == 0

def test_cal_add_place_explicit_unknown_transport_exit_2(db, capsys):
    places.add(db, "Поселок"); db.commit()
    rc = cli.main(["cal", "add", "--title", "Поездка", "--start",
                   _future_start(), "--place", "Поселок",
                   "--transport", "unknown"])
    captured = capsys.readouterr()
    assert rc == 2
    assert _TRANSPORT_HINT in captured.err

def test_cal_add_place_with_transport_succeeds(db, capsys):
    places.add(db, "Поселок"); db.commit()
    rc = cli.main(["cal", "add", "--title", "Поездка", "--start",
                   _future_start(), "--place", "Поселок",
                   "--transport", "car", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["transport"] == "car"

def test_cal_add_no_place_allows_unknown_transport(db, capsys):
    rc = cli.main(["cal", "add", "--title", "Созвон", "--start",
                   _future_start(), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["title"] == "Созвон"

# --- Task 2 (meds-defer): `fam med defer <id> --until` ---

_ALMATY = timezone(timedelta(hours=5))

def _future_hhmm_almaty(hours=2):
    """A HH:MM comfortably in the future by Almaty wall-clock, but never
    crossing local midnight -- meds.defer() rejects `until` at/after
    today's midnight, so a naive now+2h could flap right before 22:00
    Almaty. Clamp to 23:59 instead of rolling into tomorrow."""
    now = datetime.now(_ALMATY)
    target = now + timedelta(hours=hours)
    if target.date() != now.date():
        target = now.replace(hour=23, minute=59, second=0, microsecond=0)
        if target <= now:
            target = now + timedelta(minutes=1)
    return target.strftime("%H:%M")

def _mk_pending_intake(db, name="мисол", plan="2026-07-24T04:00:00Z"):
    med_id = meds.add(db, name, ["09:00"], remaining=10, threshold=1)
    cur = db.execute(
        "INSERT INTO med_intakes (med_id, plan_ts_utc, status, "
        "series_next_utc, created_at) VALUES (?,?,?,?,?)",
        (med_id, plan, "pending", plan, "2026-07-24T04:00:00Z"),
    )
    db.commit()
    return cur.lastrowid

def test_med_defer_hhmm_sets_series_next(db, capsys):
    iid = _mk_pending_intake(db)
    until = _future_hhmm_almaty()
    rc = cli.main(["med", "defer", str(iid), "--until", until, "--json"])
    captured = capsys.readouterr()
    assert rc == 0
    out = json.loads(captured.out)
    assert out["intake_id"] == iid
    assert out["until_local"] == until

    row = db.execute(
        "SELECT status, series_next_utc FROM med_intakes WHERE id=?", (iid,)
    ).fetchone()
    assert row["status"] == "pending"
    assert row["series_next_utc"] != "2026-07-24T04:00:00Z"

def test_med_defer_bad_time_exits_2(db, capsys):
    iid = _mk_pending_intake(db)
    rc = cli.main(["med", "defer", str(iid), "--until", "25:99"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""

def test_med_defer_unknown_intake_exits_2(db, capsys):
    rc = cli.main(["med", "defer", "999999", "--until", "23:00"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""

# --- Task 9 (extcal): `fam cal adopt` / `fam cal disown` --------------------
# Only `extcal._request` is monkeypatched in this section (the seam the task
# 9 brief names explicitly) -- these tests exercise the real
# `extcal.drop_valarm`/`_strip_valarm_ics`/`_export_put`/`_export_reread_etag`
# code (including the host-guard-adjacent header building and the 412-retry
# logic), never the real network.

def _iphone_event(db, title="Йога", start=None,
                   href="https://caldav.icloud.com/1/calendars/personal/evt1.ics",
                   etag='"e0"'):
    """An owner='iphone' event with external_href/external_etag attached --
    mirrors what a real import (extcal._apply_event_insert) leaves behind:
    cal.add() defaults to owner='hermes' and builds a transient chain, the
    raw UPDATE flips owner and attaches external_*, and the SECOND
    rem.regenerate() call retracts that transient chain (the exact two
    steps _apply_event_insert itself performs -- see extcal.py's own
    docstring there) -- so this fixture starts genuinely reminder-free,
    like a freshly-imported row, not an artifact of skipping that step.

    Seeds the default reminder rule first (rem.seed_default_rules) -- the
    plain `db` fixture does not (only `fam init`/cmd_init does), and
    without it applicable_rules() has nothing to build a chain from
    regardless of owner (same convention as this file's own `_seed_rem`
    helper for the Task 3 `fam rem` tests)."""
    rem.seed_default_rules(db)
    start = start or _future_start(hours=5)
    e = cal.add(db, title, start)
    db.execute(
        "UPDATE events SET owner='iphone', external_uid='uid-adopt-test', "
        "external_href=?, external_etag=? WHERE id=?",
        (href, etag, e["id"]))
    rem.regenerate(db, e["id"])
    db.commit()
    return cal.get(db, e["id"])

def _ok_valarm_response(*a, **kw):
    # A minimal but STRUCTURALLY COMPLETE resource (matched BEGIN/END,
    # including END:VEVENT/END:VCALENDAR) -- fix-round 1's integrity
    # check in `_strip_valarm_ics` (finding I3) refuses anything less,
    # so this fixture must satisfy it same as a real GET response would.
    return extcal.Response(
        200,
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n",
        {},
    )

def test_cal_adopt_flips_owner_and_builds_chain(db, capsys, monkeypatch):
    e = _iphone_event(db)
    assert e["owner"] == "iphone"
    assert rem.list_reminders(db, event_id=e["id"]) == []
    monkeypatch.setattr(extcal, "_request", _ok_valarm_response)

    rc = cli.main(["cal", "adopt", str(e["id"]), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["owner"] == "hermes"
    assert out["reminders_created"] > 0
    assert out["valarm_dropped"] is True

    updated = cal.get(db, e["id"])
    assert updated["owner"] == "hermes"
    assert len(rem.list_reminders(db, event_id=e["id"])) == out["reminders_created"]

    rows = audit.query(db, since_utc=None, kind_prefix="cal.adopt", grep=None, limit=10)
    assert len(rows) == 1
    assert rows[0]["payload"]["valarm_dropped"] is True

def test_cal_disown_reverts_owner_and_clears_chain(db, capsys, monkeypatch):
    e = _iphone_event(db)
    monkeypatch.setattr(extcal, "_request", _ok_valarm_response)
    assert cli.main(["cal", "adopt", str(e["id"])]) == 0
    capsys.readouterr()
    assert len(rem.list_reminders(db, event_id=e["id"])) > 0

    rc = cli.main(["cal", "disown", str(e["id"]), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["owner"] == "iphone"

    updated = cal.get(db, e["id"])
    assert updated["owner"] == "iphone"
    assert rem.list_reminders(db, event_id=e["id"]) == []

    rows = audit.query(db, since_utc=None, kind_prefix="cal.disown", grep=None, limit=10)
    assert len(rows) == 1
    assert rows[0]["payload"]["id"] == e["id"]

def test_cal_adopt_already_hermes_exit_2(db, capsys):
    e = cal.add(db, "Обычное", _future_start())
    db.commit()
    assert e["owner"] == "hermes"
    rc = cli.main(["cal", "adopt", str(e["id"])])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""

def test_cal_disown_already_iphone_exit_2(db, capsys):
    e = _iphone_event(db)
    rc = cli.main(["cal", "disown", str(e["id"])])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""

def test_cal_adopt_unknown_id_exit_2(db, capsys):
    rc = cli.main(["cal", "adopt", "999999"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""

def test_cal_disown_unknown_id_exit_2(db, capsys):
    rc = cli.main(["cal", "disown", "999999"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""

def test_cal_adopt_valarm_strip_put_has_if_match_and_preserves_other_fields(db, capsys, monkeypatch):
    original_ics = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
        "UID:her-uid-1\r\nSUMMARY:Йога\r\nDTSTART:20370720T130000Z\r\n"
        "BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT15M\r\nEND:VALARM\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    calls = []
    def fake_request(method, url, headers=None, body=None, timeout=None):
        calls.append((method, url, dict(headers or {}), body))
        if method == "GET":
            return extcal.Response(200, original_ics, {})
        if method == "PUT":
            return extcal.Response(204, b"", {"ETag": '"e1"'})
        raise AssertionError(f"unexpected method: {method}")
    monkeypatch.setattr(extcal, "_request", fake_request)

    href = "https://caldav.icloud.com/1/calendars/personal/evt1.ics"
    e = _iphone_event(db, href=href, etag='"e0"')

    rc = cli.main(["cal", "adopt", str(e["id"])])
    assert rc == 0

    methods = [c[0] for c in calls]
    assert methods == ["GET", "PUT"]
    _method, put_url, put_headers, put_body = calls[1]
    assert put_url == href
    assert put_headers.get("If-Match") == '"e0"'
    assert "VALARM" not in put_body
    assert "UID:her-uid-1" in put_body
    assert "SUMMARY:Йога" in put_body
    assert "DTSTART:20370720T130000Z" in put_body

def test_cal_adopt_valarm_strip_412_reread_and_retry_once(db, capsys, monkeypatch):
    ics = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:her-uid-1\r\n"
           "BEGIN:VALARM\r\nACTION:DISPLAY\r\nEND:VALARM\r\n"
           "END:VEVENT\r\nEND:VCALENDAR\r\n")
    calls = []
    def fake_request(method, url, headers=None, body=None, timeout=None):
        calls.append(method)
        if method == "GET":
            return extcal.Response(200, ics, {"ETag": '"fresh"'})
        if method == "PUT" and calls.count("PUT") == 1:
            return extcal.Response(412, b"", {})
        if method == "PUT" and calls.count("PUT") == 2:
            return extcal.Response(204, b"", {"ETag": '"final"'})
        raise AssertionError(f"unexpected extra call: {calls}")
    monkeypatch.setattr(extcal, "_request", fake_request)

    e = _iphone_event(db, etag='"stale"')
    rc = cli.main(["cal", "adopt", str(e["id"]), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["valarm_dropped"] is True
    assert calls == ["GET", "PUT", "GET", "PUT"]  # exactly one retry, never a second

def test_cal_adopt_valarm_strip_failure_does_not_undo_adoption(db, capsys, monkeypatch):
    def fake_request(method, url, headers=None, body=None, timeout=None):
        return None  # total transport failure (timeout/DNS/host-guard)
    monkeypatch.setattr(extcal, "_request", fake_request)

    e = _iphone_event(db)
    rc = cli.main(["cal", "adopt", str(e["id"]), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["owner"] == "hermes"
    assert out["valarm_dropped"] is False
    assert out.get("valarm_error")

    # Adoption stands despite the VALARM-strip failure (task 9 brief: one
    # extra ring from her phone is the accepted cost, not silence from
    # Hermes).
    updated = cal.get(db, e["id"])
    assert updated["owner"] == "hermes"
    assert len(rem.list_reminders(db, event_id=e["id"])) > 0

    rows = audit.query(db, since_utc=None, kind_prefix="cal.adopt", grep=None, limit=10)
    assert rows[0]["payload"]["valarm_dropped"] is False
    assert rows[0]["payload"]["valarm_error"]

def test_cal_adopt_disown_never_call_gate_deliver(db, capsys, monkeypatch):
    monkeypatch.setattr(gate, "deliver", _must_not_be_called)
    monkeypatch.setattr(extcal, "_request", _ok_valarm_response)

    e = _iphone_event(db)
    assert cli.main(["cal", "adopt", str(e["id"])]) == 0
    capsys.readouterr()
    assert cli.main(["cal", "disown", str(e["id"])]) == 0
    capsys.readouterr()

def test_cal_adopt_without_external_href_skips_network_entirely(db, capsys, monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("must not touch the network when there is no external_href")
    monkeypatch.setattr(extcal, "_request", boom)

    e = cal.add(db, "Прямое от Гермеса", _future_start())
    db.execute("UPDATE events SET owner='iphone' WHERE id=?", (e["id"],))
    rem.regenerate(db, e["id"])
    db.commit()

    rc = cli.main(["cal", "adopt", str(e["id"]), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["owner"] == "hermes"
    assert out["valarm_dropped"] is None

# --- Task 9 fix-round 1 ------------------------------------------------
# C1 (Critical): `disown` on a plain, never-imported Hermes event must be
# refused -- flipping such an event to owner='iphone' would silently drop
# its ONLY reminder source (nothing on her iPhone could ever ring for it).
# I2 (Important): `drop_valarm` must never PUT into her collection without
# a real If-Match etag (unlike export_own's own write-target, this
# collection can hold HER concurrent edits).
# I3 (Important): `_strip_valarm_ics` must refuse (not silently truncate)
# an unclosed VALARM or an otherwise unbalanced/incomplete resource.

def test_cal_disown_native_hermes_event_exit_2(db, capsys):
    # C1: a plain Hermes-created event (no external_uid/external_href at
    # all) must not be disown-able -- "не напоминай про это" said about
    # an ordinary Hermes event is exactly the natural phrasing that would
    # trigger this without the guard.
    rem.seed_default_rules(db)
    e = cal.add(db, "Йога от Гермеса", _future_start(hours=5))
    db.commit()
    assert e["owner"] == "hermes"
    assert not e.get("external_uid") and not e.get("external_href")
    pending_before = len(rem.list_reminders(db, event_id=e["id"]))
    assert pending_before > 0

    rc = cli.main(["cal", "disown", str(e["id"])])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err.strip() != ""

    # Nothing must have changed -- the guard fires before any write.
    updated = cal.get(db, e["id"])
    assert updated["owner"] == "hermes"
    assert len(rem.list_reminders(db, event_id=e["id"])) == pending_before

    rows = audit.query(db, since_utc=None, kind_prefix="cal.disown", grep=None, limit=10)
    assert rows == []

def test_cal_disown_reports_reminders_removed(db, capsys, monkeypatch):
    # Minor #4: disown's own output/audit should say how many pending
    # reminders it actually dropped, symmetric with adopt's reminders_created.
    e = _iphone_event(db)
    monkeypatch.setattr(extcal, "_request", _ok_valarm_response)
    assert cli.main(["cal", "adopt", str(e["id"])]) == 0
    capsys.readouterr()
    pending = len(rem.list_reminders(db, event_id=e["id"]))
    assert pending > 0

    rc = cli.main(["cal", "disown", str(e["id"]), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["reminders_removed"] == pending
    assert rem.list_reminders(db, event_id=e["id"]) == []

    rows = audit.query(db, since_utc=None, kind_prefix="cal.disown", grep=None, limit=10)
    assert rows[0]["payload"]["reminders_removed"] == pending

def test_cal_adopt_valarm_strip_refuses_put_when_no_etag_on_record(db, capsys, monkeypatch):
    # I2: an owner='iphone' row with no external_etag on record must not
    # trigger an unconditional PUT into her collection.
    calls = []
    valid_ics = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:her-uid-1\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    def fake_request(method, url, headers=None, body=None, timeout=None):
        calls.append(method)
        if method == "GET":
            return extcal.Response(200, valid_ics, {})
        raise AssertionError(f"must not PUT with no etag on record: {calls}")
    monkeypatch.setattr(extcal, "_request", fake_request)

    e = _iphone_event(db, etag=None)
    rc = cli.main(["cal", "adopt", str(e["id"]), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["owner"] == "hermes"  # adoption still stands
    assert out["valarm_dropped"] is False
    assert "etag" in out["valarm_error"]
    assert calls == ["GET"]  # never reached the PUT

def test_cal_adopt_valarm_strip_412_retry_refuses_when_reread_has_no_etag(db, capsys, monkeypatch):
    # I2's second refusal path: a 412 whose re-read GET comes back with
    # no fresh ETag must not fall back to an unconditional retry PUT.
    calls = []
    valid_ics = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:her-uid-1\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    def fake_request(method, url, headers=None, body=None, timeout=None):
        calls.append(method)
        if method == "GET":
            return extcal.Response(200, valid_ics, {})  # no ETag header
        if method == "PUT" and calls.count("PUT") == 1:
            return extcal.Response(412, b"", {})
        raise AssertionError(f"must not retry PUT with no fresh etag: {calls}")
    monkeypatch.setattr(extcal, "_request", fake_request)

    e = _iphone_event(db, etag='"stale"')
    rc = cli.main(["cal", "adopt", str(e["id"]), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["owner"] == "hermes"
    assert out["valarm_dropped"] is False
    assert "etag" in out["valarm_error"]
    assert calls == ["GET", "PUT", "GET"]  # re-read happened, retry did not

def test_cal_adopt_valarm_strip_refuses_unclosed_valarm(db, capsys, monkeypatch):
    # I3(a): an unclosed BEGIN:VALARM must not silently truncate the rest
    # of the resource (END:VEVENT/END:VCALENDAR and everything after it).
    calls = []
    unclosed = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:her-uid-1\r\n"
                "SUMMARY:Йога\r\nBEGIN:VALARM\r\nACTION:DISPLAY\r\n")
    def fake_request(method, url, headers=None, body=None, timeout=None):
        calls.append(method)
        if method == "GET":
            return extcal.Response(200, unclosed, {})
        raise AssertionError(f"must not PUT a malformed/truncated resource: {calls}")
    monkeypatch.setattr(extcal, "_request", fake_request)

    e = _iphone_event(db)
    rc = cli.main(["cal", "adopt", str(e["id"]), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["owner"] == "hermes"  # adoption still stands
    assert out["valarm_dropped"] is False
    assert calls == ["GET"]  # refused before any PUT

def test_cal_adopt_valarm_strip_refuses_truncated_mid_get(db, capsys, monkeypatch):
    # I3(b): a resource that looks cut off mid-transfer (unbalanced
    # BEGIN/END, missing END:VCALENDAR) must be refused, not re-PUT as-is.
    calls = []
    truncated = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:her-uid-1\r\n"
                 "BEGIN:VALARM\r\nACTION:DISPLAY\r\nEND:VALARM\r\nEND:VEVENT\r\n")
    # note: no END:VCALENDAR -- looks like the GET was cut short.
    def fake_request(method, url, headers=None, body=None, timeout=None):
        calls.append(method)
        if method == "GET":
            return extcal.Response(200, truncated, {})
        raise AssertionError(f"must not PUT a truncated resource: {calls}")
    monkeypatch.setattr(extcal, "_request", fake_request)

    e = _iphone_event(db)
    rc = cli.main(["cal", "adopt", str(e["id"]), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["owner"] == "hermes"
    assert out["valarm_dropped"] is False
    assert calls == ["GET"]

# --- Final review, blocker 1 (Critical): adopt on a recurring series --------
# A recurring event's master + RECURRENCE-ID overrides arrive as ONE CalDAV
# resource; `expand()` materializes it into N `events` rows sharing the same
# `external_href`. `drop_valarm` strips VALARM from that href's resource AS A
# WHOLE (there is no "just this occurrence"'s VALARM on the wire), so the
# original single-row `adopt` flipped owner for ONE occurrence while
# silencing her phone for ALL of them -- every other occurrence lost its
# only alarm and gained no Hermes chain either. `disown` cannot undo this
# (the alarm is gone, never captured anywhere). Fix: adopt widens to every
# `owner='iphone'` row sharing the target's `external_href` in the same
# atomic operation, before the one (still-shared) VALARM-strip call.

def _iphone_series_occurrence(db, href, external_uid, title="Тренировка", start=None):
    """One owner='iphone' occurrence of a recurring series -- mirrors what
    extcal._apply_event_insert leaves behind for EACH materialized
    occurrence of a master+overrides resource: same external_href (they
    all come from the same CalDAV GET), different external_uid (uid +
    recurrence_id, extcal's own composite-key convention)."""
    rem.seed_default_rules(db)
    start = start or _future_start(hours=5)
    e = cal.add(db, title, start)
    db.execute(
        "UPDATE events SET owner='iphone', external_uid=?, "
        "external_href=?, external_etag='\"e0\"' WHERE id=?",
        (external_uid, href, e["id"]))
    rem.regenerate(db, e["id"])
    db.commit()
    return cal.get(db, e["id"])

def test_cal_adopt_recurring_series_adopts_every_occurrence_sharing_href(db, capsys, monkeypatch):
    href = "https://caldav.icloud.com/1/calendars/personal/training-series.ics"
    e1 = _iphone_series_occurrence(db, href, "training-uid::rid1",
                                    start=_future_start(hours=5))
    e2 = _iphone_series_occurrence(db, href, "training-uid::rid2",
                                    start=_future_start(hours=5 + 24 * 7))
    e3 = _iphone_series_occurrence(db, href, "training-uid::rid3",
                                    start=_future_start(hours=5 + 24 * 14))

    calls = []
    def counting_request(method, *a, **kw):
        calls.append(method)
        return _ok_valarm_response(method, *a, **kw)
    monkeypatch.setattr(extcal, "_request", counting_request)

    rc = cli.main(["cal", "adopt", str(e1["id"]), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["owner"] == "hermes"
    assert out["valarm_dropped"] is True
    assert sorted(out["adopted_ids"]) == sorted([e1["id"], e2["id"], e3["id"]])

    # Every sibling occurrence flipped owner and got its own chain -- not
    # just the one named on the command line.
    for e in (e1, e2, e3):
        updated = cal.get(db, e["id"])
        assert updated["owner"] == "hermes"
        assert len(rem.list_reminders(db, event_id=e["id"])) > 0

    # Exactly ONE VALARM-strip round-trip for the whole series -- the href
    # is shared, so one GET/PUT covers all three occurrences; this must
    # not turn into N separate CalDAV writes.
    assert calls.count("GET") == 1
    assert calls.count("PUT") == 1

    rows = audit.query(db, since_utc=None, kind_prefix="cal.adopt", grep=None, limit=10)
    assert len(rows) == 1
    assert sorted(rows[0]["payload"]["adopted_ids"]) == sorted([e1["id"], e2["id"], e3["id"]])

def test_cal_adopt_single_event_unaffected_by_series_widening(db, capsys, monkeypatch):
    """Regression guard: a plain, non-recurring iPhone event (its href
    belongs to no other row) must still adopt exactly as before -- no
    `adopted_ids` in the output, and a DIFFERENT owner='iphone' event
    (its own, unrelated external_href) is not swept up by the widening
    query.

    Fix-round 2, minor (c): the PREVIOUS version of this guard asserted
    `cal.get(db, other["id"])["owner"] == "hermes"` on a PLAIN
    `cal.add()`-created event -- true trivially, since `cal.add()`
    already defaults new events to `owner='hermes'` with no `adopt`
    call involved at all, so that assertion could never have caught a
    regression where the sibling-widening query over-matched. This
    version uses a second, genuinely owner='iphone' event (via
    `_iphone_series_occurrence`) with its OWN distinct href, and checks
    that IT specifically is left untouched (`owner` still 'iphone').
    """
    e = _iphone_event(
        db, href="https://caldav.icloud.com/1/calendars/personal/evt1.ics")
    other = _iphone_series_occurrence(
        db, "https://caldav.icloud.com/1/calendars/personal/evt-unrelated.ics",
        "other-uid-unrelated", title="Другое", start=_future_start(hours=10))
    monkeypatch.setattr(extcal, "_request", _ok_valarm_response)

    rc = cli.main(["cal", "adopt", str(e["id"]), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["owner"] == "hermes"
    assert "adopted_ids" not in out

    untouched = cal.get(db, other["id"])
    assert untouched["owner"] == "iphone"

# --- Fix-round 2, finding N2: `disown` mirrors adopt's series widening -----
# (final review had flagged this as an asymmetry: `adopt` widens to the
# whole series, `disown` did not, leaving a confusing half-revert).

def test_cal_disown_recurring_series_disowns_every_occurrence_sharing_href(db, capsys, monkeypatch):
    href = "https://caldav.icloud.com/1/calendars/personal/training-series.ics"
    e1 = _iphone_series_occurrence(db, href, "training-uid::rid1",
                                    start=_future_start(hours=5))
    e2 = _iphone_series_occurrence(db, href, "training-uid::rid2",
                                    start=_future_start(hours=5 + 24 * 7))
    e3 = _iphone_series_occurrence(db, href, "training-uid::rid3",
                                    start=_future_start(hours=5 + 24 * 14))
    monkeypatch.setattr(extcal, "_request", _ok_valarm_response)

    rc = cli.main(["cal", "adopt", str(e1["id"])])
    assert rc == 0
    capsys.readouterr()
    for e in (e1, e2, e3):
        assert cal.get(db, e["id"])["owner"] == "hermes"
        assert len(rem.list_reminders(db, event_id=e["id"])) > 0

    rc = cli.main(["cal", "disown", str(e2["id"]), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["owner"] == "iphone"
    assert sorted(out["disowned_ids"]) == sorted([e1["id"], e2["id"], e3["id"]])

    for e in (e1, e2, e3):
        updated = cal.get(db, e["id"])
        assert updated["owner"] == "iphone"
        assert rem.list_reminders(db, event_id=e["id"]) == []

    rows = audit.query(db, since_utc=None, kind_prefix="cal.disown", grep=None, limit=10)
    assert len(rows) == 1
    assert sorted(rows[0]["payload"]["disowned_ids"]) == sorted([e1["id"], e2["id"], e3["id"]])
    assert rows[0]["payload"]["reminders_removed"] > 0


def test_cal_disown_single_event_unaffected_by_series_widening(db, capsys, monkeypatch):
    """Regression guard, mirrors adopt's own: disowning one adopted event
    must not touch a DIFFERENT adopted event with its own, unrelated
    href."""
    e = _iphone_event(
        db, href="https://caldav.icloud.com/1/calendars/personal/evt1.ics")
    other = _iphone_series_occurrence(
        db, "https://caldav.icloud.com/1/calendars/personal/evt-unrelated2.ics",
        "other-uid-unrelated-2", title="Другое2", start=_future_start(hours=12))
    monkeypatch.setattr(extcal, "_request", _ok_valarm_response)
    assert cli.main(["cal", "adopt", str(e["id"])]) == 0
    capsys.readouterr()
    assert cli.main(["cal", "adopt", str(other["id"])]) == 0
    capsys.readouterr()

    rc = cli.main(["cal", "disown", str(e["id"]), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert "disowned_ids" not in out

    assert cal.get(db, other["id"])["owner"] == "hermes"  # untouched


# --- Fix-round 2, minor (a): adopt/disown widening must not sweep in a
# cancelled/done occurrence of the same recurring resource -- it is already
# resolved, not a live occurrence to remind about.

def test_cal_adopt_skips_cancelled_sibling_of_the_series(db, capsys, monkeypatch):
    href = "https://caldav.icloud.com/1/calendars/personal/training-series2.ics"
    e1 = _iphone_series_occurrence(db, href, "training2-uid::rid1",
                                    start=_future_start(hours=5))
    e2 = _iphone_series_occurrence(db, href, "training2-uid::rid2",
                                    start=_future_start(hours=5 + 24 * 7))
    db.execute("UPDATE events SET status='cancelled' WHERE id=?", (e2["id"],))
    db.commit()
    monkeypatch.setattr(extcal, "_request", _ok_valarm_response)

    rc = cli.main(["cal", "adopt", str(e1["id"]), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert "adopted_ids" not in out  # only e1 -- e2 excluded (cancelled)

    untouched = cal.get(db, e2["id"])
    assert untouched["owner"] == "iphone"
    assert untouched["status"] == "cancelled"


def test_cal_disown_widens_to_a_cancelled_sibling_of_the_series_too(db, capsys, monkeypatch):
    """Fix-round 3, Important finding I1 (status asymmetry): this test
    used to be `test_cal_disown_skips_cancelled_sibling_of_the_series`
    and asserted the OPPOSITE of what is asserted below -- that a
    cancelled `owner='hermes'` sibling stayed untouched by `disown`,
    mirroring `cal adopt`'s own `status='active'` widening filter. That
    mirroring was the bug: `extcal._series_already_adopted` (the check a
    later sync tick uses to decide whether a BRAND-NEW occurrence should
    inherit `owner='hermes'`) matches ANY status, not just 'active' --
    so a status-filtered `disown` could leave exactly one cancelled-but-
    still-`owner='hermes'` row behind, which `_series_already_adopted`
    would keep finding forever, silently re-adopting every future
    occurrence of a series she explicitly disowned. `disown`'s widening
    now matches ANY status too -- see `cmd_cal_disown`'s own docstring.
    (`cal adopt`'s OWN widening keeps its `status='active'` filter
    unchanged -- that side of the asymmetry was never the problem.)
    """
    href = "https://caldav.icloud.com/1/calendars/personal/training-series3.ics"
    e1 = _iphone_series_occurrence(db, href, "training3-uid::rid1",
                                    start=_future_start(hours=5))
    e2 = _iphone_series_occurrence(db, href, "training3-uid::rid2",
                                    start=_future_start(hours=5 + 24 * 7))
    monkeypatch.setattr(extcal, "_request", _ok_valarm_response)
    assert cli.main(["cal", "adopt", str(e1["id"]), "--json"]) == 0
    capsys.readouterr()
    assert cal.get(db, e2["id"])["owner"] == "hermes"  # e2 adopted alongside e1

    # e2 gets cancelled while still owner='hermes' -- exactly what a
    # remote cancellation via the sync tick leaves behind
    # (extcal._apply_event_cancel never touches owner).
    db.execute("UPDATE events SET status='cancelled' WHERE id=?", (e2["id"],))
    db.commit()

    rc = cli.main(["cal", "disown", str(e1["id"]), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert sorted(out["disowned_ids"]) == sorted([e1["id"], e2["id"]])

    reverted = cal.get(db, e2["id"])
    assert reverted["owner"] == "iphone"  # no longer left behind as owner='hermes'
    assert reverted["status"] == "cancelled"  # its own status is untouched either way


def test_cal_disown_status_asymmetry_no_longer_strands_a_hermes_row(db, capsys, monkeypatch):
    """I1's own end-to-end scenario: after `disown` widens to every
    status (the fix above), NO `owner='hermes'` row is left sharing this
    href -- so `extcal._series_already_adopted` (what a later sync tick
    actually calls to decide a brand-new occurrence's owner) correctly
    reports "not adopted" afterwards, instead of continuing to answer
    "yes" off a stale cancelled leftover forever."""
    href = "https://caldav.icloud.com/1/calendars/personal/training-series4.ics"
    e1 = _iphone_series_occurrence(db, href, "training4-uid::rid1",
                                    start=_future_start(hours=5))
    e2 = _iphone_series_occurrence(db, href, "training4-uid::rid2",
                                    start=_future_start(hours=5 + 24 * 7))
    monkeypatch.setattr(extcal, "_request", _ok_valarm_response)
    assert cli.main(["cal", "adopt", str(e1["id"]), "--json"]) == 0
    capsys.readouterr()
    assert extcal._series_already_adopted(db, href) is True

    db.execute("UPDATE events SET status='cancelled' WHERE id=?", (e2["id"],))
    db.commit()

    rc = cli.main(["cal", "disown", str(e1["id"]), "--json"])
    assert rc == 0
    capsys.readouterr()

    # Before this fix: e2 stayed owner='hermes' (status filter excluded
    # it), so this would still report True -- a future occurrence of the
    # series would silently keep inheriting owner='hermes' despite the
    # explicit disown.
    assert extcal._series_already_adopted(db, href) is False


# --- Final review, blocker 2 (Important): `--now` requires `--dry-run` -----
# on the real CLI path (extcal._time_range's own iCloud query window always
# uses the real clock, never --now, so a real run desyncs the local
# snapshot from what iCloud actually returns and can permanently tombstone
# rows that never disappeared).

def test_tick_cal_ext_now_without_dry_run_refused_by_main(capsys):
    rc = cli.main(["tick", "cal-ext", "--now", "2037-01-01T00:00:00+00:00"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "--dry-run" in captured.err

def test_tick_cal_ext_now_with_dry_run_allowed_through_main(capsys, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config",
                         lambda *a, **k: {"extcal_enabled": False})
    rc = cli.main(["tick", "cal-ext", "--now", "2037-01-01T00:00:00+00:00",
                   "--dry-run"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "disabled" in captured.out

def test_tick_cal_ext_without_now_unaffected_by_the_guard(capsys, monkeypatch):
    monkeypatch.setattr(cli.gate, "load_config",
                         lambda *a, **k: {"extcal_enabled": False})
    rc = cli.main(["tick", "cal-ext"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "disabled" in captured.out

# --- Final review, blocker 3 (Important, privacy) ---------------------------
# `cal.adopt.valarm_error` must not leak HER OWN iCloud event's absolute
# href into stdout or the audit entry.

def test_cal_adopt_valarm_error_redacts_her_href(db, capsys, monkeypatch):
    href = "https://caldav.icloud.com/1/calendars/personal/evt-secret.ics"
    def fake_request(*a, **kw):
        return None  # total transport failure -> "GET <href> failed (status=None)"
    monkeypatch.setattr(extcal, "_request", fake_request)

    e = _iphone_event(db, href=href)
    rc = cli.main(["cal", "adopt", str(e["id"]), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["valarm_dropped"] is False
    assert href not in out["valarm_error"]
    assert "evt-secret.ics" not in out["valarm_error"]
    assert "<href>" in out["valarm_error"]

    rows = audit.query(db, since_utc=None, kind_prefix="cal.adopt", grep=None, limit=10)
    payload_text = json.dumps(rows[0]["payload"], ensure_ascii=False)
    assert href not in payload_text
    assert "evt-secret.ics" not in payload_text


# --- Fix-round 2, minor (b): `_redact_extcal_text` must also catch the
# double-quoted `repr()` form (an RRULE value containing an apostrophe
# switches Python's repr to double quotes) and the free-form dateutil
# exception-detail text `_expand_master` appends alongside the quoted
# RRULE literal -- neither was reachable by the original single-quote-only
# pattern.

def test_redact_extcal_text_catches_double_quoted_rrule_repr():
    # A value containing an apostrophe -> Python's repr() switches to
    # double quotes (verified directly: repr("FREQ=WEEKLY;X=IT'S") ==
    # '"FREQ=WEEKLY;X=IT\'S"' style double-quoted form).
    text = ("Calendar: RRULE \"FREQ=WEEKLY;NOTE=IT'S\" for uid='series1' "
            "could not be parsed/evaluated (ValueError: bad token)")
    out = cli._redact_extcal_text(text)
    assert "IT'S" not in out
    assert "FREQ=WEEKLY" not in out
    assert "RRULE <redacted>" in out
    assert "uid='series1'" in out  # uid is deliberately left alone


def test_redact_extcal_text_strips_dateutil_exception_detail():
    # The dateutil exception's OWN text (`{e}`) is not `!r`-quoted at all
    # -- it can independently echo a raw fragment of the RRULE value in a
    # shape the quoted-literal pattern never matches.
    text = ("Calendar: RRULE 'FREQ=WEEKLY;BYDAY=MO' for uid='series1' "
            "could not be parsed/evaluated (ValueError: leaked "
            "FREQ=WEEKLY;BYDAY=MO fragment)")
    out = cli._redact_extcal_text(text)
    assert "FREQ=WEEKLY;BYDAY=MO" not in out
    assert "leaked" not in out
    assert "RRULE <redacted>" in out
    assert "(ValueError: <redacted>)" in out
    assert "uid='series1'" in out


def test_redact_extcal_text_still_redacts_plain_href_and_single_quoted_rrule():
    # Regression guard: the original two cases (final review, blocker 3)
    # keep working exactly as before.
    text = ("GET https://caldav.icloud.com/1/calendars/personal/evt1.ics "
            "failed (status=404); RRULE 'FREQ=DAILY' for uid='u1' could "
            "not be parsed/evaluated (ValueError: bad)")
    out = cli._redact_extcal_text(text)
    assert "evt1.ics" not in out
    assert "<href>" in out
    assert "FREQ=DAILY" not in out
    assert "RRULE <redacted>" in out


# --- Fix-round 3, Minor finding M2: `_EXPAND_ERROR_DETAIL_RE` used to
# close its capture on the FIRST `)` after the exception type/colon
# (`[^)]*(\))`) -- wrong whenever dateutil's own exception text contains
# ITS OWN, unrelated parentheses, and unable to match AT ALL (so nothing
# in the whole clause got redacted) when that text happened to contain no
# `)` before the end of the string.

def test_redact_extcal_text_does_not_leak_a_tail_after_a_nested_paren():
    # dateutil quotes the bad token in its own parentheses -- the OLD
    # pattern stopped at THAT `)`, letting everything after it (a second
    # RRULE-shaped fragment) ride straight through unredacted.
    text = ("Calendar: RRULE 'FREQ=SECRETLY' for uid='series1' could not "
            "be parsed/evaluated (ValueError: invalid token "
            "(FREQ=SECRETLY) trailing FREQ=LEAK)")
    out = cli._redact_extcal_text(text)
    assert "FREQ=LEAK" not in out
    assert "FREQ=SECRETLY" not in out
    assert "(ValueError: <redacted>)" in out
    assert "uid='series1'" in out


def test_redact_extcal_text_redacts_even_without_a_trailing_closing_paren():
    # No `)` anywhere after the exception type/colon -- the OLD pattern
    # (which REQUIRED one to match at all) skipped redaction entirely
    # here, leaking the whole raw exception text, RRULE fragment
    # included.
    text = ("Calendar: RRULE 'FREQ=DAILY' for uid='series2' could not be "
            "parsed/evaluated (ValueError: unterminated token "
            "FREQ=DAILY;X=(unbalanced")
    out = cli._redact_extcal_text(text)
    assert "FREQ=DAILY;X=" not in out
    assert "unbalanced" not in out
    assert "(ValueError: <redacted>" in out
    assert "uid='series2'" in out


# --- Final re-review (pre-prod hardening): `_EXPAND_ERROR_DETAIL_RE` was
# built without `re.DOTALL`, so bare `.` could never cross a `\n` --
# `expand()` catches `Exception` broadly around `dateutil.rrulestr`, so a
# multi-line exception message is possible, and when one lands here the
# pattern's `$` (end of string) was never reachable across the embedded
# newline: NOT a partial/truncated redaction, the WHOLE match failed and
# the entire multi-line tail (RRULE fragment included) rode straight
# through unredacted -- verified live before the fix by feeding exactly
# this text through `_redact_extcal_text` and observing it come back
# completely unchanged.

def test_redact_extcal_text_handles_multiline_exception_detail():
    text = ("Calendar: RRULE 'FREQ=ANNUALLY' for uid='series3' could not "
            "be parsed/evaluated (ValueError: line1 FREQ=ANNUALLY\n"
            "line2 FREQ=LEAK)")
    out = cli._redact_extcal_text(text)
    assert "FREQ=ANNUALLY" not in out
    assert "FREQ=LEAK" not in out
    assert "line1" not in out
    assert "line2" not in out
    assert "RRULE <redacted>" in out
    assert "(ValueError: <redacted>)" in out
    assert "uid='series3'" in out


# --- Final re-review (pre-prod hardening): `extcal_full_resync_days`
# used to go straight from `cfg.get(...)` into `timedelta(days=...)`
# with no validation. `0`/negative makes `force_full` true on EVERY tick
# forever (instead of ~once/day); non-numeric blows up `timedelta` with
# a `TypeError` the broad `except Exception` in `cmd_tick_cal_ext` turns
# into a `tick.error` -- sync stays dead until a human fixes the config
# by hand. `_extcal_full_resync_days` clamps to `[1, 30]` and defaults
# (never raises) on anything that doesn't coerce to `int`.

@pytest.mark.parametrize("raw, expected", [
    (0, 1),            # zero -- would otherwise force a full pass every tick
    (-5, 1),            # negative -- same runaway-full-resync failure mode
    (1, 1),             # already the minimum -- passes through unchanged
    (15, 15),           # ordinary in-range value -- passes through unchanged
    (30, 30),           # already the maximum -- passes through unchanged
    (31, 30),           # one over the cap -- clamped down
    (1000, 30),         # wildly too large -- clamped down
    (3.7, 3),           # float coerces via int() truncation, not a fallback
])
def test_extcal_full_resync_days_clamps_numeric_range(raw, expected):
    assert cli._extcal_full_resync_days(
        {"extcal_full_resync_days": raw}) == expected


@pytest.mark.parametrize("raw", ["not-a-number", None, [], {}, "3.5x"])
def test_extcal_full_resync_days_falls_back_to_default_on_non_numeric(raw):
    # Must NOT raise (the old code's bare `cfg.get(...)` fed straight
    # into `timedelta(days=...)` would TypeError on all of these) --
    # falls back to the same default `gate.CONFIG_DEFAULTS` documents.
    assert cli._extcal_full_resync_days(
        {"extcal_full_resync_days": raw}) == 1


def test_extcal_full_resync_days_defaults_when_key_missing():
    assert cli._extcal_full_resync_days({}) == 1


def test_refresh_pending_acks_does_not_overwrite_projection_on_config_error(
    db, monkeypatch
):
    writes = []

    def fail_config():
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(cli.gate, "load_config", fail_config)
    monkeypatch.setattr(cli.acks, "write", lambda *args, **kwargs: writes.append(1))

    assert cli._refresh_pending_acks(db) is None
    assert writes == []
