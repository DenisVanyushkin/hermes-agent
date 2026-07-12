import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from fam import audit, cal, cli, gate, mail, people, places, rem

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

def test_cal_add_past_end_without_past_start_is_not_validated(db, capsys):
    end_in_past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    rc = cli.main(["cal", "add", "--title", "X", "--start",
                   "2099-01-01T05:00:00+00:00", "--end", end_in_past, "--json"])
    assert rc == 0

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
                   "--travel-min", "0", "--json"])
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
                    "error_capped": 0}

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
                    "2026-07-15T05:00:00+00:00", "--with", "Денис", "--json"])
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
                    "2026-07-15T05:00:00+00:00", "--with", "Тая"])

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
                    "2026-07-15T05:00:00+00:00", "--with", "Денис"])

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
                    "2026-07-15T05:00:00+00:00", "--with", "Денис", "--json"])
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
                    "2026-07-15T05:00:00+00:00", "--with", "Денис", "--json"])
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
                        "2026-07-15T05:00:00+00:00", "--with", "Денис", "--json"])
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
                        "2026-07-15T05:00:00+00:00", "--with", "Денис", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc_add == 0
    assert len(calls) == 1

    rc_update = cli.main(["cal", "update", str(out["id"]), "--start",
                           "2026-07-15T06:00:00+00:00"])
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
                        "2026-07-15T05:00:00+00:00", "--with", "Денис", "--json"])
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
