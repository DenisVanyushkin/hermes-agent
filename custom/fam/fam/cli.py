"""fam CLI router. Subcommands register via build_parser()."""
import argparse, json, re, sys
from datetime import date as _date, datetime, timedelta, timezone
from fam import audit, cal, db as famdb, gate, grid, mail, meds, people, places, plans, rem, shopping, tick

def cmd_init(args):
    conn = famdb.connect()
    famdb.init_db(conn)
    rem.seed_default_rules(conn)
    rem.migrate_rules_2c(conn)
    conn.commit()
    out = {"ok": True, "db": famdb.resolve_db_path()}
    print(json.dumps(out, ensure_ascii=False) if args.json else f"initialized {out['db']}")
    return 0

def cmd_log(args):
    conn = famdb.connect()
    since = args.since
    if args.last_hours is not None:
        since = (datetime.now(timezone.utc) - timedelta(hours=args.last_hours)).isoformat(timespec="seconds")
    rows = audit.query(conn, since_utc=since, kind_prefix=args.kind, grep=args.grep, limit=args.limit)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        for r in rows:
            payload_json = json.dumps(r["payload"], ensure_ascii=False)
            print(f"{r['ts_utc']}\t{r['kind']}\t{r['actor']}\t{payload_json}")
    return 0

def cmd_people_add(args):
    conn = famdb.connect()
    kind = "group" if args.group else "person"
    p = people.add(conn, args.name, kind=kind, slug=args.slug, aliases=args.alias)
    conn.commit()
    print(f"added {p['kind']}: {p['name']} (id={p['id']})")
    return 0

def cmd_people_alias(args):
    conn = famdb.connect()
    people.alias(conn, args.ref, args.alias)
    conn.commit()
    print(f"alias added: {args.alias} -> {args.ref}")
    return 0

def cmd_people_member(args):
    conn = famdb.connect()
    people.add_member(conn, args.group_ref, args.person_ref)
    conn.commit()
    print(f"member added: {args.person_ref} -> {args.group_ref}")
    return 0

def cmd_people_resolve(args):
    conn = famdb.connect()
    p = people.resolve(conn, args.text)
    if args.json:
        print(json.dumps(p, ensure_ascii=False))
    elif p is None:
        print("not found")
    else:
        line = f"{p['name']} ({p['kind']}, id={p['id']})"
        if p["kind"] == "group":
            line += " members=" + ", ".join(m["name"] for m in p["members"])
        print(line)
    return 0

def cmd_people_list(args):
    conn = famdb.connect()
    rows = people.list_people(conn)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        for r in rows:
            slug_part = f"\t{r['slug']}" if r["slug"] else ""
            print(f"{r['id']}\t{r['kind']}\t{r['name']}{slug_part}")
    return 0

def cmd_places_add(args):
    conn = famdb.connect()
    p = places.add(conn, args.name, address=args.address, lat=args.lat,
                    lon=args.lon, aliases=args.alias)
    conn.commit()
    print(f"added place: {p['name']} (id={p['id']})")
    return 0

def cmd_places_alias(args):
    conn = famdb.connect()
    places.alias(conn, args.ref, args.alias)
    conn.commit()
    print(f"alias added: {args.alias} -> {args.ref}")
    return 0

def cmd_places_resolve(args):
    conn = famdb.connect()
    p = places.resolve(conn, args.text)
    if args.json:
        print(json.dumps(p, ensure_ascii=False))
    elif p is None:
        print("not found")
    else:
        print(f"{p['name']} (id={p['id']}) {p['address']}".rstrip())
    return 0

def cmd_places_update(args):
    conn = famdb.connect()
    fields = {}
    if args.lat is not None: fields["lat"] = args.lat
    if args.lon is not None: fields["lon"] = args.lon
    if args.travel_min is not None: fields["travel_min"] = args.travel_min
    if args.address is not None: fields["address"] = args.address
    if args.notes is not None: fields["notes"] = args.notes
    if args.category is not None: fields["category"] = args.category
    p = places.update(conn, args.ref, **fields)
    conn.commit()
    if args.json:
        print(json.dumps(p, ensure_ascii=False))
    else:
        print(f"updated place: {p['name']} (id={p['id']})")
    return 0

def cmd_places_list(args):
    conn = famdb.connect()
    rows = places.list_all(conn)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        for r in rows:
            addr_part = f"\t{r['address']}" if r["address"] else ""
            print(f"{r['id']}\t{r['name']}{addr_part}")
    return 0

def _fmt_event(e):
    line = f"{e['id']}\t{e['start_local']}\t{e['title']}\t[{e['status']}]"
    if e.get("place"):
        line += f"\t@{e['place']['name']}"
    if e.get("participants"):
        line += "\twith:" + ",".join(p["name"] for p in e["participants"])
    return line

def _event_has_denis_participant(e):
    return any(p.get("slug") == "denis" for p in e.get("participants", []))

def _log_mail_result(conn, event_id, result, to=None):
    if result.get("ok"):
        audit.log(conn, "mail.sent", {"event_id": event_id, "to": to})
    else:
        audit.log(conn, "mail.error", {"event_id": event_id, "error": result.get("error")})

