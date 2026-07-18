"""fam CLI router. Subcommands register via build_parser()."""
import argparse, json, re, sys
from datetime import date as _date, datetime, timedelta, timezone
from fam import audit, cal, db as famdb, gate, geo2gis, grid, mail, maint, meds, people, places, plans, rem, series, shopping, tick

def cmd_init(args):
    conn = famdb.connect()
    famdb.init_db(conn)
    rem.seed_default_rules(conn)
    rem.migrate_rules_2c(conn)
    conn.commit()
    famdb.harden_perms(famdb.resolve_db_path())
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

def cmd_people_update(args):
    conn = famdb.connect()
    home = None if args.home == "" else args.home
    p = people.set_home(conn, args.ref, home)
    conn.commit()
    if getattr(args, "json", False):
        print(json.dumps(p, ensure_ascii=False))
    else:
        home_name = p["home_place"]["name"] if p["home_place"] else "(none)"
        print(f"updated {p['name']}: home={home_name}")
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

def _maybe_resolve_2gis(address, lat, lon):
    """If a 2GIS link was given as the address and no explicit coords,
    resolve coordinates from the link. Returns (lat, lon) -- unchanged
    when coords were explicit, the link is not 2GIS, or resolution fails.
    """
    if lat is None and lon is None and address and geo2gis.is_2gis_link(address):
        coords = geo2gis.resolve_place_coords(address)
        if coords:
            return coords
    return lat, lon


def cmd_places_add(args):
    conn = famdb.connect()
    lat, lon = _maybe_resolve_2gis(args.address, args.lat, args.lon)
    p = places.add(conn, args.name, address=args.address, lat=lat,
                    lon=lon, aliases=args.alias)
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
    lat, lon = _maybe_resolve_2gis(args.address, args.lat, args.lon)
    fields = {}
    if lat is not None: fields["lat"] = lat
    if lon is not None: fields["lon"] = lon
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

def _prompt_captcha(url):
    print(f"captcha image: {url}")
    return (None, input("captcha text: ").strip())

def cmd_car_auth_init(args):
    import getpass
    from fam import car
    from starline import StarlineAuth
    app_id = input("app_id [15526]: ").strip() or "15526"
    app_secret = getpass.getpass("app_secret: ")
    login = input("SLID login: ").strip()
    password = getpass.getpass("SLID password: ")
    store = car.bootstrap(
        StarlineAuth(), app_id, app_secret, login, password,
        prompt_sms=lambda: input("SMS code: ").strip(),
        prompt_captcha=_prompt_captcha)
    client = car.StarlineClient()
    client.save_store(store)
    print("token stored. Run `fam car poll` to confirm and set device_id.")
    return 0

def cmd_car_set_device(args):
    """`fam car set-device [device_id]` -- discover/select which
    StarLine device fam polls. No arg: auto-set if exactly one device
    is reachable, else list them (never guess among several)."""
    from fam import car
    client = car.StarlineClient()
    if args.device_id:
        client.set_device(args.device_id)
        print(f"set device_id={args.device_id}")
        return 0
    devices = client.list_devices()
    if not devices:
        print("no devices")
        return 0
    if len(devices) == 1:
        (dev_id, alias), = devices.items()
        client.set_device(dev_id)
        print(f"set device_id={dev_id} ({alias})")
        return 0
    for dev_id, alias in devices.items():
        print(f"{dev_id}\t{alias}")
    print("multiple devices found; re-run `fam car set-device <device_id>` with the one you want")
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

def _check_trip_has_transport(place, transport):
    """A place-bound event is a trip: its car/warmup departure hooks and the
    road/leave_at math depend on HOW Amina gets there. Leaving transport
    'unknown' silently disables those hooks (15.07 "posyolok": fuel 16%%,
    low-fuel flag set, yet no "zapravsya" because transport was unknown).
    So a trip with no concrete mode is rejected at the CLI layer, forcing the
    skill to set it (asking Amina if unclear). Placeless events -- calls,
    birthdays -- never trip hooks, so 'unknown' stays allowed there."""
    if place and (transport is None or transport == "unknown"):
        raise ValueError(
            "trip to a place needs a transport mode -- pass "
            "--transport car|walk|public (ask Amina: машина/пешком/такси). "
            "Transport is unknown."
        )

