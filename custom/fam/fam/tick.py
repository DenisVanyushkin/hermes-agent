"""Scheduled ticks: `fam tick reminders`/`fam tick digest` run one sweep
each with no LLM in the orchestration loop itself (gate.deliver's rewrite
subprocess is the only place an LLM is involved).

Unlike cal.py/rem.py/gate.py/people.py/places.py -- which never commit,
leaving the transaction boundary to their caller -- this module DOES own
its own commits. A tick has no external caller managing a shared
transaction; the tick run IS the transaction boundary, and it is invoked
on a schedule (systemd timer, Task 8) with no human watching. Committing
once per due reminder (rather than once for the whole tick) narrows the
blast radius of a mid-run crash: gate.deliver's send is an irreversible
real-world side effect (a WhatsApp message actually left), so once a
reminder's outcome is committed it will never be resent by a later tick.
With a single end-of-tick commit, a crash after reminder N of M would
leave all N already-sent messages still marked "pending" in the DB, and
the next tick would resend all of them. Net effect: this module guarantees
at-least-once delivery -- a crash between gate.deliver's send and the
per-reminder commit can cause the next tick to resend that one reminder,
but a reminder already committed as sent is never resent, and a due
reminder is never silently dropped.

fam tick digest (Task 7) is a much simpler single-message tick: unlike
reminders, there is exactly one thing to send per run (or zero, on the
dup-guard skip), so it owns a single commit at the very end rather than
one per item.
"""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fam import audit, cal, gate, rem, road, weather

ALMATY = ZoneInfo("Asia/Almaty")

# On repeated "error" outcomes from gate.deliver, a reminder is cancelled
# rather than retried forever once its error_count reaches this many.
ERROR_CAP = 3

# Anchor re-check threshold for road recompute (see _recompute_road's
# docstring): a shift bigger than this many minutes from the prior
# travel_min_road is considered a "big shift" whose stale depart-time
# anchor forces one follow-up recompute (checked_at=NULL) rather than
# just stamping checked_at=now.
ROAD_ANCHOR_RECHECK_MIN = 10