def _maybe_email_event(conn, e, material_changed=True):
    """After a successful `cal add`/`cal update` whose participants
    include the person with slug=="denis", send an .ics email via
    mail.send_event_email() -- IF cfg["email_enabled"] (Task 10). This is
    a CLI-layer side effect, not a domain one (mirrors how gate/tick own
    delivery, not cal.py itself) -- it runs in its OWN transaction, after
    the caller's cal.add()/cal.update() has already committed, since a
    mail hiccup must never undo or block the calendar write. Success logs
    `mail.sent`; a send failure logs `mail.error` (send_event_email()
    itself never raises).

    material_changed: dedup gate for `cal update` (Fix round 1) -- an
    update should only re-send when a MATERIAL field actually changed
    (title/start_utc/end_utc/place/participants/travel_min; title added
    by product decision, phase-2b final review Minor #7 -- see cal.py's
    _MAIL_TRIGGER_COLUMNS and update()'s "_material_changed" signal,
    which cmd_cal_update passes straight through here), e.g. a notes-only
    edit must not trigger a second email. `cal add` always passes the
    default True: an add has no "before" state to compare against, so it
    is unconditionally material (still gated on the denis-participant and
    email_enabled checks below).

    The whole body below is wrapped in try/except: config load, send, and
    audit are all best-effort here (Fix round 1 hardening) -- e.g.
    gate.load_config() raising on a corrupt live config must never
    propagate past this function and fail the `cal add`/`cal update`
    operation, which has already committed by the time this runs. On any
    failure, makes one best-effort attempt to audit mail.error (itself
    wrapped, in case even that fails) and always swallows.
    """
    try:
        if not material_changed:
            return
        if not _event_has_denis_participant(e):
            return
        cfg = gate.load_config()
        if not cfg.get("email_enabled"):
            return
        result = mail.send_event_email(e, cfg)
        _log_mail_result(conn, e["id"], result, to=cfg.get("email_to"))
        conn.commit()
    except Exception as exc:  # noqa: BLE001 -- deliberate catch-all, see docstring
        try:
            audit.log(conn, "mail.error", {"event_id": e.get("id"), "error": str(exc)})
            conn.commit()
        except Exception:  # noqa: BLE001 -- best-effort audit; never propagate
            pass

# Task 13: `cal add`/`cal update --start` guardrail -- CLI-layer only (the
# domain cal.add()/cal.update() are untouched: they still accept any
# valid ISO-8601 start with an explicit offset). An LLM caller with no
# reliable "now" signal in its context can log a start in the past (e.g.
# "today at 1pm" recorded with yesterday's date, the 12.07 incident this
# guardrail exists for) -- a 10-minute grace period tolerates ordinary
# submission latency around "right now" without flagging it as a mistake.
# --allow-past bypasses this for genuinely retroactive entries.
_PAST_START_GRACE = timedelta(minutes=10)

def _check_start_not_past(start_value, allow_past):
    if allow_past:
        return
    start_utc = cal._to_utc_iso(start_value)
    start_dt = datetime.fromisoformat(start_utc)
    now_dt = datetime.now(timezone.utc)
    if start_dt < now_dt - _PAST_START_GRACE:
        now_almaty = now_dt.astimezone(cal.ALMATY).isoformat(timespec="seconds")
        raise ValueError(
            f"start is in the past (now: {now_almaty}). If the user means "
            "a past event, retry with --allow-past; otherwise re-derive "
            "the date (run date)."
        )

def cmd_cal_add(args):
    _check_start_not_past(args.start, args.allow_past)
    conn = famdb.connect()
    e = cal.add(conn, args.title, args.start, end_utc=args.end, place=args.place,
                participants=args.with_, transport=args.transport, notes=args.notes,
                travel_min=args.travel_min)
    conn.commit()
    _maybe_email_event(conn, e)
    if args.json:
        print(json.dumps(e, ensure_ascii=False))
    else:
        print(f"added event: {e['title']} (id={e['id']}) {e['start_local']}")
    return 0

def cmd_cal_update(args):
    if args.start is not None:
        _check_start_not_past(args.start, args.allow_past)
    conn = famdb.connect()
    fields = {}
    if args.title is not None: fields["title"] = args.title
    if args.start is not None: fields["start_utc"] = args.start
    if args.end is not None: fields["end_utc"] = args.end
    if args.place is not None: fields["place"] = args.place
    if args.transport is not None: fields["transport"] = args.transport
    if args.notes is not None: fields["notes"] = args.notes
    if args.travel_min is not None: fields["travel_min"] = args.travel_min
    if args.add_person: fields["add_person"] = args.add_person
    if args.rm_person: fields["rm_person"] = args.rm_person
    e = cal.update(conn, args.id, **fields)
    conn.commit()
    # cal.update()'s "_material_changed" is an internal signal for this
    # hook only (see cal.py's docstring) -- pop it before anything else
    # (JSON output below) sees the dict, so it never leaks as public API.
    material_changed = e.pop("_material_changed", True)
    _maybe_email_event(conn, e, material_changed=material_changed)
    if args.json:
        print(json.dumps(e, ensure_ascii=False))
    else:
        print(f"updated event: {e['title']} (id={e['id']})")
    return 0

def cmd_cal_cancel(args):
    conn = famdb.connect()
    e = cal.cancel(conn, args.id)
    conn.commit()
    if args.json:
        print(json.dumps(e, ensure_ascii=False))
    else:
        print(f"cancelled event: {e['title']} (id={e['id']})")
    return 0

def cmd_cal_done(args):
    conn = famdb.connect()
    e = cal.done(conn, args.id)
    conn.commit()
    if args.json:
        print(json.dumps(e, ensure_ascii=False))
    else:
        print(f"done event: {e['title']} (id={e['id']})")
    return 0

