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

fam tick meds-gen (Phase 5 Task 3) is simpler still: no gate.deliver call
at all, so nothing it does is irreversible in the digest/reminders sense
-- a crash mid-run just loses that run's uncommitted work, and the very
next run (whether that's a retry or tomorrow's own scheduled fire)
regenerates it from scratch because generation is idempotent on
(med_id, plan_ts_utc). It owns one commit at the very end, same as
digest.
"""
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fam import audit, cal, gate, meds, plans, rem, road, shopping, weather

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

# 3b Task 6: the evening follow-up's fixed closing question -- same
# role as DIGEST_QUESTION (goes into raw["question"] AND the
# deterministic human_fallback from this single constant).
FOLLOWUP_QUESTION = "Как прошло, что удалось из планов?"


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

        # 3b Task 4: "по пути" -- open plans on the way to this event
        # piggyback onto the SAME reminder (no new message, no extra
        # budget spend). Only leave/prepare stages (the ones already
        # about departure/getting ready) qualify, and only for events
        # with a place (plans.match_enroute's geo reason needs a route
        # to compare against; a place-less event can still match via
        # "person", but scoping this to reminder["kind"] AND a place
        # keeps the call rare and avoids surprising a caller-with-a-
        # meeting reminder). plans.match_enroute is called at most ONCE
        # here -- not on every minute tick -- because it may internally
        # call road.route_for_event (TomTom, daily-capped); this call
        # site is reached only for a reminder that is actually due and
        # about to be delivered this tick.
        # Final review Finding 2: match_enroute may call road.route_for_event
        # (TomTom) and must never be allowed to take down the whole minute
        # tick -- an exception here is swallowed and audited as
        # tick.error/enroute; the reminder itself still gets delivered
        # below, just without the "по пути" piggyback.
        # B1: compute the route ONCE per event per tick and share it
        # between plans.match_enroute and shopping.match_enroute below --
        # both may otherwise independently call road.route_for_event
        # (TomTom, daily-capped) for the exact same event, doubling the
        # TomTom spend for no benefit. Same try/except discipline as the
        # matchers themselves: a failure here degrades to route=None, so
        # each matcher falls back to computing (or skipping) its own
        # route exactly as it did before this change.
        route = None
        if reminder["kind"] in ("leave", "prepare") and event["place"]:
            try:
                route = road.route_for_event(conn, event, cfg, now_utc=now)
            except Exception as e:
                audit.log(conn, "tick.error",
                          {"where": "route_for_event", "error": str(e)[:200]})
                route = None

        if reminder["kind"] in ("leave", "prepare") and event["place"]:
            try:
                matches = plans.match_enroute(conn, event, cfg, now_utc=now,
                                               route=route)
            except Exception as e:
                audit.log(conn, "tick.error",
                          {"where": "enroute", "error": str(e)[:200]})
                matches = []
            if matches:
                max_items = cfg.get("enroute_max_items", 2)
                chosen = matches[:max_items]
                titles = [m["plan"]["title"] for m in chosen]
                raw["enroute"] = "По пути: " + "; ".join(titles)
                audit.log(conn, "tick.enroute",
                          {"event_id": event["id"],
                           "plan_ids": [m["plan"]["id"] for m in chosen]})

        # Phase 5 Task 6: "заодно" -- a categorized place (grocery/
        # pharmacy, places.category) on the way to this event, with a
        # non-empty matching shopping list, piggybacks onto the SAME
        # reminder -- reuse of the plans-enroute block directly above:
        # same guard (reminder["kind"] in ("leave","prepare") and
        # event["place"]), same call-at-most-once-per-delivered-reminder
        # discipline (shopping.match_enroute may itself call
        # road.route_for_event, TomTom, daily-capped), no new message
        # (no extra gate.deliver call => budget doesn't grow). A failure
        # here is swallowed and audited as tick.error/shop_enroute,
        # mirroring the enroute guard's own try/except -- it must never
        # take down reminder delivery. B2: BOTH categorized matches
        # (grocery AND pharmacy) are surfaced when both are in-corridor --
        # not just shopping.match_enroute's first result -- joined into
        # one "; "-separated line so the reminder stays a single message
        # (no new gate.deliver call => budget doesn't grow).
        if reminder["kind"] in ("leave", "prepare") and event["place"]:
            try:
                shop_matches = shopping.match_enroute(conn, event, cfg, now_utc=now,
                                                       route=route)
            except Exception as e:
                audit.log(conn, "tick.error",
                          {"where": "shop_enroute", "error": str(e)[:200]})
                shop_matches = []
            if shop_matches:
                raw["shop_enroute"] = "Заодно: " + "; ".join(
                    f"{match['place']['name']} — " + ", ".join(match["items"])
                    for match in shop_matches
                )
                for match in shop_matches:
                    audit.log(conn, "tick.shop_enroute",
                              {"event_id": event["id"],
                               "place_id": match["place"]["id"],
                               "n_items": len(match["items"])})

        # Phase 4 Task 8: departure hooks (fuel-low + cabin-temp warmup
        # suggestion) piggyback onto the SAME leave/prepare reminder --
        # same guard shape as the enroute/shop_enroute blocks above (no
        # new message, no extra budget spend). Only for car events; a
        # failure here is swallowed and audited as tick.error/car_hooks,
        # mirroring the enroute guard's own try/except.
        if reminder["kind"] in ("leave", "prepare") and event["transport"] == "car":
            from fam import car as carmod
            try:
                car_hooks = carmod.departure_hooks(conn, event, cfg)
            except Exception as e:
                car_hooks = []
                audit.log(conn, "tick.error", {"where": "car_hooks", "error": str(e)[:200]})
            if car_hooks:
                raw["car"] = "; ".join(car_hooks)
                audit.log(conn, "tick.car_hook",
                          {"event_id": event.get("id"), "hooks": car_hooks})

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

    # Final review Finding 2: an exception from _followup must not take
    # down the reminders tick that already ran above it -- swallow and
    # audit, same contract as the enroute guard.
    try:
        _followup(conn, now_utc=now, cfg=cfg)
    except Exception as e:
        audit.log(conn, "tick.error",
                  {"where": "followup", "error": str(e)[:200]})
        conn.commit()

    # Phase 5 Task 4: persistent medication reminder series -- see
    # _meds_series's own docstring. Runs last, after event reminders and
    # the evening follow-up, wrapped in its own try/except (same guard
    # pattern as the enroute/followup hooks above) so a failure here
    # never sinks a tick that already delivered ordinary reminders/
    # followup this same run.
    try:
        _meds_series(conn, now_utc=now, cfg=cfg)
    except Exception as e:
        audit.log(conn, "tick.error", {"where": "meds", "error": str(e)[:200]})
        conn.commit()

    # 6b: edge-triggered bridge-down alert (health owns the meta flag; we
    # own the commit, same as every other write in this tick).
    try:
        from fam import health as _health
        from fam import gate as _gate
        _health.maybe_alert_readiness(conn, _gate.load_config())
    except Exception:                                # noqa: BLE001 -- never fail the tick
        pass

    audit.log(conn, "tick.reminders", counts)
    conn.commit()
    return counts


def _meds_series(conn, now_utc, cfg):
    """Phase 5 Task 4: persistent medication reminder series -- the
    minute-tick counterpart to meds_gen's once-a-day generation. Called
    from the very end of reminders(); the caller wraps this whole call in
    a try/except (module docstring's guard-per-hook pattern), so any
    exception here is audited as tick.error{"where":"meds"} rather than
    sinking the tick.

    Selects every med_intakes row still status='pending' whose
    series_next_utc is set (NOT NULL) and <= now -- a row is only ever a
    series-candidate while series_next_utc is non-NULL, so an
    out-of-stock intake that already cleared its own series_next_utc to
    NULL (below) is naturally excluded from every later tick this same
    day; it will next be touched by meds_gen's midnight missed-closeout,
    not by this function again.

    Per due row:
      - in_quiet_hours(now, cfg) -> skip entirely: no send,
        series_next_utc left untouched. This is a PAUSE, not a cancel --
        the next tick at/after quiet hours end will find this same row
        still due (series_next_utc never moved forward) and try again.
      - meds.get(med_id) is None, OR the med has enabled=0 (5 T9 final
        review, FIX-1): skip entirely, no send -- "stop reminding me
        about X" (skill contract: fam meds edit <id> --enabled 0) must
        actually stop the series, not just the digest. series_next_utc
        is cleared to NULL (same "never nag again today" cleanup the
        out-of-stock branch already does) so a later tick this same day
        doesn't re-check meds.get for this row again; status is left
        pending -- meds_gen's midnight closeout still marks an un-acked
        dose "missed" the same as any other pending row. Audited as
        tick.med: {intake_id, mode:"disabled"}.
      - meds.get(med_id): if remaining is not None and remaining == 0 ->
        exactly ONE "go buy this" notice (mode="out_of_stock"), then
        series_next_utc is cleared to NULL so this same dose never nags
        again today -- status is left pending; meds_gen's midnight
        closeout is what eventually marks an un-acked dose "missed".
      - otherwise -> an ordinary "take this now" reminder (mode="take"),
        delivered via gate.deliver(force=True) -- Denis's decision:
        medication reminders bypass quiet hours and the daily budget
        entirely (see gate.budget_spent_today's kind=="med" exclusion;
        the quiet-hours check above is this function's OWN pause logic,
        separate from gate.deliver's own quiet-hours gate). Regardless
        of gate.deliver's returned status (sent/quiet/budget/error),
        series_next_utc advances to now + cfg["med_repeat_min"] (default
        45) minutes -- the next escalation in this dose's own persistent
        series. status is left pending either way; T5 owns the ack that
        finally closes it.

    Every UPDATE that touches series_next_utc (disabled/out_of_stock/
    take branches alike) is qualified with AND status='pending' (5 T9
    final review, FIX-2 / Backlog #5): between this function's own
    SELECT above and its per-row UPDATE, gate.deliver's send is real
    wall-clock work, wide enough a window for a concurrent ack (T5's
    meds.take/skip, e.g. via the amina-fam skill) to flip status out
    from under this loop. Without the status filter, this loop's UPDATE
    would still fire on an already-acked row and clobber the ack's own
    series_next_utc=NULL back to a stale value -- a data-hygiene
    invariant violation (every non-pending row's docstring-promised
    state is series_next_utc IS NULL) even though it happens not to
    resurface the row in a later tick's due-selection (that query also
    filters status='pending').

    Every row is audited as tick.med: {intake_id, mode:"disabled"} for
    the disabled/unknown-med branch, {intake_id, mode:"out_of_stock"}
    for the out-of-stock branch, {intake_id, mode:"take", status} for
    the ordinary branch -- "status" is gate.deliver's return value, kept
    here (unlike out_of_stock, whose own delivery outcome isn't the
    operationally interesting fact -- the series being paused for the
    day is) since a persistent "quiet"/"budget"/"error" for a live dose
    is exactly the kind of thing worth seeing in the audit trail.

    Commits once per due row (mirrors the per-reminder commit in the main
    loop above -- gate.deliver's send is an irreversible real-world side
    effect, so each row's outcome is narrowed to its own transaction).
    """
    now_dt = _parse_utc(now_utc)
    due = conn.execute(
        "SELECT * FROM med_intakes WHERE status='pending' "
        "AND series_next_utc IS NOT NULL AND series_next_utc <= ? "
        "ORDER BY series_next_utc",
        (now_utc,),
    ).fetchall()

    for row in due:
        intake_id = row["id"]

        if gate.in_quiet_hours(now_utc, cfg):
            continue

        med = meds.get(conn, row["med_id"])

        if med is None or not med.get("enabled"):
            conn.execute(
                "UPDATE med_intakes SET series_next_utc=NULL "
                "WHERE id=? AND status='pending'",
                (intake_id,),
            )
            audit.log(conn, "tick.med",
                      {"intake_id": intake_id, "mode": "disabled"})
            conn.commit()
            continue

        name = med["name"]
        dose = med.get("dose")

        if med.get("remaining") is not None and med["remaining"] == 0:
            raw = {"mode": "out_of_stock", "name": name, "dose": dose}
            human_fallback = f"Заканчивается {name} — надо купить."
            gate.deliver(conn, "med", raw, human_fallback, cfg, force=True,
                          now_utc=now_utc)
            conn.execute(
                "UPDATE med_intakes SET series_next_utc=NULL "
                "WHERE id=? AND status='pending'",
                (intake_id,),
            )
            audit.log(conn, "tick.med",
                      {"intake_id": intake_id, "mode": "out_of_stock"})
        else:
            raw = {"mode": "take", "name": name, "dose": dose}
            human_fallback = f"Пора принять {name}" + (
                f" ({dose})" if dose else "")
            status = gate.deliver(conn, "med", raw, human_fallback, cfg,
                                   force=True, now_utc=now_utc)
            repeat_min = cfg.get("med_repeat_min", 45)
            next_utc = (now_dt + timedelta(minutes=repeat_min)).isoformat(
                timespec="seconds")
            conn.execute(
                "UPDATE med_intakes SET series_next_utc=? "
                "WHERE id=? AND status='pending'",
                (next_utc, intake_id),
            )
            audit.log(conn, "tick.med",
                      {"intake_id": intake_id, "mode": "take",
                       "status": status})
        conn.commit()


def _today_almaty(now_utc):
    return _parse_utc(now_utc).astimezone(ALMATY).date().isoformat()


def _followup_related_plans(conn, event, open_plans):
    """Open plans related to this event: attached_event_id == event.id,
    OR plan.person_id among the event's participants. No geo/TomTom
    matching here (Denis's decision, 3b Task 6) -- attached + person is
    enough for an evening recap, and this must never trigger a live
    routing call. `open_plans` is passed in (plans.list_open(conn))
    rather than re-fetched per event, since _followup calls this once
    per outbound event of the day.
    """
    participant_ids = {p["id"] for p in event.get("participants", [])}
    related = []
    for plan in open_plans:
        if plan.get("attached_event_id") == event["id"]:
            related.append(plan)
        elif participant_ids and plan.get("person_id") in participant_ids:
            related.append(plan)
    return related


def _followup_day_bounds_utc(date_local):
    """UTC [from, to) bounds for date_local's Asia/Almaty calendar day --
    same math as cal.day, duplicated here (Task 8 acceptance fix 2)
    because _followup needs active AND done events for the day, whereas
    cal.day itself is hardcoded to status="active" and cal.list_range's
    only other option (status=None) is EVERY status, including
    cancelled. Computing the bounds here and calling cal.list_range with
    status=None, then filtering client-side to {active, done}, is the
    smallest change that doesn't touch cal.py's contract for its other
    callers (digest, etc. still want active-only "what's still on the
    plan").
    """
    y, m, d = (int(x) for x in date_local.split("-"))
    start_of_day = datetime(y, m, d, 0, 0, 0, tzinfo=ALMATY)
    end_of_day = start_of_day + timedelta(days=1)
    from_utc = start_of_day.astimezone(timezone.utc).isoformat(timespec="seconds")
    to_utc = end_of_day.astimezone(timezone.utc).isoformat(timespec="seconds")
    return from_utc, to_utc


def _followup(conn, now_utc, cfg):
    """3b Task 6: the evening combined follow-up.

    Fires at most once per Asia/Almaty calendar day, in the first
    minute-tick at or after cfg["followup_local_time"] (default
    "20:00"). Dedup is a meta row keyed "followup_sent:<date_local>",
    checked first. Unlike the original 3b Task 6 cut, meta is set on the
    "nothing to say" outcomes (no_events, no_plans) AND on "sent", but
    NOT on a real gate.deliver refusal (quiet/budget/error) -- Task 8
    acceptance fix 1 (Denis, live sweep): the original "set on every
    outcome" contract meant a quiet/budget/error refusal silently lost
    the whole day's follow-up, since the next tick would see meta
    already set and never re-evaluate. Leaving meta unset on a refusal
    makes every subsequent minute-tick this same Asia/Almaty day retry
    the identical send (outbound events + related plans are re-derived
    fresh each time, so a plan closed/event added in between is picked
    up too) until either gate.deliver finally returns "sent", or quiet
    hours begin -- at which point gate.deliver itself keeps returning
    "quiet" every tick (followup is not reminder-exempt from quiet
    hours, see gate.deliver's own docstring), so it never spuriously
    recovers after 21:30 anyway. The no_events/no_plans "silence"
    outcomes still set meta immediately: there is nothing there to
    retry into existing, so re-checking every minute for the rest of
    the day would be pure waste -- mirrors _digest_already_sent_today's
    role but keyed in `meta` rather than derived from gate.sent audit
    rows, since a followup's "nothing to say" outcome never calls
    gate.deliver at all and so leaves no gate.sent row to key off of.

    Outbound events: today's events (Task 8 acceptance fix 2: status
    active OR done -- see _followup_day_bounds_utc; an event that was
    actually driven to and marked done must still surface in the
    evening recap, not just one still sitting "active" because nobody
    checked it off) with a resolved place AND start_utc already in the
    past relative to now_utc -- "already left for it", not "about to".
    Related plans: every open plan attached to one of those events, or
    matching one of their participants by person_id (see
    _followup_related_plans) -- deliberately NO geo/enroute matching
    (no live TomTom call from an unattended tick, Denis's decision).

    No outbound events, or outbound events but zero related plans ->
    silence: meta is still set (so this is never re-checked today) and
    tick.followup is still audited, with status "no_events"/"no_plans"
    respectively -- same "record the null outcome" contract as
    tick.reminders' all-zero run.

    Otherwise: ONE gate.deliver(kind="followup", force=False) call per
    tick -- an ordinary budget unit, quiet hours respected exactly like
    any other non-reminder kind (gate.deliver's own gate, not duplicated
    here). raw carries the event list and the related plans' titles;
    FOLLOWUP_QUESTION goes into raw["question"] AND is the last line of
    the deterministic human_fallback (same "one constant, can't drift"
    pattern as DIGEST_QUESTION). tick.followup is audited every tick
    with the gate.deliver outcome ("sent"/"quiet"/"budget"/"error") as
    status regardless of whether it was actually delivered, but meta is
    only written for "sent" (see above) -- a quiet/budget/error outcome
    IS retried by a later tick the same day, unlike digest's own
    force=True/once-a-day contract.

    Returns the status string ("no_events", "no_plans", or gate.deliver's
    outcome), mainly for tests; reminders() itself ignores the return
    value -- there is no separate followup bucket in tick.reminders'
    counts dict, this is audited under its own tick.followup kind
    instead.
    """
    now_dt = _parse_utc(now_utc)
    local_dt = now_dt.astimezone(ALMATY)
    date_local = local_dt.date().isoformat()
    meta_key = f"followup_sent:{date_local}"

    already = conn.execute(
        "SELECT value FROM meta WHERE key=?", (meta_key,)
    ).fetchone()
    if already is not None:
        return None

    followup_local_time = cfg.get("followup_local_time", "20:00")
    hh, mm = (int(x) for x in followup_local_time.split(":"))
    threshold_dt = local_dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if local_dt < threshold_dt:
        return None

    from_utc, to_utc = _followup_day_bounds_utc(date_local)
    events = cal.list_range(conn, from_utc, to_utc, status=None)
    outbound_events = [
        e for e in events
        if e.get("status") in ("active", "done")
        and e.get("place") is not None
        and _parse_utc(e["start_utc"]) < now_dt
    ]

    related_plans = []
    if outbound_events:
        open_plans = plans.list_open(conn)
        seen_ids = set()
        for event in outbound_events:
            for plan in _followup_related_plans(conn, event, open_plans):
                if plan["id"] not in seen_ids:
                    seen_ids.add(plan["id"])
                    related_plans.append(plan)

    if not outbound_events:
        status = "no_events"
    elif not related_plans:
        status = "no_plans"
    else:
        raw = {
            "kind": "followup",
            "date_local": date_local,
            "events": [
                {"event_id": e["id"], "title": e["title"],
                 "start_local": e["start_local"]}
                for e in outbound_events
            ],
            "plans": [{"plan_id": p["id"], "title": p["title"]}
                      for p in related_plans],
            "question": FOLLOWUP_QUESTION,
        }
        lines = ["Открытые планы:"]
        lines.extend(p["title"] for p in related_plans)
        lines.append(FOLLOWUP_QUESTION)
        human_fallback = "\n".join(lines)

        status = gate.deliver(conn, "followup", raw, human_fallback, cfg,
                               now_utc=now_utc)

    # Task 8 acceptance fix 1: only a "sent" or a "nothing to say" outcome
    # (no_events/no_plans) is a permanent verdict for today -- a real
    # gate.deliver refusal (quiet/budget/error) leaves meta unset so the
    # next minute tick retries (see this function's docstring).
    if status in ("no_events", "no_plans", "sent"):
        conn.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
            (meta_key, now_utc),
        )
    audit.log(conn, "tick.followup",
              {"date_local": date_local, "status": status,
               "n_events": len(outbound_events),
               "n_plans": len(related_plans)})
    conn.commit()
    return status


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


def _burning_plans(conn, cfg, date_local):
    """Open, not-yet-attached plans whose deadline is within
    cfg["plan_deadline_horizon_days"] (default 3) days of date_local,
    inclusive -- 3b Task 5. attached_event_id is already set means the
    plan has a calendar slot and no longer needs a digest nudge (mirrors
    plans.match_enroute's own "not yet attached" filter). A deadline in
    the past (relative to date_local) is still included, marked
    "overdue": True rather than silently dropped -- a missed deadline is
    exactly the kind of thing a digest should surface, not hide.

    Returns a list of {"plan_id", "title", "deadline", "overdue"} dicts,
    ordered like plans.list_open() (deadline ascending, NULLs -- already
    excluded here -- last).

    Defense-in-depth (Final review Finding 1): plans.add() now validates
    deadline before insert, but a malformed deadline could still reach
    this table via some other write path (direct SQL, a future caller
    that bypasses plans.add). A plan whose deadline fails
    date.fromisoformat is skipped here rather than raising -- one bad
    row must not crash the whole digest tick -- and audited as
    plan.bad_deadline so it stays visible for cleanup instead of just
    silently vanishing from the digest.
    """
    horizon_days = cfg.get("plan_deadline_horizon_days", 3)
    today = date.fromisoformat(date_local)
    cutoff = today + timedelta(days=horizon_days)

    burning = []
    for plan in plans.list_open(conn):
        if plan.get("attached_event_id") is not None:
            continue
        deadline = plan.get("deadline")
        if deadline is None:
            continue
        try:
            deadline_date = date.fromisoformat(deadline)
        except (TypeError, ValueError):
            audit.log(conn, "plan.bad_deadline",
                      {"plan_id": plan["id"], "deadline": deadline})
            continue
        if deadline_date > cutoff:
            continue
        burning.append({
            "plan_id": plan["id"],
            "title": plan["title"],
            "deadline": deadline,
            "overdue": deadline_date < today,
        })
    return burning


def _busy_two_days(conn, date_local):
    """Compact time+title list of today's and tomorrow's active events
    (Asia/Almaty calendar days) -- 3b Task 5. Feeds the LLM rewrite raw
    material to propose a free slot for a burning plan in the digest
    text itself (Denis's decision: the MODEL formulates the slot, this
    function only supplies the busy facts it reasons over). Never
    appears in the deterministic fallback -- see _build_digest_fallback.
    """
    today = date.fromisoformat(date_local)
    tomorrow_local = (today + timedelta(days=1)).isoformat()

    busy = []
    for e in cal.day(conn, date_local) + cal.day(conn, tomorrow_local):
        busy.append({"start_local": e["start_local"], "title": e["title"]})
    return busy


def _fallback_plan_line(plan):
    line = f"{plan['title']} — до {plan['deadline']}"
    if plan["overdue"]:
        line += " (просрочено)"
    return line


def _meds_digest(conn, date_local):
    """The digest's medication *exceptions* -- yesterday's missed doses
    and low-stock meds. Two independent lists, each queried separately
    (no single "meds status" concept ties them together).

    Routine "today's planned intakes" are deliberately NOT surfaced here
    (originally the digest's third list): those doses are reminded by
    their own minute-tick during the day, so repeating the day's schedule
    in the morning message is noise -- medication belongs in the digest
    only as an exception the user should act on, not as a daily roster.

    missed_yesterday: med_intakes rows with status='missed' whose
    plan_ts_utc falls in the day BEFORE date_local, joined to an enabled
    med (5 T9 final review, FIX-1: a disabled med must not surface just
    because meds_gen generated the row before it was disabled -- "stop
    reminding me about X" means stop everywhere). meds_gen's midnight
    closeout is what actually flips a stale pending row to 'missed', so
    this is a straight read of that outcome, not a re-derivation.
    {name} per row.

    low_stock: meds.list(conn) (enabled meds only, mirrors every other
    caller of the module) filtered by the SAME low-stock formula
    meds.take's restock trigger uses (T5): remaining is not None and
    remaining <= threshold, guarded so the default threshold=0 only
    fires once remaining has actually hit 0 (threshold>0 or
    remaining==0) -- an untracked med (remaining=None) never appears
    here. {name, remaining} per med.

    Returns {"missed_yesterday": [...], "low_stock": [...]} -- both keys
    always present, each an empty list when there is nothing to report;
    digest() omits the whole "meds" key from raw (and
    _build_digest_fallback its section) when both are empty.
    """
    yesterday_local = (
        date.fromisoformat(date_local) - timedelta(days=1)
    ).isoformat()
    yest_from, yest_to = _followup_day_bounds_utc(yesterday_local)

    missed_rows = conn.execute(
        "SELECT m.name AS name FROM med_intakes i "
        "JOIN meds m ON m.id = i.med_id "
        "WHERE i.status='missed' AND i.plan_ts_utc >= ? AND i.plan_ts_utc < ? "
        "AND m.enabled=1 "
        "ORDER BY i.plan_ts_utc",
        (yest_from, yest_to),
    ).fetchall()
    missed_yesterday = [{"name": row["name"]} for row in missed_rows]

    low_stock = []
    for med in meds.list(conn):
        remaining = med.get("remaining")
        threshold = med.get("threshold", 0)
        if remaining is not None and remaining <= threshold \
                and (threshold > 0 or remaining == 0):
            low_stock.append({"name": med["name"], "remaining": remaining})

    return {"missed_yesterday": missed_yesterday, "low_stock": low_stock}


def _fallback_meds_lines(meds_digest):
    """Deterministic "Лекарства:" section lines for the digest fallback
    -- omitted entirely (returns []) when both of meds_digest's lists are
    empty. Order: yesterday's missed (name), then low-stock ("пора
    купить" -- the literal phrase the task brief specifies, distinct from
    _meds_series's own out-of-stock wording since that's a different,
    immediate-nag message). Routine today's intakes are no longer part of
    the digest at all (see _meds_digest).
    """
    missed = meds_digest.get("missed_yesterday") or []
    low_stock = meds_digest.get("low_stock") or []
    if not (missed or low_stock):
        return []

    lines = ["Лекарства:"]
    lines.extend(f"{item['name']} — пропущено вчера" for item in missed)
    lines.extend(
        f"{item['name']} — пора купить (осталось {item['remaining']})"
        for item in low_stock
    )
    return lines


def _build_digest_fallback(date_local, wx, events, burning_plans=None,
                            meds=None):
    """Deterministic fallback text (no LLM involved) -- used verbatim
    when gate.deliver's rewrite fails, and as the source raw material the
    rewrite is asked to restyle otherwise. Sections: date header, weather
    line (omitted entirely when wx is None), event list (or "no events"),
    burning-plans list (omitted entirely when empty -- 3b Task 5),
    medication list (omitted entirely when today/missed_yesterday/
    low_stock are all empty -- Phase 5 Task 7, see _fallback_meds_lines),
    and the fixed closing question. Deliberately does NOT include a busy-
    today/tomorrow section or propose any slot: slot suggestion is the
    LLM rewrite's job (raw["busy_two_days"] feeds it), this fallback only
    ever states plain facts. Comfortably under the 900-char digest
    ceiling for a normal day's event/plan/meds count; gate.deliver's own
    length ceiling (shorten-retry, then send-as-is with long=True) is the
    backstop for the pathological case, not this function.
    """
    burning_plans = burning_plans or []
    meds = meds or {}
    lines = [f"Доброе утро! Сегодня {date_local}."]
    weather_line = _fallback_weather_line(wx)
    if weather_line:
        lines.append(weather_line)
    if events:
        lines.append("Планы на сегодня:")
        lines.extend(_fallback_event_line(e) for e in events)
    else:
        lines.append("Событий нет.")
    if burning_plans:
        lines.append("Горящие планы:")
        lines.extend(_fallback_plan_line(p) for p in burning_plans)
    lines.extend(_fallback_meds_lines(meds))
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

    meds: _meds_digest(conn, date_local) -- yesterday's missed doses and
    low-stock meds only (routine today's intakes are no longer surfaced;
    see _meds_digest). raw["meds"] is set ONLY when at least one of those
    exception lists is non-empty; on an ordinary day it is absent from
    raw entirely, so the rewrite never mentions medication when there is
    nothing to act on.

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

    burning_plans = _burning_plans(conn, cfg, date_local)
    busy_two_days = _busy_two_days(conn, date_local)
    meds_digest = _meds_digest(conn, date_local)

    # Empty/unavailable sections are dropped from raw entirely rather
    # than sent as null/[] -- the rewrite prompt tells the LLM to reflect
    # every field it IS given, so a present-but-empty field would make it
    # narrate the absence ("Погода не указана", "без ... лекарств").
    # weather: omitted when unavailable. meds: omitted unless there is an
    # exception to report (missed dose / low stock). events/burning_plans
    # stay always-present, so "Событий нет." is still stated by design.
    raw = {
        "kind": "digest",
        "date_local": date_local,
        "events": event_list,
        "burning_plans": burning_plans,
        "busy_two_days": busy_two_days,
        "question": DIGEST_QUESTION,
    }
    if wx is not None:
        raw["weather"] = wx
    if meds_digest["missed_yesterday"] or meds_digest["low_stock"]:
        raw["meds"] = meds_digest
    human_fallback = _build_digest_fallback(date_local, wx, event_list,
                                             burning_plans, meds_digest)

    status = gate.deliver(conn, "digest", raw, human_fallback, cfg,
                           force=True, now_utc=now)

    summary = {"status": status, "date_local": date_local,
               "weather_present": wx is not None, "n_events": len(event_list)}
    audit.log(conn, "tick.digest", summary)
    conn.commit()
    return summary


def meds_gen(conn, now_utc=None, cfg=None):
    """Phase 5 Task 3: midnight Asia/Almaty generation of today's
    med_intakes rows, plus closing out yesterday's still-pending rows as
    missed. Fires once a day via its own systemd timer (OnCalendar
    19:00 UTC = 00:00 Almaty, Task 3 step 5) -- NOT a minute-tick like
    reminders(), so there is no due-selection here, only "what does
    today look like".

    cfg is accepted (not loaded via gate.load_config() when omitted, the
    way digest()/reminders() do) purely for signature parity with the
    other tick entry points -- meds_gen has nothing today that reads
    config (no gate.deliver call, no quiet-hours/budget gate to
    respect), so it is accepted and otherwise ignored.

    now = now_utc or _now() drives BOTH date_local (which Almaty
    calendar day is "today") and the missed-closeout boundary below --
    unlike rem.regenerate's created_at, which always stamps the real
    wall clock separately (see created_at = _now() below), because that
    bookkeeping timestamp has no business logic riding on it the way
    date_local does.

    Step 1 -- generate: for every enabled med (meds.list(conn) already
    filters disabled=0 by default), for every "HH:MM" in its `times`,
    build plan_ts_utc = that HH:MM on TODAY's Almaty calendar date,
    converted to UTC. Skipped (not inserted) if a med_intakes row for
    this exact (med_id, plan_ts_utc) already exists -- the idempotency
    check a re-run (retry, or two timer fires close together) relies on
    to avoid duplicate intakes for the same scheduled dose. A fresh
    insert is status=pending, taken_ts_utc=NULL, series_next_utc=
    plan_ts_utc (T4's persistent-series minute-tick is what advances
    series_next_utc later; at creation it always starts equal to its
    own plan_ts_utc). One med whose times somehow fail to parse (e.g. a
    row that bypassed meds._validate_times via direct DB surgery) is
    caught, audited as tick.error {where: "meds_gen", med_id, error},
    and skipped -- same "one bad item must not sink the whole tick"
    contract as road_recompute's per-event try/except (see that
    function's docstring) -- rather than letting one malformed med's
    exception take down generation for every other med this run.

    Step 2 -- close yesterday's tail: every med_intakes row still
    status=pending with plan_ts_utc BEFORE the start of today's Almaty
    calendar day (computed the same way _followup_day_bounds_utc
    computes its from_utc, duplicated inline here rather than reusing
    that helper since meds_gen only ever needs the single lower bound,
    not a [from, to) pair) is flipped to status=missed. A pending row
    from EARLIER today (e.g. an 08:00 dose not yet acked by the time
    this tick runs, which it never does since this only runs once at
    midnight -- but a manually-backdated or test-inserted row could
    still land there) is left alone; only taken/skipped/missed rows are
    already-closed and are never revisited either way (the SQL filters
    on status='pending').

    ALWAYS audits tick.meds_gen {generated, missed} -- including an
    all-zero run, so "nothing to generate, nothing stale" is a recorded
    fact rather than indistinguishable from "the tick didn't run",
    mirroring tick.reminders'/tick.digest's own always-audit contract.

    Owns a single commit at the very end (see module docstring) --
    generation is idempotent and closing the tail is naturally
    re-derivable, so there is no per-item blast-radius reason to commit
    more often than that, unlike reminders()'s per-item gate.deliver
    commits.

    Returns the same {generated, missed} dict that was audited.
    """
    now = now_utc or _now()
    date_local = _today_almaty(now)
    year, month, day = (int(x) for x in date_local.split("-"))

    # Start of TODAY's Almaty calendar day, in UTC -- the lower bound
    # below which a still-pending intake is yesterday's (or older)
    # unacknowledged tail. Same construction as
    # _followup_day_bounds_utc's from_utc, inlined here since only the
    # single lower bound is needed (see docstring above).
    start_of_today_local = datetime(year, month, day, 0, 0, 0, tzinfo=ALMATY)
    start_of_today_utc = start_of_today_local.astimezone(timezone.utc).isoformat(
        timespec="seconds")

    generated = 0
    for med in meds.list(conn):
        try:
            for hhmm in med["times"]:
                hour, minute = (int(x) for x in hhmm.split(":"))
                plan_dt_local = datetime(year, month, day, hour, minute, 0,
                                          tzinfo=ALMATY)
                plan_ts_utc = plan_dt_local.astimezone(timezone.utc).isoformat(
                    timespec="seconds")

                existing = conn.execute(
                    "SELECT 1 FROM med_intakes WHERE med_id=? AND plan_ts_utc=?",
                    (med["id"], plan_ts_utc),
                ).fetchone()
                if existing is not None:
                    continue

                created_at = _now()
                try:
                    conn.execute(
                        "INSERT INTO med_intakes(med_id, plan_ts_utc, "
                        "taken_ts_utc, status, series_next_utc, created_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (med["id"], plan_ts_utc, None, "pending", plan_ts_utc,
                         created_at),
                    )
                except sqlite3.IntegrityError:
                    # idx_med_intakes_med_plan (db.py SCHEMA, 5-T3 review
                    # round 1) backstops the SELECT-existing check above
                    # against its own TOCTOU race -- two overlapping
                    # meds_gen runs could both pass the SELECT before
                    # either INSERTs. Same outcome as the existing-row
                    # branch above: this dose was already generated, so
                    # it is skipped rather than counted or treated as the
                    # per-med error the outer except below is for.
                    continue
                generated += 1
        except Exception as e:
            audit.log(conn, "tick.error",
                      {"where": "meds_gen", "med_id": med.get("id"),
                       "error": str(e)[:200]})

    missed_ids = [
        row["id"] for row in conn.execute(
            "SELECT id FROM med_intakes WHERE status='pending' "
            "AND plan_ts_utc < ?",
            (start_of_today_utc,),
        ).fetchall()
    ]
    for intake_id in missed_ids:
        conn.execute(
            "UPDATE med_intakes SET status='missed' WHERE id=?",
            (intake_id,),
        )

    counts = {"generated": generated, "missed": len(missed_ids)}
    audit.log(conn, "tick.meds_gen", counts)
    conn.commit()
    return counts


def car(conn, client=None, now_utc=None, cfg=None):
    """Phase 4 Task 7: poll StarLine -> record_metrics -> update the
    fuel-low flag -> check/alert staleness. Owns its own commit (same
    contract as meds_gen/digest above -- this tick has no external
    caller managing a shared transaction, so the tick run IS the
    transaction boundary).

    StarLine being unavailable (client.poll() returns None, e.g. token
    expired or the API is down) is not an error: it is audited as
    tick.car {skipped: "unavailable"} and no car_metrics row is written
    this run, but staleness is still checked/alerted below -- an
    extended outage is exactly what the staleness alert exists to catch.
    """
    from fam import car as carmod
    cfg = cfg or gate.load_config()
    if client is None:
        client = carmod.StarlineClient()
    metrics = client.poll()
    result = {"recorded": 0}
    if metrics is None:
        audit.log(conn, "tick.car", {"skipped": "unavailable"}, actor="tick")
    else:
        carmod.record_metrics(conn, metrics)
        carmod.update_fuel_flag(conn, metrics.get("fuel_pct"), cfg)
        result["recorded"] = 1
    carmod.maybe_alert_staleness(conn, cfg, now=now_utc)
    conn.commit()
    return result