# The digest's fixed closing question -- goes into raw["question"] (the
# JSON the LLM rewrite sees) AND the deterministic human_fallback, from
# this single constant, so the two can never drift apart.
DIGEST_QUESTION = "Если появятся планы или изменения — расскажи или надиктуй, я запишу."


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_utc(value):
    """Parse an ISO-8601 string into an aware UTC datetime. A naive string
    (no tzinfo) is treated as already UTC -- mirrors gate.py/rem.py's own
    local copy of this helper.
    """
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def road_recompute(conn, now_utc=None, cfg=None):
    """Threshold road recompute (Phase 3a Task 4): at T-120 and T-60
    before an active future event's leave_at (cfg["road_recompute_min"],
    default [120, 60] -- sorted desc here so the widest window is tried
    first), recompute travel time via road.compute_travel_min with
    departAt = the event's CURRENT leave_at. Called at the very start of
    reminders(), before due selection, so a moved leave_at affects this
    same tick's due reminders -- not just the next one.

    Candidate selection: cheap SQL first (active, future-start events
    whose place has both lat and lon), then per-event Python logic
    (leave_at needs cal.get's full event dict, which SQL alone can't
    give). If cfg has no home coordinates configured, nothing is ever a
    candidate -- this is checked once up front (mirrors cal.recompute_road's
    same guard) rather than per event.

    Per event: minutes_to_leave = leave_at - now. Events already past
    their leave_at (<=0) are skipped -- nothing left to prepare for. For
    each threshold (widest first): the window is "open" once
    minutes_to_leave <= threshold; it counts as already-checked-in-this-
    window only if road_checked_at is not None AND
    road_checked_at >= leave_at - threshold (i.e. the last check
    happened at or after this window opened). The first open,
    not-yet-checked threshold triggers exactly one recompute for this
    event this tick, then moves to the next event -- this both matches
    "one recompute per threshold" (a check inside window N only
    satisfies window N+1 once N+1's own boundary arrives, since N+1's
    window opens later than N's) and avoids doing two recomputes for one
    event in one tick when multiple windows are simultaneously open
    (e.g. the very first check ever, discovered already inside the T-60
    window).

    The threshold guard is really a FRESHNESS INVARIANT: an event is
    skipped for threshold T iff a check already exists within T minutes
    of the event's CURRENT leave_at (checked_at >= leave_dt - T) -- so
    when leave itself moves, the guard is re-evaluated against the new
    leave and self-heals, rather than tracking historical window
    boundaries. Freshness alone still leaves one gap: the recompute that
    MOVED leave was anchored (depart_at) at the OLD leave, so its stored
    figure is fresh but its anchor is stale -- the anchor re-check rule
    below (big shift -> checked_at=NULL -> one follow-up recompute at
    the corrected anchor next tick) closes it.

    Write: source tomtom/straight with a changed minute value updates
    travel_min_road, audits road.recompute {event_id, old, new, source},
    and regenerates the reminder chain (rem.regenerate -- pending stages
    move, sent stages are untouched by construction). road_checked_at is
    stamped `now` for a small change, but set to NULL when the change is
    big (|new - old| > ROAD_ANCHOR_RECHECK_MIN, old not NULL) -- the anchor re-check rule
    above. Unchanged minutes only bump road_checked_at (no audit, no
    regen).
    Source manual/place/none has nothing new to persist as
    travel_min_road, but road_checked_at is still bumped -- otherwise an
    event whose place lacks coordinates in cfg's home sense, or that has
    fallen through to a manual/place figure, would be re-tried every
    single tick forever for no reason.

    Never raises: each event is wrapped in try/except -- any failure
    (including inside road.compute_travel_min, though that function
    itself is already defensive) is audited as road.hook_error and the
    loop continues to the next candidate, exactly like cal.recompute_road's
    contract.

    Commits once per event touched (recompute or checked-at bump), or
    once per per-event failure -- same per-item commit granularity as
    the rest of this module (see module docstring).

    Returns the count of events touched (recomputed or checked-at
    bumped) this tick -- folded into reminders()'s tick.reminders audit
    as "road_recomputed".
    """
    cfg = cfg if cfg is not None else gate.load_config()
    if cfg.get("road_home_lat") is None or cfg.get("road_home_lon") is None:
        return 0

    now = now_utc or _now()
    now_dt = _parse_utc(now)
    thresholds = sorted(cfg.get("road_recompute_min", [120, 60]), reverse=True)

    candidates = conn.execute(
        "SELECT e.id FROM events e JOIN places p ON p.id = e.place_id "
        "WHERE e.status='active' AND e.start_utc > ? "
        "AND p.lat IS NOT NULL AND p.lon IS NOT NULL",
        (now,),
    ).fetchall()

    touched = 0
    for row in candidates:
        event_id = row["id"]
        try:
            event = cal.get(conn, event_id)
            if event is None or event["status"] != "active":
                continue

            leave_dt = _parse_utc(rem.leave_at(conn, event))
            minutes_to_leave = (leave_dt - now_dt).total_seconds() / 60
            if minutes_to_leave <= 0:
                continue

            checked_at = event.get("road_checked_at")
            checked_dt = _parse_utc(checked_at) if checked_at else None

            for threshold in thresholds:
                if minutes_to_leave > threshold:
                    continue
                window_open = leave_dt - timedelta(minutes=threshold)
                if checked_dt is not None and checked_dt >= window_open:
                    continue

                depart_at = leave_dt.isoformat(timespec="seconds")
                minutes, source = road.compute_travel_min(
                    conn, event, cfg, now_utc=depart_at)
                # road_checked_at is stamped from this tick's own `now`
                # (injected or real, per _now() at the top of reminders())
                # -- NOT a fresh real-clock read -- so the threshold-
                # window guard above compares like with like across
                # ticks, mirroring how sent_at/fire_at_utc are stamped
                # elsewhere in this module. cal.recompute_road's own
                # hook uses a fresh real-clock stamp instead, but that
                # hook has no "which tick's now" ambiguity to resolve.
                if source in ("tomtom", "straight"):
                    old_minutes = event.get("travel_min_road")
                    if minutes != old_minutes:
                        # Anchor drift vs freshness (review fix): this
                        # recompute's depart_at was the OLD leave_at. A
                        # small delta barely moves leave, so the stale
                        # anchor is immaterial and the freshness
                        # invariant (checked within `threshold` min of
                        # the current leave) can resume. A BIG delta
                        # (>10 min, and only when there was a prior
                        # computed value to drift from -- old NULL means
                        # first-ever computation, whose anchor error is
                        # unknowable and bounded by the thresholds
                        # anyway) moves leave enough that the figure we
                        # just stored was computed for a departure time
                        # now materially wrong. Storing checked_at=NULL
                        # instead of now forces exactly one follow-up
                        # recompute on the next tick, anchored at the
                        # corrected leave; if that one is stable
                        # (delta <= 10) checked_at sticks. Oscillation is
                        # bounded by road_daily_cap.
                        big_shift = (old_minutes is not None
                                     and abs(minutes - old_minutes) > ROAD_ANCHOR_RECHECK_MIN)
                        checked_stamp = None if big_shift else now
                        conn.execute(
                            "UPDATE events SET travel_min_road=?, "
                            "road_checked_at=? WHERE id=?",
                            (minutes, checked_stamp, event_id),
                        )
                        audit.log(conn, "road.recompute",
                                  {"event_id": event_id, "old": old_minutes,
                                   "new": minutes, "source": source})
                        rem.regenerate(conn, event_id, now_utc=now)
                    else:
                        conn.execute(
                            "UPDATE events SET road_checked_at=? WHERE id=?",
                            (now, event_id),
                        )
                else:
                    # manual/place/none: nothing computed to persist as
                    # travel_min_road, but the window still counts as
                    # checked -- otherwise a place without coordinates
                    # (or a config without home coords) would be retried
                    # every single minute forever.
                    conn.execute(
                        "UPDATE events SET road_checked_at=? WHERE id=?",
                        (now, event_id),
                    )
                conn.commit()
                touched += 1
                break
        except Exception:
            audit.log(conn, "road.hook_error", {"event_id": event_id})
            conn.commit()

    return touched