def cmd_cal_show(args):
    conn = famdb.connect()
    e = cal.get(conn, args.id)
    if e is None:
        # cal.get() returning None is fine (mirrors people/places get());
        # unknown ids are rejected here, like update/cancel/done's
        # ValueError contract, so `fam cal show <bad-id>` exits 2.
        raise ValueError(f"unknown event: {args.id}")
    if args.json:
        print(json.dumps(e, ensure_ascii=False))
    else:
        print(_fmt_event(e))
    return 0

def cmd_cal_day(args):
    conn = famdb.connect()
    rows = cal.day(conn, args.date)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        for e in rows:
            print(_fmt_event(e))
    return 0

def cmd_cal_range(args):
    conn = famdb.connect()
    from_utc = cal._to_utc_iso(args.from_iso)
    to_utc = cal._to_utc_iso(args.to_iso)
    rows = cal.list_range(conn, from_utc, to_utc)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        for e in rows:
            print(_fmt_event(e))
    return 0

def _month_arg(value):
    """argparse type= for --month: YYYY-MM -> (year, month). Raising
    ArgumentTypeError here makes argparse print a message and exit 2,
    same contract as an unrecognized/malformed flag.
    """
    if not re.fullmatch(r"\d{4}-\d{2}", value):
        raise argparse.ArgumentTypeError(f"invalid month (expected YYYY-MM): {value}")
    year, month = int(value[:4]), int(value[5:7])
    if not 1 <= month <= 12:
        raise argparse.ArgumentTypeError(f"invalid month (expected YYYY-MM): {value}")
    return (year, month)

def _date_arg(value):
    """argparse type= for --day/--week: YYYY-MM-DD, validated as a real
    date. Shared by both flags since the format contract is identical.
    """
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise argparse.ArgumentTypeError(f"invalid date (expected YYYY-MM-DD): {value}")
    try:
        _date(int(value[:4]), int(value[5:7]), int(value[8:10]))
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date (expected YYYY-MM-DD): {value}")
    return value

def cmd_cal_grid(args):
    conn = famdb.connect()
    if args.month is not None:
        year, month = args.month
        out = grid.render_month(conn, year, month, args.out)
    elif args.week is not None:
        out = grid.render_week(conn, args.week, args.out)
    else:
        out = grid.render_day(conn, args.day, args.out)
    if args.json:
        print(json.dumps({"ok": True, "path": out}, ensure_ascii=False))
    else:
        print(f"wrote {out}")
    return 0

def _fmt_reminder(r):
    return f"{r['id']}\t{r['event_id']}\t{r['fire_at_utc']}\t{r['label']}\t[{r['status']}]"

def cmd_rem_list(args):
    conn = famdb.connect()
    rows = rem.list_reminders(conn, event_id=args.event, due=args.due)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        for r in rows:
            print(_fmt_reminder(r))
    return 0

def cmd_rem_ack(args):
    conn = famdb.connect()
    if cal.get(conn, args.event_id) is None:
        raise ValueError(f"unknown event: {args.event_id}")
    count = rem.ack_chain(conn, args.event_id, scope=args.scope)
    conn.commit()
    out = {"event_id": args.event_id, "acked": count, "scope": args.scope}
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(f"acked {count} reminder(s) for event {args.event_id}")
    return 0

def cmd_rem_cancel(args):
    conn = famdb.connect()
    if cal.get(conn, args.event_id) is None:
        raise ValueError(f"unknown event: {args.event_id}")
    count = rem.cancel_chain(conn, args.event_id)
    conn.commit()
    out = {"event_id": args.event_id, "cancelled": count}
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(f"cancelled {count} reminder(s) for event {args.event_id}")
    return 0

def cmd_rem_rules(args):
    conn = famdb.connect()
    rows = rem.list_rules(conn)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        for r in rows:
            state = "on" if r["enabled"] else "off"
            print(f"{r['id']}\t{r['scope']}\t{state}\t{r['stages']}")
    return 0

def _fmt_active_chain(a):
    return (f"{a['event_id']}\t{a['start_local']}\t{a['title']}\t"
            f"next={a['next_fire_local']}\tpending={a['pending_count']}\t"
            f"sent={a['sent_count']}")

def cmd_rem_active(args):
    """`fam rem active` -- events with an in-progress reminder chain (>=1
    pending reminder). This is the lookup a conversational reaction
    ("уже выходим") uses to find WHICH event it's about, since the
    reminder that fired is delivered out-of-band and never lands in the
    agent's own session context (see amina-fam skill's Reminder Reactions).
    """
    conn = famdb.connect()
    rows = rem.active_chains(conn)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        for r in rows:
            print(_fmt_active_chain(r))
    return 0

def cmd_tick_reminders(args):
    conn = famdb.connect()
    # tick.reminders() owns its own commits (see fam/tick.py docstring) --
    # unlike the other cmd_* handlers above, no conn.commit() here.
    counts = tick.reminders(conn, now_utc=args.now)
    if args.json:
        print(json.dumps(counts, ensure_ascii=False))
    else:
        print(" ".join(f"{k}={v}" for k, v in counts.items()))
    return 0

def cmd_tick_digest(args):
    conn = famdb.connect()
    # tick.digest() owns its own commit (see fam/tick.py) -- no
    # conn.commit() here, same as cmd_tick_reminders above.
    summary = tick.digest(conn, now_utc=args.now)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(" ".join(f"{k}={v}" for k, v in summary.items()))
    return 0