def cmd_cal_add(args):
    if getattr(args, "repeat", None):
        return _cmd_cal_add_series(args)
    if not args.start:
        print("error: --start is required (or use --repeat for a recurring schedule)",
              file=sys.stderr)
        return 2
    _check_start_not_past(args.start, args.allow_past)
    _check_trip_has_transport(args.place, args.transport)
    conn = famdb.connect()
    e = cal.add(conn, args.title, args.start, end_utc=args.end, place=args.place,
                participants=args.with_, transport=args.transport, notes=args.notes,
                travel_min=args.travel_min, prep_min=args.prep_min)
    conn.commit()
    _maybe_email_event(conn, e)
    if args.json:
        print(json.dumps(e, ensure_ascii=False))
    else:
        print(f"added event: {e['title']} (id={e['id']}) {e['start_local']}")
    return 0


def _cmd_cal_add_series(args):
    """--repeat weekly path: create an event_series and materialize its first
    occurrences immediately (so the next one shows up right away)."""
    if not args.days or not args.start_time:
        print("error: --repeat weekly requires --days and --start-time",
              file=sys.stderr)
        return 2
    _check_trip_has_transport(args.place, args.transport)
    conn = famdb.connect()
    try:
        s = series.add(conn, args.title, args.days, args.start_time,
                       end_time=args.end_time, place=args.place,
                       participants=args.with_, transport=args.transport,
                       notes=args.notes, until_local=args.until,
                       prep_min=args.prep_min)
    except (ValueError, cal.UnknownRefError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    created = series.generate(conn)
    conn.commit()
    if args.json:
        print(json.dumps({"series": s, "generated": created}, ensure_ascii=False))
    else:
        span = f" {args.start_time}-{args.end_time}" if args.end_time else f" {args.start_time}"
        print(f"added series: {s['title']} (id={s['id']}) {s['weekdays']}{span} "
              f"[{created} upcoming]")
    return 0


def cmd_cal_series_list(args):
    conn = famdb.connect()
    rows = series.list_active(conn)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
    elif not rows:
        print("no active series")
    else:
        for sr in rows:
            span = f"{sr['start_time']}-{sr['end_time']}" if sr['end_time'] else sr['start_time']
            print(f"{sr['id']}	{sr['title']}	{sr['weekdays']} {span}	"
                  f"{sr['future_count']} upcoming")
    return 0


def cmd_cal_series_cancel(args):
    conn = famdb.connect()
    try:
        removed = series.cancel(conn, args.id)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    conn.commit()
    print(f"cancelled series {args.id}; removed {removed} upcoming occurrence(s)")
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
    if args.prep_min is not None: fields["prep_min"] = args.prep_min
    if args.add_person: fields["add_person"] = args.add_person
    if args.rm_person: fields["rm_person"] = args.rm_person
    e = cal.update(conn, args.id, **fields)
    conn.commit()
    # cal.update()'s "_material_changed" is an internal signal for this
    # hook only (see cal.py's docstring) -- pop it before anything else
    # (JSON output below) sees the dict, so it never leaks as public API.
    material_changed = e.pop("_material_changed", True)
    _maybe_email_event(conn, e, material_changed=material_changed)
    # --prep-asked is a small additive flag, orthogonal to the regular
    # update() fields above (it isn't a valid cal.update() kwarg -- there's
    # no reminder/road/mail consequence to it): a direct column write plus
    # its own audit entry, applied alongside whatever else was requested.
    if getattr(args, "prep_asked", False):
        conn.execute("UPDATE events SET prep_asked=1 WHERE id=?", (args.id,))
        audit.log(conn, "cal.update", {"id": args.id, "prep_asked": 1})
        conn.commit()
        e["prep_asked"] = 1
    if args.json:
        print(json.dumps(e, ensure_ascii=False))
    else:
        print(f"updated event: {e['title']} (id={e['id']})")
    return 0

def cmd_cal_cancel(args):
    conn = famdb.connect()
    e = cal.cancel(conn, args.id)
    conn.commit()
    # cal.cancel()'s "dropped_prep_plans" is transient (not persisted --
    # see cal.py's docstring), surfaced here so a human/LLM caller sees
    # which prep-plans got dropped as a side effect of the cancellation.
    dropped = e.pop("dropped_prep_plans", [])
    if args.json:
        out = dict(e)
        out["dropped_prep_plans"] = dropped
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(f"cancelled event: {e['title']} (id={e['id']})")
        if dropped:
            titles = ", ".join(p["title"] for p in dropped)
            print(f"dropped prep plan(s): {titles}")
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

def _audit_tick_error(where, exc):
    """Persist a tick.error marker so the nightly problem_summary sweep
    (6b) can see a failure that would otherwise only hit journald.
    Best-effort: a failure to record must not mask the original error."""
    try:
        conn = famdb.connect()
        try:
            audit.log(conn, "tick.error",
                      {"where": where, "error": str(exc)[:200]}, actor="tick")
            conn.commit()
        finally:
            conn.close()
    except Exception:                                # noqa: BLE001
        pass

def cmd_tick_reminders(args):
    conn = famdb.connect()
    # tick.reminders() owns its own commits (see fam/tick.py docstring) --
    # unlike the other cmd_* handlers above, no conn.commit() here.
    try:
        counts = tick.reminders(conn, now_utc=args.now)
    except Exception as e:                           # noqa: BLE001 -- mark then re-raise
        _audit_tick_error("reminders", e)
        raise
    if args.json:
        print(json.dumps(counts, ensure_ascii=False))
    else:
        print(" ".join(f"{k}={v}" for k, v in counts.items()))
    return 0

def cmd_tick_digest(args):
    conn = famdb.connect()
    # tick.digest() owns its own commit (see fam/tick.py) -- no
    # conn.commit() here, same as cmd_tick_reminders above.
    try:
        summary = tick.digest(conn, now_utc=args.now)
    except Exception as e:                           # noqa: BLE001 -- mark then re-raise
        _audit_tick_error("digest", e)
        raise
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(" ".join(f"{k}={v}" for k, v in summary.items()))
    return 0

def cmd_tick_cal_gen(args):
    conn = famdb.connect()
    try:
        created = series.generate(conn, now_utc=args.now)
    except Exception as e:                           # noqa: BLE001 -- mark then re-raise
        _audit_tick_error("cal_gen", e)
        raise
    audit.log(conn, "tick.cal_gen", {"created": created})
    conn.commit()
    if getattr(args, "json", False):
        print(json.dumps({"created": created}, ensure_ascii=False))
    else:
        print(f"created={created}")
    return 0


def cmd_tick_meds_gen(args):
    conn = famdb.connect()
    # tick.meds_gen() owns its own commit (see fam/tick.py) -- no
    # conn.commit() here, same as cmd_tick_reminders/cmd_tick_digest above.
    try:
        counts = tick.meds_gen(conn, now_utc=args.now)
    except Exception as e:                           # noqa: BLE001 -- mark then re-raise
        _audit_tick_error("meds_gen", e)
        raise
    if args.json:
        print(json.dumps(counts, ensure_ascii=False))
    else:
        print(" ".join(f"{k}={v}" for k, v in counts.items()))
    return 0

def cmd_tick_car(args):
    conn = famdb.connect()
    # tick.car() owns its own commit (see fam/tick.py) -- no
    # conn.commit() here, same as cmd_tick_reminders/cmd_tick_digest/
    # cmd_tick_meds_gen above.
    try:
        counts = tick.car(conn, now_utc=args.now)
    except Exception as e:                           # noqa: BLE001 -- mark then re-raise
        _audit_tick_error("car", e)
        raise
    if args.json:
        print(json.dumps(counts, ensure_ascii=False))
    else:
        print(" ".join(f"{k}={v}" for k, v in counts.items()))
    return 0

def cmd_car_warmup(args):
    """`fam car warmup [--confirm] [--requester X]` -- remote-start the
    engine via car.do_warmup, guarded by the daily limit and
    already-on checks (phase 4 T9). Without --confirm this is a dry
    preview only: it never touches the StarLine API or the audit log,
    so an agent/operator can safely ask "what would happen" first."""
    from fam import car
    requester = args.requester or "denis"
    if not args.confirm:
        out = {"ok": None, "preview": True, "requester": requester}
        if args.json:
            print(json.dumps(out, ensure_ascii=False))
        else:
            print(f"dry run: would warm up the car for {requester} (use --confirm to actually start)")
        return 0
    conn = famdb.connect()
    cfg = gate.load_config()
    client = car.StarlineClient()
    result = car.do_warmup(conn, client, cfg, requester)
    conn.commit()
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"warmup: {result['reason']}" if not result["ok"] else "warmup: started")
    return 0