def reminders(conn, now_utc=None, cfg=None):
    """Run one reminders tick.

    Selects pending reminders with fire_at_utc <= now (rem.list_reminders
    due=True). For each:
      - if its event is no longer active (cancelled/done, or missing --
        the latter shouldn't happen given events(id) ON DELETE CASCADE,
        but is treated the same defensively), the reminder itself is
        marked cancelled and audited as rem.cancel_stale, with no send
        attempt. This is a backstop for reminders whose event's status
        changed by some path other than cal.cancel()/cal.done() (which
        already cancel their own chain via rem.cancel_chain).
      - else if it is stale -- its age (now - fire_at_utc) exceeds
        cfg["reminder_max_age_min"] (default 120, see
        gate.CONFIG_DEFAULTS), OR now is already past event.start_utc
        plus that same max age -- it is cancelled and audited as
        rem.cancel_stale_age, with no send attempt. This is a backstop
        for a reminder repeatedly parked by quiet hours/budget until it
        has become operationally pointless (e.g. an 8-hour-old "pora
        vyhodit'" for an event that already happened).
      - otherwise gate.deliver(kind="reminder", ...) rewrites and sends
        it: "sent" -> status=sent + sent_at=now; "quiet"/"budget" -> left
        pending (a later tick will retry once the window/budget clears);
        "error" -> the reminder's error_count is incremented; once it
        reaches ERROR_CAP (3), the reminder is cancelled and audited as
        rem.cancel_error_cap instead of being retried forever, otherwise
        it is left pending (a later tick will retry) and counted as
        "error" so a persistent-but-not-yet-capped failure stays visible.

    Before any of the above, road_recompute(conn, now_utc=now, cfg=cfg)
    (Phase 3a Task 4) runs first: threshold road recomputes at T-120/T-60
    before an event's leave_at. Deliberately first, not last -- a
    recompute in THIS call can move leave_at (and thus the leave_at-
    anchored reminder chain) before due selection below runs, so a
    reminder whose fire time just shifted earlier is correctly picked up
    (or skipped) by this very tick rather than the next one.

    Commits once per due reminder (see module docstring), once per event
    touched by road_recompute, plus once more for the tick-level audit
    row below.

    ALWAYS audits tick.reminders {due, sent, quiet, budget, error,
    cancelled, stale, error_capped, road_recomputed} -- including an
    all-zero run, so "nothing was due" is a recorded fact rather than
    indistinguishable from "the tick didn't run at all". This is a
    deliberately richer contract than the original implementation plan's
    literal {due, sent, skipped}: quiet/budget/error/cancelled/stale/
    error_capped/road_recomputed are each their own bucket instead of
    being collapsed into "skipped", because a persistent delivery
    failure, a stale-age cancel, a quiet-hours defer, and a threshold
    road recompute are operationally different things an admin needs to
    tell apart in the tick summary.

    Returns the counts dict.
    """
    now = now_utc or _now()
    now_dt = _parse_utc(now)
    cfg = cfg if cfg is not None else gate.load_config()
    max_age_min = cfg.get("reminder_max_age_min", 120)
    max_age = timedelta(minutes=max_age_min)

    road_recomputed = road_recompute(conn, now_utc=now, cfg=cfg)

    due = rem.list_reminders(conn, due=True, now_utc=now)
    counts = {"due": len(due), "sent": 0, "quiet": 0, "budget": 0,
              "error": 0, "cancelled": 0, "stale": 0, "error_capped": 0,
              "road_recomputed": road_recomputed}

    for reminder in due:
        event = cal.get(conn, reminder["event_id"])
        if event is None or event["status"] != "active":
            conn.execute(
                "UPDATE reminders SET status='cancelled' WHERE id=?",
                (reminder["id"],),
            )
            audit.log(conn, "rem.cancel_stale",
                      {"reminder_id": reminder["id"],
                       "event_id": reminder["event_id"]})
            counts["cancelled"] += 1
            conn.commit()
            continue

        fire_dt = _parse_utc(reminder["fire_at_utc"])
        start_dt = _parse_utc(event["start_utc"])
        age = now_dt - fire_dt
        if age > max_age or now_dt > start_dt + max_age:
            conn.execute(
                "UPDATE reminders SET status='cancelled' WHERE id=?",
                (reminder["id"],),
            )
            audit.log(conn, "rem.cancel_stale_age",
                      {"reminder_id": reminder["id"], "event_id": event["id"],
                       "fire_at_utc": reminder["fire_at_utc"],
                       "age_min": round(age.total_seconds() / 60)})
            counts["stale"] += 1
            conn.commit()
            continue

        raw = {
            "label": reminder["label"],
            "event_id": event["id"],
            "title": event["title"],
            "start_local": event["start_local"],
            "participants": [p["name"] for p in event["participants"]],
            # Live-found bug (Task 16): the rewrite once bound the
            # label's action to event["start_local"] (the event's own
            # start) instead of the actual send time -- e.g. "В 13:00 Тае
            # пора собираться" for a reminder that fires 45 min before
            # the event. sent_now_local is the real send-time anchor,
            # derived from the same `now_dt` this tick already parsed
            # (never a fresh wall-clock read), so gate.py's rewrite has
            # an explicit "now" distinct from the event's start_local --
            # see GATE_REMINDER_TIME_SEMANTICS_INSTRUCTION.
            "sent_now_local": now_dt.astimezone(ALMATY).isoformat(
                timespec="seconds"
            ),
        }
        if event["place"]:
            raw["place_name"] = event["place"]["name"]
        # Phase 2c, task 7: this event's already-sent reminder texts
        # today (if any) go into raw so the rewrite doesn't repeat
        # itself verbatim on a chain continuation -- see
        # gate.prior_texts_today and the variation-rule clause appended
        # to GATE_REMINDER_TIME_SEMANTICS_INSTRUCTION. Key omitted
        # entirely (not an empty list) when there's nothing prior, so a
        # first send's raw stays exactly as it was before this task.
        prior = gate.prior_texts_today(conn, event["id"], now)
        if prior:
            raw["prior_texts"] = prior
        human_fallback = (
            f"{reminder['label']}: {event['title']} — {event['start_local']}"
        )

        status = gate.deliver(conn, "reminder", raw, human_fallback, cfg,
                               now_utc=now)
        if status == "sent":
            conn.execute(
                "UPDATE reminders SET status='sent', sent_at=? WHERE id=?",
                (now, reminder["id"]),
            )
            counts["sent"] += 1
        elif status == "error":
            new_error_count = reminder["error_count"] + 1
            if new_error_count >= ERROR_CAP:
                conn.execute(
                    "UPDATE reminders SET status='cancelled', error_count=? "
                    "WHERE id=?",
                    (new_error_count, reminder["id"]),
                )
                audit.log(conn, "rem.cancel_error_cap",
                          {"reminder_id": reminder["id"],
                           "errors": new_error_count})
                counts["error_capped"] += 1
            else:
                conn.execute(
                    "UPDATE reminders SET error_count=? WHERE id=?",
                    (new_error_count, reminder["id"]),
                )
                counts["error"] += 1
        else:
            counts[status] += 1
        conn.commit()

    audit.log(conn, "tick.reminders", counts)
    conn.commit()
    return counts