def cmd_tick_meds_gen(args):
    conn = famdb.connect()
    # tick.meds_gen() owns its own commit (see fam/tick.py) -- no
    # conn.commit() here, same as cmd_tick_reminders/cmd_tick_digest above.
    counts = tick.meds_gen(conn, now_utc=args.now)
    if args.json:
        print(json.dumps(counts, ensure_ascii=False))
    else:
        print(" ".join(f"{k}={v}" for k, v in counts.items()))
    return 0

def cmd_tick_maintenance(args):
    from fam import maint
    cfg = gate.load_config()
    now = None
    if getattr(args, "now", None):
        from datetime import datetime
        now = datetime.fromisoformat(args.now)
    result = maint.run_maintenance(
        cfg, dry_run=getattr(args, "dry_run", False), now=now)
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"pruned={result['pruned']} "
              f"backups={len(result['backups'])} errors={len(result['errors'])}")
    return 1 if result["errors"] else 0

def cmd_mail_test(args):
    """`fam mail test EVENT_ID` -- manually trigger the .ics email for one
    event, unconditionally (no email_enabled/denis-participant gating --
    this is a diagnostic/live-check command, see Task 10/T11). Always
    exits 0 on a known event; the send outcome (ok/error) is reported in
    the output itself, same as the cal add/update hook's audit contract.
    """
    conn = famdb.connect()
    e = cal.get(conn, args.id)
    if e is None:
        raise ValueError(f"unknown event: {args.id}")
    cfg = gate.load_config()
    result = mail.send_event_email(e, cfg)
    _log_mail_result(conn, e["id"], result, to=cfg.get("email_to"))
    conn.commit()
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"mail test: {result}")
    return 0

def cmd_road(args):
    """`fam road EVENT_ID` -- manual road recompute for one event (debug +
    skill use, Task 5). Runs the SAME code path as cal add/update's hook
    (cal.recompute_road -- same audit kinds), then regenerates the
    reminder chain so it reflects the fresh travel_min_road, mirroring
    cal.update's hook order. An event without usable coordinates (or no
    home config) is informational, not an error: source "none", exit 0.
    """
    conn = famdb.connect()
    e = cal.get(conn, args.event_id)
    if e is None:
        raise ValueError(f"unknown event: {args.event_id}")
    res = cal.recompute_road(conn, args.event_id)
    if res.get("minutes") is None:
        # commit anyway: recompute_road may still have audited
        # road.error/road.cap/road.hook_error rows worth keeping.
        conn.commit()
        reason = res["reason"]
        out = {"event_id": args.event_id, "travel_min_road": None,
               "source": "none", "reason": reason}
        if args.json:
            print(json.dumps(out, ensure_ascii=False))
        elif reason == "no_place_coords":
            print(f"road: event {args.event_id} has no place coordinates (source=none)")
        elif reason == "no_home_config":
            print(f"road: event {args.event_id} skipped, home coordinates not "
                  f"configured (source=none)")
        elif reason.startswith("fallback_source:"):
            src = reason.split(":", 1)[1]
            print(f"road: event {args.event_id} not computed, leave_at falls "
                  f"back to '{src}' (source=none)")
        else:  # "error"
            print(f"road: event {args.event_id} recompute failed -- детали в "
                  f"fam log --kind road (source=none)")
        return 0
    rem.regenerate(conn, args.event_id)
    conn.commit()
    e = cal.get(conn, args.event_id)
    out = {"event_id": args.event_id,
           "travel_min_road": e["travel_min_road"],
           "source": res["source"],
           "leave_at_local": cal._to_local_iso(rem.leave_at(conn, e))}
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(f"road: event {args.event_id} travel_min_road={out['travel_min_road']} "
              f"source={out['source']} leave_at={out['leave_at_local']}")
    return 0

def _fmt_plan(p):
    line = f"{p['id']}\t{p['title']}\t[{p['status']}]"
    if p.get("deadline"):
        line += f"\tdue={p['deadline']}"
    if p.get("place"):
        line += f"\t@{p['place']['name']}"
    if p.get("person"):
        line += f"\tfor:{p['person']['name']}"
    return line

def cmd_plan_add(args):
    conn = famdb.connect()
    plan_id = plans.add(conn, args.title, place=args.place, person=args.person,
                         deadline=args.deadline, notes=args.notes)
    conn.commit()
    p = plans.get(conn, plan_id)
    if args.json:
        print(json.dumps(p, ensure_ascii=False))
    else:
        print(f"added plan: {p['title']} (id={p['id']})")
    return 0

def cmd_plan_list(args):
    conn = famdb.connect()
    rows = plans.list_all(conn) if args.all else plans.list_open(conn)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        for p in rows:
            print(_fmt_plan(p))
    return 0

def cmd_plan_done(args):
    conn = famdb.connect()
    if not plans.mark(conn, args.id, "done"):
        raise ValueError(f"unknown plan: {args.id}")
    conn.commit()
    p = plans.get(conn, args.id)
    if args.json:
        print(json.dumps(p, ensure_ascii=False))
    else:
        print(f"done plan: {p['title']} (id={p['id']})")
    return 0

def cmd_plan_drop(args):
    conn = famdb.connect()
    if not plans.mark(conn, args.id, "dropped"):
        raise ValueError(f"unknown plan: {args.id}")
    conn.commit()
    p = plans.get(conn, args.id)
    if args.json:
        print(json.dumps(p, ensure_ascii=False))
    else:
        print(f"dropped plan: {p['title']} (id={p['id']})")
    return 0