def cmd_car_stop(args):
    """`fam car stop [--confirm] [--requester X]` -- remote engine stop
    via car.do_stop (warmup's mirror: dry preview without --confirm,
    fresh-poll + already_off guard, attempt-before-actuator audit,
    notify; no daily limit -- stopping is physically harmless)."""
    from fam import car
    requester = args.requester or "denis"
    if not args.confirm:
        out = {"ok": None, "preview": True, "requester": requester}
        if args.json:
            print(json.dumps(out, ensure_ascii=False))
        else:
            print(f"dry run: would stop the engine for {requester} (use --confirm to actually stop)")
        return 0
    conn = famdb.connect()
    cfg = gate.load_config()
    client = car.StarlineClient()
    result = car.do_stop(conn, client, cfg, requester)
    conn.commit()
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"stop: {result['reason']}" if not result["ok"] else "stop: stopped")
    return 0

def cmd_car_status(args):
    """`fam car status` -- latest car_metrics row + the fuel-low flag,
    so an agent can answer Amina about fuel/car state without reaching
    into the DB directly."""
    from fam import car
    conn = famdb.connect()
    if getattr(args, "live", False):
        # Poll StarLine now instead of trusting the last 30-min tick --
        # the only honest way to answer "машина заведена?" right after a
        # remote start/stop. poll() never raises; None -> fall back to
        # whatever the DB already has.
        data = car.StarlineClient().poll()
        if data:
            car.record_metrics(conn, data)
            conn.commit()
    row = conn.execute(
        "SELECT * FROM car_metrics ORDER BY ts_utc DESC LIMIT 1").fetchone()
    out = {k: row[k] for k in row.keys()} if row else {}
    out.pop("raw_json", None)
    out["fuel_is_low"] = car.fuel_is_low(conn)
    # run OR ign, same rule as the warmup guard: the S96v2 auto-start
    # reports ign=true while run stays false, so engine_on alone lies.
    out["engine_running"] = car._latest_engine_on(conn) if row else False
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        if not row:
            print("no car data yet")
        else:
            print(f"ts={out.get('ts_utc')}\tfuel={out.get('fuel_pct')}%\t"
                  f"engine_running={out['engine_running']}\tfuel_is_low={out['fuel_is_low']}")
    return 0