def _today_almaty(now_utc):
    return _parse_utc(now_utc).astimezone(ALMATY).date().isoformat()


def _digest_already_sent_today(conn, _real_now=None):
    """True if a gate.sent audit row with payload kind=="digest" already
    exists within today's Asia/Almaty calendar day. Reuses gate.py's own
    day-bounds helper so "today" always agrees with budget_spent_today's
    definition, even though budget_spent_today itself EXCLUDES digest
    rows from its own count (see gate.py's docstring) -- the two queries
    look at the same window for different reasons.

    The day window is always anchored to the REAL wall clock, never to
    digest()'s own now_utc override: audit.log() stamps every row's
    ts_utc from datetime.now() regardless of any now_utc a caller passes
    elsewhere (see audit.py), so the gate.sent row this guard is looking
    for is always real-clock-stamped. Deriving the window from now_utc
    instead would let a live run's --now override (or any drift between
    now_utc and the real clock) point the guard at the wrong day,
    causing a double-send (window misses today's real row) or a wrong
    skip (window matches a stale row it shouldn't).

    _real_now is a test-only injection point (mirrors _fetch_weather's
    role for the weather fetch): None means "use the actual current
    time", the normal production behaviour.
    """
    real_now = _real_now or _now()
    from_utc, to_utc = gate._almaty_day_utc_bounds(real_now)
    rows = conn.execute(
        "SELECT payload FROM audit_log WHERE kind='gate.sent' "
        "AND ts_utc >= ? AND ts_utc < ?",
        (from_utc, to_utc),
    ).fetchall()
    return any(json.loads(r["payload"]).get("kind") == "digest" for r in rows)