def cmd_plan_attach(args):
    conn = famdb.connect()
    if plans.get(conn, args.id) is None:
        raise ValueError(f"unknown plan: {args.id}")
    if cal.get(conn, args.event) is None:
        raise ValueError(f"unknown event: {args.event}")
    plans.attach(conn, args.id, args.event)
    conn.commit()
    # Final review Finding 3: the amina-fam skill promises the event's
    # road leave_at "recomputes automatically" after an attach. Reuse the
    # same per-event mechanism cal.add/cal.update/`fam road` already use
    # (cal.recompute_road) rather than inventing a second one -- it is
    # already best-effort/never-raises and self-audits (road.computed /
    # road.hook_error), so a recompute failure here must not fail the
    # attach itself. This recomputes the SAME route the event already
    # had (home -> event place); it does NOT route through the newly
    # attached plan's place as a waypoint -- a deliberate backlog item,
    # not this fix's scope.
    try:
        cal.recompute_road(conn, args.event)
        conn.commit()
    except Exception as e:
        audit.log(conn, "tick.error",
                  {"where": "plan_attach_recompute", "error": str(e)[:200]})
        conn.commit()
    p = plans.get(conn, args.id)
    if args.json:
        print(json.dumps(p, ensure_ascii=False))
    else:
        print(f"attached plan {args.id} -> event {args.event}")
    return 0

def _fmt_med(m):
    times_str = ",".join(m["times"])
    line = f"{m['id']}\t{m['name']}\t{times_str}"
    if m.get("dose"):
        line += f"\tdose={m['dose']}"
    if m.get("remaining") is not None:
        line += f"\tremaining={m['remaining']}"
    line += f"\tthreshold={m['threshold']}"
    if not m.get("enabled", True):
        line += "\t[disabled]"
    return line

def _parse_times_arg(value):
    """`--times 08:00,20:00` -> ["08:00", "20:00"]. No format validation
    here -- meds.add()/meds.edit() validate each entry (ValueError ->
    CLI exit 2 via main()'s except clause), same as the rest of this
    module leaving domain validation to the fam/*.py layer.
    """
    return [t.strip() for t in value.split(",") if t.strip()]

def cmd_meds_add(args):
    conn = famdb.connect()
    times = _parse_times_arg(args.times)
    med_id = meds.add(conn, args.name, times, dose=args.dose,
                       remaining=args.remaining, threshold=args.threshold)
    conn.commit()
    m = meds.get(conn, med_id)
    if args.json:
        print(json.dumps(m, ensure_ascii=False))
    else:
        print(f"added med: {m['name']} (id={m['id']})")
    return 0

def cmd_meds_list(args):
    conn = famdb.connect()
    rows = meds.list(conn, include_disabled=args.all)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        for m in rows:
            print(_fmt_med(m))
    return 0

def cmd_meds_edit(args):
    conn = famdb.connect()
    fields = {}
    if args.name is not None: fields["name"] = args.name
    if args.dose is not None: fields["dose"] = args.dose
    if args.times is not None: fields["times"] = _parse_times_arg(args.times)
    if args.remaining is not None: fields["remaining"] = args.remaining
    if args.threshold is not None: fields["threshold"] = args.threshold
    if args.enabled is not None: fields["enabled"] = bool(args.enabled)
    if not meds.edit(conn, args.id, **fields):
        raise ValueError(f"unknown med: {args.id}")
    conn.commit()
    m = meds.get(conn, args.id)
    if args.json:
        print(json.dumps(m, ensure_ascii=False))
    else:
        print(f"updated med: {m['name']} (id={m['id']})")
    return 0

def cmd_meds_rm(args):
    conn = famdb.connect()
    if not meds.remove(conn, args.id):
        raise ValueError(f"unknown med: {args.id}")
    conn.commit()
    out = {"id": args.id}
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(f"removed med {args.id}")
    return 0

def cmd_med_taken(args):
    """`fam med taken <intake_id>` -- ack a scheduled dose as taken
    (phase 5 Task 5). meds.take raises ValueError on an unknown
    intake_id, handled by main()'s except ValueError -> exit 2, same
    as every other unknown-ref path in this CLI. This command never
    sends a proactive "time to buy" message itself (Denis's decision:
    that's the skill's job in T8, or the digest's in T7) -- it only
    surfaces the restock fact in the returned dict / text line.
    """
    conn = famdb.connect()
    result = meds.take(conn, args.id)
    conn.commit()
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        line = f"taken: intake {args.id}"
        if result.get("remaining") is not None:
            line += f" (remaining={result['remaining']})"
        if result.get("restock"):
            line += " -- restock needed, added to shopping" \
                if result.get("restock_added") \
                else " -- restock needed (already on shopping list)"
        print(line)
    return 0

def _fmt_pending_intake(r):
    return f"{r['intake_id']}\t{r['name']}\t{r['plan_ts_utc']}\t[{r['status']}]"

