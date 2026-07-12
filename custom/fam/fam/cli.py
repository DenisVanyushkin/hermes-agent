"""fam CLI router. Subcommands register via build_parser()."""
import argparse, json, re, sys
from datetime import date as _date, datetime, timedelta, timezone
from fam import audit, cal, db as famdb, gate, grid, mail, people, places, rem, tick

def cmd_init(args):
    conn = famdb.connect()
    famdb.init_db(conn)
    rem.seed_default_rules(conn)
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
    count = rem.ack_chain(conn, args.event_id)
    conn.commit()
    out = {"event_id": args.event_id, "acked": count}
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

    sp = sub.add_parser("mail")
    mail_sub = sp.add_subparsers(dest="mail_cmd", required=True)

    spmt = mail_sub.add_parser("test"); spmt.set_defaults(func=cmd_mail_test)
    spmt.add_argument("id", type=int)
    spmt.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
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