def _fallback_weather_line(wx):
    if wx is None:
        return None
    today = wx["today"]
    tmin = round(today["tmin"])
    tmax = round(today["tmax"])
    line = f"Сегодня {tmin}…{tmax}°C"
    precip = today.get("precip_mm") or 0
    if precip > 0:
        line += f", возможны осадки ({precip:g} мм)"
    else:
        line += ", без осадков"
    return line


def _fallback_event_line(event):
    local_time = datetime.fromisoformat(event["start_local"]).strftime("%H:%M")
    return f"{local_time} {event['title']}"


def _build_digest_fallback(date_local, wx, events):
    """Deterministic fallback text (no LLM involved) -- used verbatim
    when gate.deliver's rewrite fails, and as the source raw material the
    rewrite is asked to restyle otherwise. Sections: date header, weather
    line (omitted entirely when wx is None), event list (or "no events"),
    and the fixed closing question. Comfortably under the 900-char digest
    ceiling for a normal day's event count; gate.deliver's own length
    ceiling (shorten-retry, then send-as-is with long=True) is the
    backstop for the pathological case, not this function.
    """
    lines = [f"Доброе утро! Сегодня {date_local}."]
    weather_line = _fallback_weather_line(wx)
    if weather_line:
        lines.append(weather_line)
    if events:
        lines.append("Планы на сегодня:")
        lines.extend(_fallback_event_line(e) for e in events)
    else:
        lines.append("Событий нет.")
    lines.append(DIGEST_QUESTION)
    return "\n".join(lines)