def cmd_med_list(args):
    """`fam med list [--pending]` -- list pending med_intakes (phase 5
    T8 review round 1). --pending is accepted for symmetry with the
    skill's verb-matching call sites (and to be self-documenting) but
    isn't otherwise load-bearing: meds.list_pending() only ever
    returns status='pending' rows, since no other status is worth
    listing here (taken/skipped are already resolved acks; missed is
    tick.meds_gen's own bookkeeping) -- Denis's call, 5 T8 review.
    This replaces the amina-fam skill's old audit-log join for
    resolving "what dose is this ack about" (see meds.list_pending's
    docstring for why that join was unreliable).
    """
    conn = famdb.connect()
    rows = meds.list_pending(conn)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        for r in rows:
            print(_fmt_pending_intake(r))
    return 0

def cmd_med_skip(args):
    """`fam med skip <intake_id>` -- ack a scheduled dose as skipped,
    this dose only (phase 5 Task 5): remaining is left untouched and
    the next scheduled intake is unaffected. Same unknown-intake_id ->
    ValueError -> exit 2 contract as cmd_med_taken.
    """
    conn = famdb.connect()
    result = meds.skip(conn, args.id)
    conn.commit()
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"skipped: intake {args.id}")
    return 0

def _fmt_shop(it):
    line = f"{it['id']}\t{it['name']}"
    if it.get("qty"):
        line += f"\tqty={it['qty']}"
    if it.get("added_by"):
        line += f"\tby={it['added_by']}"
    if it.get("source") == "meds":
        line += "\t[meds]"
    return line

def cmd_shop_add(args):
    conn = famdb.connect()
    item_id = shopping.add(conn, args.name, qty=args.qty, added_by=args.by)
    conn.commit()
    it = shopping.get(conn, item_id)
    if args.json:
        print(json.dumps(it, ensure_ascii=False))
    else:
        print(f"added to shopping: {it['name']} (id={it['id']})")
    return 0

def cmd_shop_list(args):
    conn = famdb.connect()
    rows = shopping.list_open(conn)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        for it in rows:
            print(_fmt_shop(it))
    return 0

def cmd_shop_done(args):
    conn = famdb.connect()
    if not shopping.mark_done(conn, args.id):
        raise ValueError(f"unknown shopping item: {args.id}")
    conn.commit()
    it = shopping.get(conn, args.id)
    if args.json:
        print(json.dumps(it, ensure_ascii=False))
    else:
        print(f"done: {it['name']} (id={it['id']})")
    return 0