def cmd_car_set_transport(args):
    """`fam car set-transport <event_id> car|walk|public` -- a focused
    shortcut over cal.update(transport=...) (same underlying field as
    `fam cal update --transport`) for the common case of only touching
    transport, with no other event fields in play."""
    conn = famdb.connect()
    e = cal.update(conn, args.event_id, transport=args.transport)
    conn.commit()
    e.pop("_material_changed", None)
    if args.json:
        print(json.dumps(e, ensure_ascii=False))
    else:
        print(f"transport set: event {e['id']} -> {args.transport}")
    return 0

def cmd_tick_maintenance(args):
    from fam import maint
    cfg = gate.load_config()
    now = None
    if getattr(args, "now", None):
        from datetime import datetime, timezone
        now = datetime.fromisoformat(args.now)
        if now.tzinfo is None:
            # An offset-less --now string parses naive, which would
            # subtly shift the retention boundary vs the stored
            # aware timestamps (prod always uses aware _now_utc()) --
            # normalize to UTC-aware so ops/testing --now behaves the
            # same as prod.
            now = now.replace(tzinfo=timezone.utc)
    result = maint.run_maintenance(
        cfg, dry_run=getattr(args, "dry_run", False), now=now)
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"pruned={result['pruned']} "
              f"backups={len(result['backups'])} errors={len(result['errors'])}")
    return 1 if result["errors"] else 0