def digest(conn, cfg=None, now_utc=None, _fetch_weather=None, _real_now=None):
    """Run one digest tick: today's weather + today's active events + a
    fixed closing question, delivered as a single gate.deliver(kind=
    "digest", force=True) call -- outside the daily budget and meant to
    fire once, right after quiet hours end, via its own systemd timer
    (Task 8).

    Dup guard (checked FIRST, before fetching weather or querying
    events): if a gate.sent audit row with payload kind=="digest" already
    exists for today's Asia/Almaty calendar day -- i.e. the scheduled
    digest already went out and this is a re-run (manual retry, a second
    timer fire, etc.) -- returns {"skipped": "already_sent", "date_local"}
    and audits tick.digest with that same payload, without touching
    weather/cal or calling gate.deliver again. The guard's day window is
    always anchored to the REAL wall clock (see
    _digest_already_sent_today's docstring), NOT to now_utc below --
    now_utc only drives date_local and which events count as "today"'s.
    _real_now is a test-only injection point for that real-clock window
    (mirrors _fetch_weather); production always leaves it as None.

    weather: (_fetch_weather or weather.fetch_almaty)(). None is a
    legitimate outcome (Open-Meteo unreachable) -- the weather section is
    simply omitted from raw/fallback, it never blocks the digest.

    events: cal.day(conn, today_almaty) -- ACTIVE-ONLY by design (Global
    Constraints call this out explicitly for the digest, as opposed to
    contexts that need cal.list_range(status=None)): a cancelled/done
    event has nothing left to plan around, only what's still actually on
    today's plan belongs in a "what's the plan today" message. Each event
    item includes event_id so a later ack/cancel-from-context (Task 9)
    can address it.

    question: the fixed closing ask (DIGEST_QUESTION) goes into raw
    verbatim -- not a bare flag -- since raw is the JSON the LLM rewrite
    actually reads; the same constant is also the last line of the
    deterministic human_fallback, so the two can never phrase the ask
    differently. This also doubles as the day's planning intake per the
    digest-doubles-as-intake spec amendment: replying in chat with
    today's plans is the expected next turn.

    Always audits tick.digest -- both the dup-guard skip and the normal
    path -- with {status, date_local, weather_present, n_events} (or
    {skipped: "already_sent", date_local}) so an admin can see what
    happened without cross-referencing gate.sent.

    Returns the same dict that was audited.
    """
    now = now_utc or _now()
    cfg = cfg if cfg is not None else gate.load_config()
    date_local = _today_almaty(now)

    if _digest_already_sent_today(conn, _real_now=_real_now):
        summary = {"skipped": "already_sent", "date_local": date_local}
        audit.log(conn, "tick.digest", summary)
        conn.commit()
        return summary

    fetch = _fetch_weather or weather.fetch_almaty
    wx = fetch()
    events = cal.day(conn, date_local)

    event_list = []
    for e in events:
        item = {"event_id": e["id"], "title": e["title"],
                "start_local": e["start_local"]}
        if e["place"]:
            item["place_name"] = e["place"]["name"]
        event_list.append(item)

    raw = {
        "kind": "digest",
        "date_local": date_local,
        "weather": wx,
        "events": event_list,
        "question": DIGEST_QUESTION,
    }
    human_fallback = _build_digest_fallback(date_local, wx, event_list)

    status = gate.deliver(conn, "digest", raw, human_fallback, cfg,
                           force=True, now_utc=now)

    summary = {"status": status, "date_local": date_local,
               "weather_present": wx is not None, "n_events": len(event_list)}
    audit.log(conn, "tick.digest", summary)
    conn.commit()
    return summary