def build_parser():
    p = argparse.ArgumentParser(prog="fam")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init"); sp.set_defaults(func=cmd_init)
    # default=SUPPRESS: don't let an unset sub-level flag clobber a root-level
    # --json (e.g. `fam --json init`) — only overwrite when explicitly passed here.
    sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                     help="machine-readable output")

    sp = sub.add_parser("log"); sp.set_defaults(func=cmd_log)
    sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                     help="machine-readable output")
    since_group = sp.add_mutually_exclusive_group()
    since_group.add_argument("--since", help="ISO-8601 UTC timestamp lower bound")
    since_group.add_argument("--last-hours", dest="last_hours", type=float,
                              help="lower bound as N hours before now")
    sp.add_argument("--kind", help="filter by kind prefix")
    sp.add_argument("--grep", help="substring filter on payload JSON")
    sp.add_argument("--limit", type=int, default=50)

    sp = sub.add_parser("people")
    people_sub = sp.add_subparsers(dest="people_cmd", required=True)

    spa = people_sub.add_parser("add"); spa.set_defaults(func=cmd_people_add)
    spa.add_argument("name")
    spa.add_argument("--group", action="store_true",
                      help="create a group instead of a person")
    spa.add_argument("--slug")
    spa.add_argument("--alias", dest="alias", action="append", default=[],
                      help="attach an alias (repeatable)")

    spal = people_sub.add_parser("alias"); spal.set_defaults(func=cmd_people_alias)
    spal.add_argument("ref", help="id, name, alias, or slug of the person/group")
    spal.add_argument("alias", help="new alias to attach")

    spm = people_sub.add_parser("member"); spm.set_defaults(func=cmd_people_member)
    spm.add_argument("group_ref")
    spm.add_argument("person_ref")

    spr = people_sub.add_parser("resolve"); spr.set_defaults(func=cmd_people_resolve)
    spr.add_argument("text")
    spr.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spl = people_sub.add_parser("list"); spl.set_defaults(func=cmd_people_list)
    spl.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    sp = sub.add_parser("places")
    places_sub = sp.add_subparsers(dest="places_cmd", required=True)

    spa = places_sub.add_parser("add"); spa.set_defaults(func=cmd_places_add)
    spa.add_argument("name")
    spa.add_argument("--address", default="")
    spa.add_argument("--lat", type=float)
    spa.add_argument("--lon", type=float)
    spa.add_argument("--alias", dest="alias", action="append", default=[],
                      help="attach an alias (repeatable)")

    spal = places_sub.add_parser("alias"); spal.set_defaults(func=cmd_places_alias)
    spal.add_argument("ref", help="id or name of the place")
    spal.add_argument("alias", help="new alias to attach")

    spu = places_sub.add_parser("update"); spu.set_defaults(func=cmd_places_update)
    spu.add_argument("ref", help="id, name, or alias of the place")
    spu.add_argument("--lat", type=float)
    spu.add_argument("--lon", type=float)
    spu.add_argument("--travel-min", dest="travel_min", type=int,
                      help="manual leave_at minutes fallback")
    spu.add_argument("--address")
    spu.add_argument("--notes")
    spu.add_argument("--category", choices=["grocery", "pharmacy"],
                      help="categorize this place for shopping.match_enroute "
                           "'по пути' matching (phase 5 T6)")
    spu.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spr = places_sub.add_parser("resolve"); spr.set_defaults(func=cmd_places_resolve)
    spr.add_argument("text")
    spr.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spl = places_sub.add_parser("list"); spl.set_defaults(func=cmd_places_list)
    spl.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    sp = sub.add_parser("cal")
    cal_sub = sp.add_subparsers(dest="cal_cmd", required=True)
    transport_choices = ["car", "walk", "public", "unknown"]

    spa = cal_sub.add_parser("add"); spa.set_defaults(func=cmd_cal_add)
    spa.add_argument("--title", required=True)
    spa.add_argument("--start", required=True,
                      help="ISO-8601, any offset (e.g. 2026-07-15T10:00:00+05:00)")
    spa.add_argument("--end")
    spa.add_argument("--place", help="place name/alias/id")
    spa.add_argument("--with", dest="with_", action="append", default=[],
                      help="participant ref: name/alias/slug/group (repeatable)")
    spa.add_argument("--transport", choices=transport_choices, default="unknown")
    spa.add_argument("--notes", default="")
    spa.add_argument("--travel-min", dest="travel_min", type=int,
                      help="override place travel minutes for leave_at (default: take from place)")
    spa.add_argument("--allow-past", dest="allow_past", action="store_true",
                      help="skip the past-start guardrail (retroactive event entry)")
    spa.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spu = cal_sub.add_parser("update"); spu.set_defaults(func=cmd_cal_update)
    spu.add_argument("id", type=int)
    spu.add_argument("--title")
    spu.add_argument("--start")
    spu.add_argument("--end")
    spu.add_argument("--place")
    spu.add_argument("--transport", choices=transport_choices)
    spu.add_argument("--notes")
    spu.add_argument("--travel-min", dest="travel_min", type=int,
                      help="override place travel minutes for leave_at")
    spu.add_argument("--add-person", dest="add_person", action="append", default=[],
                      help="participant ref to add (repeatable)")
    spu.add_argument("--rm-person", dest="rm_person", action="append", default=[],
                      help="participant ref to remove (repeatable)")
    spu.add_argument("--allow-past", dest="allow_past", action="store_true",
                      help="skip the past-start guardrail (retroactive event entry)")
    spu.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spc = cal_sub.add_parser("cancel"); spc.set_defaults(func=cmd_cal_cancel)
    spc.add_argument("id", type=int)
    spc.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spd = cal_sub.add_parser("done"); spd.set_defaults(func=cmd_cal_done)
    spd.add_argument("id", type=int)
    spd.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    sps = cal_sub.add_parser("show"); sps.set_defaults(func=cmd_cal_show)
    sps.add_argument("id", type=int)
    sps.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spday = cal_sub.add_parser("day"); spday.set_defaults(func=cmd_cal_day)
    spday.add_argument("date", help="YYYY-MM-DD in Asia/Almaty")
    spday.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="machine-readable output")

    sprange = cal_sub.add_parser("range"); sprange.set_defaults(func=cmd_cal_range)
    sprange.add_argument("from_iso")
    sprange.add_argument("to_iso")
    sprange.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                          help="machine-readable output")

    spg = cal_sub.add_parser("grid"); spg.set_defaults(func=cmd_cal_grid)
    grid_group = spg.add_mutually_exclusive_group(required=True)
    grid_group.add_argument("--day", type=_date_arg, help="YYYY-MM-DD")
    grid_group.add_argument("--week", type=_date_arg,
                             help="YYYY-MM-DD, any day within the target Mon-Sun week")
    grid_group.add_argument("--month", type=_month_arg, help="YYYY-MM")
    spg.add_argument("-o", "--out", dest="out", required=True, help="output PNG path")
    spg.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    sp = sub.add_parser("rem")
    rem_sub = sp.add_subparsers(dest="rem_cmd", required=True)

    spl = rem_sub.add_parser("list"); spl.set_defaults(func=cmd_rem_list)
    rem_list_group = spl.add_mutually_exclusive_group()
    rem_list_group.add_argument("--event", type=int, help="filter to one event id")
    rem_list_group.add_argument("--due", action="store_true",
                                 help="pending reminders with fire_at_utc <= now")
    spl.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spa = rem_sub.add_parser("ack"); spa.set_defaults(func=cmd_rem_ack)
    spa.add_argument("event_id", type=int)
    spa.add_argument("--scope", choices=["prepare", "all"], default="all",
                      help="prepare = only kind='prepare' stages; all = whole chain (default)")
    spa.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spc = rem_sub.add_parser("cancel"); spc.set_defaults(func=cmd_rem_cancel)
    spc.add_argument("event_id", type=int)
    spc.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spr = rem_sub.add_parser("rules"); spr.set_defaults(func=cmd_rem_rules)
    spr.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spac = rem_sub.add_parser("active"); spac.set_defaults(func=cmd_rem_active)
    spac.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="machine-readable output")

    sp = sub.add_parser("tick")
    tick_sub = sp.add_subparsers(dest="tick_cmd", required=True)

    spt = tick_sub.add_parser("reminders"); spt.set_defaults(func=cmd_tick_reminders)
    spt.add_argument("--now", help="ISO-8601 UTC override for \"now\" (testing/ops)")
    spt.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    sptd = tick_sub.add_parser("digest"); sptd.set_defaults(func=cmd_tick_digest)
    sptd.add_argument("--now", help="ISO-8601 UTC override for \"now\" (testing/ops)")
    sptd.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="machine-readable output")

    sptm = tick_sub.add_parser("meds-gen"); sptm.set_defaults(func=cmd_tick_meds_gen)
    sptm.add_argument("--now", help="ISO-8601 UTC override for \"now\" (testing/ops)")
    sptm.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="machine-readable output")

    sptx = tick_sub.add_parser("maintenance"); sptx.set_defaults(func=cmd_tick_maintenance)
    sptx.add_argument("--now", help="ISO-8601 UTC override for \"now\" (testing/ops)")
    sptx.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS,
                       help="report actions without deleting/writing")
    sptx.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="machine-readable output")

    sp = sub.add_parser("mail")
    mail_sub = sp.add_subparsers(dest="mail_cmd", required=True)

    spmt = mail_sub.add_parser("test"); spmt.set_defaults(func=cmd_mail_test)
    spmt.add_argument("id", type=int)
    spmt.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="machine-readable output")

    sp = sub.add_parser("plan")
    plan_sub = sp.add_subparsers(dest="plan_cmd", required=True)

    spa = plan_sub.add_parser("add"); spa.set_defaults(func=cmd_plan_add)
    spa.add_argument("title")
    spa.add_argument("--place", help="place name/alias/id")
    spa.add_argument("--person", help="person name/alias/slug")
    spa.add_argument("--deadline", help="YYYY-MM-DD local")
    spa.add_argument("--notes", default="")
    spa.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spl = plan_sub.add_parser("list"); spl.set_defaults(func=cmd_plan_list)
    spl.add_argument("--all", action="store_true",
                      help="include done/dropped plans (default: open only)")
    spl.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spd = plan_sub.add_parser("done"); spd.set_defaults(func=cmd_plan_done)
    spd.add_argument("id", type=int)
    spd.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spdr = plan_sub.add_parser("drop"); spdr.set_defaults(func=cmd_plan_drop)
    spdr.add_argument("id", type=int)
    spdr.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="machine-readable output")

    spat = plan_sub.add_parser("attach"); spat.set_defaults(func=cmd_plan_attach)
    spat.add_argument("id", type=int)
    spat.add_argument("--event", type=int, required=True)
    spat.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="machine-readable output")

    sp = sub.add_parser("road"); sp.set_defaults(func=cmd_road)
    sp.add_argument("event_id", type=int)
    sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                     help="machine-readable output")

    sp = sub.add_parser("meds")
    meds_sub = sp.add_subparsers(dest="meds_cmd", required=True)

    spa = meds_sub.add_parser("add"); spa.set_defaults(func=cmd_meds_add)
    spa.add_argument("name")
    spa.add_argument("--times", required=True,
                      help="comma-separated HH:MM list, e.g. 08:00,20:00")
    spa.add_argument("--dose", default="")
    spa.add_argument("--remaining", type=int)
    spa.add_argument("--threshold", type=int, default=0)
    spa.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spl = meds_sub.add_parser("list"); spl.set_defaults(func=cmd_meds_list)
    spl.add_argument("--all", action="store_true",
                      help="include disabled meds (default: enabled only)")
    spl.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spe = meds_sub.add_parser("edit"); spe.set_defaults(func=cmd_meds_edit)
    spe.add_argument("id", type=int)
    spe.add_argument("--name")
    spe.add_argument("--dose")
    spe.add_argument("--times", help="comma-separated HH:MM list")
    spe.add_argument("--remaining", type=int)
    spe.add_argument("--threshold", type=int)
    spe.add_argument("--enabled", type=int, choices=[0, 1],
                      help="1=enabled, 0=disabled")
    spe.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spr = meds_sub.add_parser("rm"); spr.set_defaults(func=cmd_meds_rm)
    spr.add_argument("id", type=int)
    spr.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    # "med" (singular) -- per-dose acks (taken/skip), distinct from
    # "meds" (plural) above, which manages med definitions themselves.
    sp = sub.add_parser("med")
    med_sub = sp.add_subparsers(dest="med_cmd", required=True)

    spt = med_sub.add_parser("taken"); spt.set_defaults(func=cmd_med_taken)
    spt.add_argument("id", type=int, help="med_intakes row id")
    spt.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    sps = med_sub.add_parser("skip"); sps.set_defaults(func=cmd_med_skip)
    sps.add_argument("id", type=int, help="med_intakes row id")
    sps.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spl = med_sub.add_parser("list"); spl.set_defaults(func=cmd_med_list)
    spl.add_argument("--pending", action="store_true",
                      help="pending intakes only (default -- no other "
                           "status is worth listing here)")
    spl.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    sp = sub.add_parser("shop")
    shop_sub = sp.add_subparsers(dest="shop_cmd", required=True)

    spa = shop_sub.add_parser("add"); spa.set_defaults(func=cmd_shop_add)
    spa.add_argument("name")
    spa.add_argument("--qty", default="")
    spa.add_argument("--by", default="")
    spa.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spl = shop_sub.add_parser("list"); spl.set_defaults(func=cmd_shop_list)
    spl.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spd = shop_sub.add_parser("done"); spd.set_defaults(func=cmd_shop_done)
    spd.add_argument("id", type=int)
    spd.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    return p

def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except cal.UnknownRefError as e:
        print(str(e), file=sys.stderr); return 2
    except ValueError as e:
        print(str(e), file=sys.stderr); return 2
    except famdb.sqlite3.Error as e:
        print(f"db error: {e}", file=sys.stderr); return 2