def cmd_tick_offsite(args):
    cfg = gate.load_config()
    if not cfg.get("offsite_enabled"):
        print("offsite disabled; skipping")
        return 0
    now = None
    if getattr(args, "now", None):
        from datetime import datetime, timezone
        now = datetime.fromisoformat(args.now)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
    try:
        result = maint.offsite_backup(cfg, now=now)
    except Exception as e:                       # noqa: BLE001
        _audit_tick_error("offsite", e)
        print(f"offsite failed: {e}")
        return 1
    for w in result["written"]:
        print(f"wrote {w}")
    if result["errors"]:
        for e in result["errors"]:
            print(f"error: {e}")
        _audit_tick_error("offsite", "; ".join(result["errors"]))
        return 1
    return 0

def cmd_tick_brevity(args):
    from fam import brevity
    cfg = gate.load_config()
    now = None
    if getattr(args, "now", None):
        from datetime import datetime, timezone
        now = datetime.fromisoformat(args.now)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
    result = brevity.run_audit(cfg, now=now)
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"sent={result['sent']} reason={result['reason']}")
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
    # --when default: computed here at the CLI layer, not inside
    # plans.add() -- "date" when --deadline is given, "departure"
    # otherwise. Only applies when --prep-for is actually used; a
    # plain (non-prep) `plan add` never touches prep_when.
    prep_when = args.when
    if args.prep_for is not None and prep_when is None:
        prep_when = "date" if args.deadline is not None else "departure"
    plan_id = plans.add(conn, args.title, place=args.place, person=args.person,
                         deadline=args.deadline, notes=args.notes,
                         prep_for_event=args.prep_for, prep_when=prep_when)
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
    """`fam shop done <id> [--restock N]` -- mark a shopping item bought.
    --restock N closes finding F1's loop: after the item is marked done it
    pushes N units back into the matching med's remaining via
    meds.restock_by_name (matched by casefolded name -- for a source='meds'
    item that name is the med's). Restock is gated on the mark_done
    transition above, which only fires once per item, so a repeat done on an
    already-bought item exits 2 before restock runs -- N is never applied
    twice (idempotency). A plain grocery item with no matching med restocks
    nothing (restock_by_name returns None).
    """
    conn = famdb.connect()
    it = shopping.get(conn, args.id)
    if it is None:
        raise ValueError(f"unknown shopping item: {args.id}")
    # shopping.mark_done returns True for any existing row (even an
    # already-done one), so it is NOT the idempotency gate. Gate restock on
    # the prior status being 'open' -- the real open->done transition -- so a
    # repeat `shop done` on an already-bought item is a genuine no-op and N is
    # never applied twice (finding F1 loop close).
    was_open = it["status"] == "open"
    shopping.mark_done(conn, args.id)
    it = shopping.get(conn, args.id)
    restock = None
    n = getattr(args, "restock", None)
    if n is not None and was_open:
        restock = meds.restock_by_name(conn, it["name"], n)
    conn.commit()
    if args.json:
        out = dict(it)
        if restock is not None:
            out["restock"] = restock
        print(json.dumps(out, ensure_ascii=False))
    else:
        line = f"done: {it['name']} (id={it['id']})"
        if restock and restock.get("restocked"):
            line += f" -- restocked {it['name']} to {restock['remaining']}"
        print(line)
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

    spu = people_sub.add_parser("update"); spu.set_defaults(func=cmd_people_update)
    spu.add_argument("ref", help="id, name, alias, or slug of the person/group")
    spu.add_argument("--home", required=True,
                      help="place ref to set as home, or \"\" to clear it")
    spu.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
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

    sp = sub.add_parser("car")
    car_sub = sp.add_subparsers(dest="car_cmd", required=True)

    spa = car_sub.add_parser("auth-init"); spa.set_defaults(func=cmd_car_auth_init)

    spsd = car_sub.add_parser("set-device"); spsd.set_defaults(func=cmd_car_set_device)
    spsd.add_argument("device_id", nargs="?", help="StarLine device id (omit to auto-discover)")

    spp = car_sub.add_parser("poll"); spp.set_defaults(func=cmd_tick_car)
    spp.add_argument("--now", help="ISO-8601 UTC override for \"now\" (testing/ops)")
    spp.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spw = car_sub.add_parser("warmup"); spw.set_defaults(func=cmd_car_warmup)
    spw.add_argument("--confirm", action="store_true",
                      help="actually start the engine (default: dry preview only)")
    spw.add_argument("--requester", help="who asked for the warmup (default: denis)")
    spw.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spst = car_sub.add_parser("stop"); spst.set_defaults(func=cmd_car_stop)
    spst.add_argument("--confirm", action="store_true",
                      help="actually stop the engine (default: dry preview only)")
    spst.add_argument("--requester", help="who asked for the stop (default: denis)")
    spst.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    sps = car_sub.add_parser("status"); sps.set_defaults(func=cmd_car_status)
    sps.add_argument("--live", action="store_true",
                      help="poll StarLine now instead of the last tick's row")
    sps.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spt = car_sub.add_parser("set-transport"); spt.set_defaults(func=cmd_car_set_transport)
    spt.add_argument("event_id", type=int)
    spt.add_argument("transport", choices=["car", "walk", "public"])
    spt.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    sp = sub.add_parser("cal")
    cal_sub = sp.add_subparsers(dest="cal_cmd", required=True)
    transport_choices = ["car", "walk", "public", "unknown"]

    spa = cal_sub.add_parser("add"); spa.set_defaults(func=cmd_cal_add)
    spa.add_argument("--title", required=True)
    spa.add_argument("--start",
                      help="ISO-8601, any offset (e.g. 2026-07-15T10:00:00+05:00); "
                           "one-off events only")
    spa.add_argument("--end")
    spa.add_argument("--repeat", choices=["weekly"],
                      help="make this a recurring series (with --days/--start-time)")
    spa.add_argument("--days",
                      help="weekdays for --repeat, e.g. mon,wed,fri")
    spa.add_argument("--start-time", dest="start_time",
                      help="HH:MM local start time for --repeat")
    spa.add_argument("--end-time", dest="end_time",
                      help="HH:MM local end time for --repeat")
    spa.add_argument("--until",
                      help="YYYY-MM-DD last date for --repeat (default: open-ended)")
    spa.add_argument("--place", help="place name/alias/id")
    spa.add_argument("--with", dest="with_", action="append", default=[],
                      help="participant ref: name/alias/slug/group (repeatable)")
    spa.add_argument("--transport", choices=transport_choices, default="unknown")
    spa.add_argument("--notes", default="")
    spa.add_argument("--travel-min", dest="travel_min", type=int,
                      help="override place travel minutes for leave_at (default: take from place)")
    spa.add_argument("--prep-min", dest="prep_min", type=int,
                      help="minutes needed to get ready before leave_at; "
                           "overrides the default/slug reminder rules with "
                           "this event's own escalation chain (also applies "
                           "with --repeat, copied onto every occurrence)")
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
    spu.add_argument("--prep-min", dest="prep_min", type=int,
                      help="minutes needed to get ready before leave_at; "
                           "overrides the default/slug reminder rules with "
                           "this event's own escalation chain")
    spu.add_argument("--add-person", dest="add_person", action="append", default=[],
                      help="participant ref to add (repeatable)")
    spu.add_argument("--rm-person", dest="rm_person", action="append", default=[],
                      help="participant ref to remove (repeatable)")
    spu.add_argument("--allow-past", dest="allow_past", action="store_true",
                      help="skip the past-start guardrail (retroactive event entry)")
    spu.add_argument("--prep-asked", dest="prep_asked", action="store_true",
                      help="mark this event as having already been asked "
                           "about prep (sets events.prep_asked=1)")
    spu.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spc = cal_sub.add_parser("cancel"); spc.set_defaults(func=cmd_cal_cancel)
    spc.add_argument("id", type=int)

    spser = cal_sub.add_parser("series")
    series_sub = spser.add_subparsers(dest="series_cmd", required=True)
    spsl = series_sub.add_parser("list"); spsl.set_defaults(func=cmd_cal_series_list)
    spsl.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="machine-readable output")
    spsc = series_sub.add_parser("cancel"); spsc.set_defaults(func=cmd_cal_series_cancel)
    spsc.add_argument("id", type=int)
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

    sptcg = tick_sub.add_parser("cal-gen"); sptcg.set_defaults(func=cmd_tick_cal_gen)
    sptcg.add_argument("--now", help="ISO-8601 UTC override for \"now\" (testing/ops)")
    sptcg.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="machine-readable output")

    sptm = tick_sub.add_parser("meds-gen"); sptm.set_defaults(func=cmd_tick_meds_gen)
    sptm.add_argument("--now", help="ISO-8601 UTC override for \"now\" (testing/ops)")
    sptm.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="machine-readable output")

    sptc = tick_sub.add_parser("car"); sptc.set_defaults(func=cmd_tick_car)
    sptc.add_argument("--now", help="ISO-8601 UTC override for \"now\" (testing/ops)")
    sptc.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="machine-readable output")

    sptx = tick_sub.add_parser("maintenance"); sptx.set_defaults(func=cmd_tick_maintenance)
    sptx.add_argument("--now", help="ISO-8601 UTC override for \"now\" (testing/ops)")
    sptx.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS,
                       help="report actions without deleting/writing")
    sptx.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="machine-readable output")

    spof = tick_sub.add_parser("offsite"); spof.set_defaults(func=cmd_tick_offsite)
    spof.add_argument("--now", help="ISO-8601 UTC override for \"now\" (testing/ops)")

    spb = tick_sub.add_parser("brevity"); spb.set_defaults(func=cmd_tick_brevity)
    spb.add_argument("--now"); spb.add_argument("--json", action="store_true")

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
    spa.add_argument("--deadline", "--due", dest="deadline", help="YYYY-MM-DD local")
    spa.add_argument("--notes", default="")
    spa.add_argument("--prep-for", dest="prep_for", type=int,
                      help="event id this plan is prep for")
    spa.add_argument("--when", choices=["date", "departure"],
                      help="prep_when: 'date' (has its own deadline) or "
                           "'departure' (just needs doing before the event); "
                           "default: 'date' if --deadline/--due given, else "
                           "'departure' (only meaningful with --prep-for)")
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
    spd.add_argument("--restock", type=int,
                      help="units bought -> add to the matching med's "
                           "remaining (close finding F1's restock loop)")
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
