"""fam CLI router. Subcommands register via build_parser()."""
import argparse, json, re, sys
from datetime import date as _date, datetime, timedelta, timezone
from urllib.parse import urljoin
from fam import acks, audit, cal, db as famdb, extcal, gate, geo2gis, goals, grid, mail, maint, meds, people, places, plans, react, rem, resolve, series, shopping, tick, whereami

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

def cmd_whereami_show(args):
    """Откуда Гермес сейчас считает дорогу и почему именно оттуда."""
    conn = famdb.connect()
    cfg = gate.load_config()
    origin = whereami.resolve_origin(conn, cfg)
    if origin is None:
        out = {"source": None, "reason": "no_origin"}
        print(json.dumps(out, ensure_ascii=False) if getattr(args, "json", False)
              else "whereami: неоткуда считать -- дом не настроен и других "
                   "источников нет")
        return 0
    if getattr(args, "json", False):
        print(json.dumps(origin, ensure_ascii=False))
    else:
        age = origin.get("fix_age_min")
        age_txt = f", фиксу {age} мин" if age is not None else ""
        print(f"whereami: {origin['label']} ({origin['source']}, "
              f"уверенность {origin['confidence']}{age_txt}) "
              f"-- {origin['lat']}, {origin['lon']}")
    return 0


def cmd_whereami_set(args):
    """Записать точку, от которой считать дорогу (присланную или ручную)."""
    conn = famdb.connect()
    cfg = gate.load_config()
    try:
        hint = whereami.set_hint(conn, args.lat, args.lon, source=args.source,
                                 label=args.label, ttl_min=args.ttl_min, cfg=cfg)
    except ValueError as e:
        print(f"whereami: {e}", file=sys.stderr)
        return 2
    conn.commit()

    # Пересчёт делается ВСЕГДА -- ради него точку и присылали.
    changed = whereami.recompute_affected(conn, cfg)
    hint["changed"] = changed

    # А вот сообщение -- только по явному --notify. Обычно точка
    # приходит внутри диалога, и агент отвечает сам; автоматическая
    # отправка здесь дала бы Амине два сообщения об одном и том же.
    # --notify нужен для неразговорного пути (например, Денис поставил
    # override руками). Kind "whereami" освобождён от бюджета в
    # gate.BUDGET_EXEMPT_KINDS, а force=True снимает блокировку -- нужны
    # оба, см. комментарий там.
    if getattr(args, "notify", False) and changed:
        lines = [f"{c['title']}: теперь ≈{c['new']} мин" for c in changed]
        text = "Пересчитал дорогу. " + "; ".join(lines)
        hint["notified"] = gate.deliver(
            conn, "whereami", {"changed": changed}, text, cfg, force=True)
        conn.commit()

    if getattr(args, "json", False):
        print(json.dumps(hint, ensure_ascii=False))
    else:
        print(f"whereami: считаю от {hint['lat']}, {hint['lon']} "
              f"(до {hint['expires_utc']})")
        for c in changed:
            print(f"  {c['title']}: {c['old']} -> {c['new']} мин")
    return 0


def cmd_whereami_clear(args):
    conn = famdb.connect()
    n = whereami.clear_hints(conn)
    conn.commit()
    if getattr(args, "json", False):
        print(json.dumps({"cleared": n}, ensure_ascii=False))
    else:
        print(f"whereami: убрано подсказок: {n}")
    return 0


def cmd_react_hook(args):
    """Machine-to-machine entry: the WhatsApp adapter pipes ONE reaction
    event as JSON on stdin, we apply the mapped ack and print the
    adapter's feedback instruction. No human-facing output mode -- this
    is never typed by the agent or by Denis (see fam/react.py)."""
    return react.run_hook()

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


def _refresh_pending_acks(conn):
    """Refresh the durable gateway projection after a mutating command."""
    try:
        cfg = gate.load_config()
    except Exception:
        return None
    acks.write(conn, cfg=cfg)


def cmd_resolve_turn(args):
    """Resolve one gateway turn from strict JSON on stdin."""
    try:
        request = json.load(sys.stdin)
    except (TypeError, json.JSONDecodeError):
        print(json.dumps({"status": "unresolved", "residual": True,
                          "reason": "malformed_request"}, ensure_ascii=False))
        return 2
    if not isinstance(request, dict):
        print(json.dumps({"status": "unresolved", "residual": True,
                          "reason": "malformed_request"}, ensure_ascii=False))
        return 2
    conn = famdb.connect()
    try:
        famdb.migrate_resolve_receipts(conn)
        result = resolve.resolve_turn(conn, request, cfg=gate.load_config())
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0

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

# Occupancy guardrail (2026-08-01), CLI-layer only -- same split as
# _check_start_not_past: cal.add()/cal.update() still accept anything.
# Double-booking is legitimate (Amina may genuinely want two things at
# once), but it must be HER decision: without --allow-overlap fam writes
# nothing and names what is already there, so the skill has to ask.
def _format_conflict(e):
    """'Интервизия 06.08 10:00–12:15 (id=103)' -- local time, en dash."""
    start = datetime.fromisoformat(e["start_local"])
    span = start.strftime("%d.%m %H:%M")
    if e["end_local"]:
        span += "–" + datetime.fromisoformat(e["end_local"]).strftime("%H:%M")
    return f"{e['title']} {span} (id={e['id']})"


def _conflict_list(conflicts, limit=3):
    shown = ", ".join(_format_conflict(e) for e in conflicts[:limit])
    extra = len(conflicts) - limit
    return shown + (f" (+{extra} more)" if extra > 0 else "")


def _check_no_overlap(conn, start_utc, end_utc, allow_overlap, exclude_id=None):
    """Raise ValueError (-> main -> exit 2) when the slot is taken and the
    caller did not pass --allow-overlap. Returns the conflicts it let
    through, so the caller can audit the acknowledgement."""
    conflicts = cal.overlaps(conn, start_utc, end_utc, exclude_id=exclude_id)
    if conflicts and not allow_overlap:
        n = len(conflicts)
        raise ValueError(
            f"overlaps {n} active event{'s' if n > 1 else ''}: "
            f"{_conflict_list(conflicts)}. Ask Amina whether to keep both, "
            "then retry with --allow-overlap.")
    return conflicts


def _audit_overlap_ack(conn, scope, conflicts, **ids):
    """One audit row per acknowledged double-booking -- makes Amina's
    deliberate overlap distinguishable from an accidental one afterwards."""
    if not conflicts:
        return
    payload = {"scope": scope, "conflicts": sorted({e["id"] for e in conflicts})}
    payload.update(ids)
    audit.log(conn, "cal.overlap_ack", payload)


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
    conflicts = _check_no_overlap(conn, args.start, args.end, args.allow_overlap)
    e = cal.add(conn, args.title, args.start, end_utc=args.end, place=args.place,
                participants=args.with_, transport=args.transport, notes=args.notes,
                travel_min=args.travel_min, prep_min=args.prep_min)
    _audit_overlap_ack(conn, "add", conflicts, event_id=e["id"])
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
    # Ref validation must win over the overlap preview below: series.add()
    # used to be the first thing this function called, so an unknown place
    # or participant surfaced immediately. Resolving refs here (pure reads,
    # same as series.add()'s own resolution) keeps that ordering -- a bad
    # ref is not something the overlap message's "ask Amina" framing fits.
    # start_time/end_time are validated here, via the same
    # series._validate_hhmm series.add() itself uses, so a bad value
    # surfaces with its clear message ("time data '25:00' does not match
    # format '%H:%M'") -- iter_occurrences below parses the same strings
    # with raw int()/datetime() calls that raise uglier ones
    # ("hour must be in 0..23", "invalid literal for int() ...") for the
    # same input (Finding 4). Validating before the preview keeps the
    # friendlier message for both paths without touching iter_occurrences.
    try:
        cal._resolve_place(conn, args.place)
        cal._resolve_participants(conn, args.with_)
        series._validate_hhmm(args.start_time)
        if args.end_time is not None:
            series._validate_hhmm(args.end_time)
    except (ValueError, cal.UnknownRefError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    now_local = datetime.now(timezone.utc).astimezone(cal.ALMATY)
    # Single clock read shared with series.generate() below (Finding 3):
    # the preview here and the materialization there must check/write the
    # same grid. Passed through generate()'s now_utc test seam as a UTC
    # ISO string, matching how it normalizes the parameter.
    now_utc_iso = now_local.astimezone(timezone.utc).isoformat(timespec="seconds")
    horizon_date = (now_local + timedelta(weeks=series.HORIZON_WEEKS)).date()
    try:
        occurrences = series.iter_occurrences(
            args.days, args.start_time, args.end_time, args.until,
            now_local, horizon_date)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # Check the whole grid BEFORE anything is written: a series that
    # collides every week is exactly the case worth asking about once.
    busy = [(start, cal.overlaps(conn, start, end)) for start, end in occurrences]
    busy = [(start, hits) for start, hits in busy if hits]
    if busy and not args.allow_overlap:
        first_start, first_hits = busy[0]
        first_local = datetime.fromisoformat(first_start).astimezone(
            cal.ALMATY).strftime("%d.%m %H:%M")
        print(f"error: series overlaps {len(busy)} of {len(occurrences)} "
              f"planned occurrences, first: {_format_conflict(first_hits[0])} "
              f"vs new {first_local}. Ask Amina, then retry with "
              "--allow-overlap.", file=sys.stderr)
        return 2

    try:
        s = series.add(conn, args.title, args.days, args.start_time,
                       end_time=args.end_time, place=args.place,
                       participants=args.with_, transport=args.transport,
                       notes=args.notes, until_local=args.until,
                       prep_min=args.prep_min)
    except (ValueError, cal.UnknownRefError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    created = series.generate(conn, now_utc=now_utc_iso)
    _audit_overlap_ack(conn, "series",
                       [e for _, hits in busy for e in hits],
                       series_id=s["id"])
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

def cmd_cal_series_update(args):
    conn = famdb.connect()
    try:
        result = series.update_participants(
            conn, args.id, add=args.add_person, remove=args.rm_person)
    except (ValueError, cal.UnknownRefError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    conn.commit()
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        n = len(result["updated_events"])
        print(f"updated series {args.id}: {n} future occurrence(s) touched")
    return 0

def cmd_cal_update(args):
    if args.start is not None:
        _check_start_not_past(args.start, args.allow_past)
    conn = famdb.connect()
    conflicts = []
    shifted_end_utc = None
    if args.start is not None or args.end is not None:
        current = cal.get(conn, args.id)
        # current is None -> unknown id: leave it to cal.update()'s own
        # ValueError so the existing "unknown event: N" contract is intact.
        if current is not None:
            new_start = args.start if args.start is not None else current["start_utc"]
            if (args.start is not None and args.end is None
                    and current["end_utc"] is not None):
                # --start alone must SHIFT THE END BY THE SAME DELTA,
                # preserving the event's duration (Denis's ruling): moving
                # a 10:00-12:15 event to 14:00 must land at 14:00-16:15, not
                # leave end_utc where it was (which can put end before the
                # new start -- the exact pre-existing silent-corruption bug
                # this also fixes). An explicit --end always wins (this
                # branch is skipped below); an end-less event stays
                # end-less. Delta computed on real UTC datetimes, then
                # renormalized through cal._to_utc_iso like every other
                # stored value.
                old_start_dt = datetime.fromisoformat(current["start_utc"])
                old_end_dt = datetime.fromisoformat(current["end_utc"])
                new_start_dt = datetime.fromisoformat(cal._to_utc_iso(args.start))
                shifted_end_utc = cal._to_utc_iso(
                    (old_end_dt + (new_start_dt - old_start_dt)).isoformat())
                new_end = shifted_end_utc
            else:
                new_end = args.end if args.end is not None else current["end_utc"]
            conflicts = _check_no_overlap(conn, new_start, new_end,
                                          args.allow_overlap, exclude_id=args.id)
    fields = {}
    if args.title is not None: fields["title"] = args.title
    if args.start is not None: fields["start_utc"] = args.start
    if args.end is not None:
        fields["end_utc"] = args.end
    elif shifted_end_utc is not None:
        fields["end_utc"] = shifted_end_utc
    if args.place is not None: fields["place"] = args.place
    if args.transport is not None: fields["transport"] = args.transport
    if args.notes is not None: fields["notes"] = args.notes
    if args.travel_min is not None: fields["travel_min"] = args.travel_min
    if args.prep_min is not None: fields["prep_min"] = args.prep_min
    if args.add_person: fields["add_person"] = args.add_person
    if args.rm_person: fields["rm_person"] = args.rm_person
    e = cal.update(conn, args.id, **fields)
    _audit_overlap_ack(conn, "update", conflicts, event_id=args.id)
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
    _refresh_pending_acks(conn)
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
    _refresh_pending_acks(conn)
    if args.json:
        print(json.dumps(e, ensure_ascii=False))
    else:
        print(f"done event: {e['title']} (id={e['id']})")
    return 0

def cmd_cal_adopt(args):
    """`fam cal adopt <event_id>` -- Task 9: she explicitly asked Hermes to
    take over reminding her about an event she originally created on her
    iPhone. Flips `owner` 'iphone' -> 'hermes', rebuilds the reminder
    chain (`rem.regenerate` -- with owner now 'hermes' this actually
    BUILDS one; see rem.regenerate's own docstring for the mirror-image
    early-exit an owner='iphone' row takes), audits `cal.adopt`, and
    best-effort strips every VALARM off her OWN iCloud copy of the event
    (`extcal.drop_valarm`, via her stored `external_href`/`external_etag`)
    so her phone stops ringing for it too -- Hermes now owns the alarm; a
    second ringing source is exactly the duplicate-reminder problem this
    whole feature exists to avoid.

    Final-review blocker 1 (Critical): a recurring event's master +
    RECURRENCE-ID overrides arrive as ONE CalDAV resource, and `expand()`
    materializes it into N `events` rows that all share the same
    `external_href` (see extcal.py's own "ключ вхождения" docstrings).
    `drop_valarm` strips VALARM from that href's resource AS A WHOLE --
    there is no such thing as "the VALARM for just one occurrence" on the
    wire. The original single-row `adopt` therefore flipped owner for
    ONE occurrence while silencing her phone for ALL of them: every OTHER
    occurrence of the series lost its only alarm and gained no Hermes
    chain either -- total, silent, unrecoverable-by-`disown` reminder
    loss for a plain "напоминай мне про <повторяющееся>" request (skill
    rule 21's own example is a recurring workout).

    Fix: adopt operates on the whole series in one atomic step. Every
    OTHER `owner='iphone'` row sharing this event's `external_href` (i.e.
    every sibling occurrence of the same underlying resource) is flipped
    to `owner='hermes'` and reminder-regenerated ALONGSIDE the requested
    `event_id`, BEFORE the single `drop_valarm` call against that shared
    href -- one VALARM-strip network call still covers the whole
    resource, but now every row it silences also has its own chain. A
    non-recurring event (href unique to it, or no href/external_uid at
    all) has no siblings, so this is a no-op widening for the common
    case -- `adopted_ids`/output stay exactly as before when there is
    only one row.

    Unknown event_id, or an event that is already owner='hermes' (nothing
    to adopt -- includes a plain Hermes-created event that was never
    hers), raises ValueError -> main()'s existing exit-2 contract, same as
    cal update/cancel/show's own unknown-id path.

    VALARM-strip failure does NOT undo the adoption (task 9 brief, an
    explicit product decision): she asked Hermes to own the reminder, so
    Hermes must actually own it even if the network call to quiet her
    phone's own copy failed -- the cost of that failure is one extra ring
    from her phone, which is strictly better than Hermes silently NOT
    reminding her because a CalDAV PUT to a DIFFERENT calendar hiccuped.
    The failure is folded into the same `cal.adopt` audit entry
    (`valarm_dropped: False`, `valarm_error: <reason>`) rather than
    swallowed, and surfaced in this command's own output. This still
    applies series-wide: one shared network failure, folded once.

    Never calls gate.deliver (this command, like every extcal entry
    point, is not itself a new source of messages to her -- project
    invariant #1).
    """
    conn = famdb.connect()
    e = cal.get(conn, args.id)
    if e is None:
        raise ValueError(f"unknown event: {args.id}")
    if e["owner"] == "hermes":
        raise ValueError(
            f"event {args.id} is already owner=hermes -- nothing to adopt"
        )

    href = e.get("external_href")
    # Blocker 1: every OTHER occurrence of the SAME recurring series (same
    # CalDAV resource, hence same href) must be adopted in the same
    # operation as `args.id` -- see docstring above. A single, non-
    # recurring event's href is unique to it, so this query naturally
    # returns just itself.
    # `AND status='active'` (fix-round 2, minor (a)): a cancelled or done
    # occurrence of the SAME resource is already resolved -- nothing left
    # to remind about -- so it must not get swept into `owner='hermes'`
    # and reported as "also adopted N other occurrence(s)" alongside the
    # live ones.
    ids = [args.id]
    if href:
        sibling_rows = conn.execute(
            "SELECT id FROM events WHERE external_href=? AND owner='iphone' "
            "AND status='active'",
            (href,),
        ).fetchall()
        ids = sorted({row["id"] for row in sibling_rows} | {args.id})

    created = 0
    for eid in ids:
        conn.execute(
            "UPDATE events SET owner='hermes' WHERE id=? AND owner='iphone'",
            (eid,),
        )
        created += rem.regenerate(conn, eid)

    valarm_dropped = None
    valarm_error = None
    if href:
        cfg = gate.load_config()
        ok, _new_etag, detail = extcal.drop_valarm(cfg, href, e.get("external_etag"))
        valarm_dropped = ok
        if not ok:
            # Blocker 3 (privacy): `detail` embeds HER OWN iCloud event's
            # absolute href (e.g. "GET https://.../evt1.ics failed
            # (status=...)") -- redact before it lands in this command's
            # own audit entry AND stdout, the same as every other extcal
            # diagnostic channel.
            valarm_error = _redact_extcal_text(detail)

    audit_payload = {
        "id": args.id, "reminders_created": created,
        "valarm_dropped": valarm_dropped, "valarm_error": valarm_error,
    }
    if len(ids) > 1:
        audit_payload["adopted_ids"] = ids
    audit.log(conn, "cal.adopt", audit_payload)
    conn.commit()

    out = {"id": args.id, "owner": "hermes", "reminders_created": created,
           "valarm_dropped": valarm_dropped}
    if len(ids) > 1:
        out["adopted_ids"] = ids
    if valarm_error:
        out["valarm_error"] = valarm_error
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(f"adopted event {args.id}: owner=hermes, "
              f"{created} reminder(s) scheduled")
        if len(ids) > 1:
            others = ", ".join(str(i) for i in ids if i != args.id)
            print(f"  also adopted {len(ids) - 1} other occurrence(s) of "
                  f"this recurring series (same iCloud resource): {others}")
        if valarm_dropped is False:
            print(f"  warning: could not remove her phone's own alarm "
                  f"({valarm_error}) -- she may get one extra ring from it")
    return 0

def cmd_cal_disown(args):
    """`fam cal disown <event_id>` -- Task 9, the inverse of `cal adopt`:
    she asked Hermes to STOP reminding her about this event (it goes back
    to being purely her iPhone's). Flips `owner` 'hermes' -> 'iphone' and
    rebuilds the chain (`rem.regenerate` -- with owner now 'iphone' this
    deletes whatever was pending and creates nothing new, the same early
    exit `cal adopt`'s own docstring describes from the other side), then
    audits `cal.disown`.

    Deliberately does NOT attempt to restore any VALARM to her iCloud
    copy: `cal adopt`'s own VALARM removal is not reversible from
    anything this module retains -- the original alarm's offset/action
    was gone the moment that PUT overwrote it, never captured anywhere.
    If she wants her phone to ring for this event again, she has to add a
    reminder to it herself, on her phone.

    Unknown event_id, an event that is already owner='iphone' (nothing to
    disown), OR an owner='hermes' event that was never actually imported
    from her iPhone in the first place (no `external_uid`/`external_href`
    -- a plain Hermes-created event) all raise ValueError -> exit 2.

    The never-imported guard is fix-round 1, finding C1: without it, "не
    напоминай про это" said about a perfectly ordinary Hermes-created
    event would flip its owner to 'iphone' and drop its reminder chain
    (`rem.regenerate`'s own owner='iphone' early exit) even though there
    is NOTHING on her iPhone that could ever ring for it -- a silent,
    total loss of the only reminder source that event had. That is the
    exact inverse of this feature's own `cal adopt` rule ("a VALARM-strip
    failure must not silence Hermes -- one extra ring beats silence"):
    here the failure mode Hermes must refuse is producing silence by a
    perfectly ordinary, everyday phrase. `fam rem cancel <event_id>` is
    the correct verb for "stop reminding me about a Hermes event" (it
    drops the chain but leaves the event itself alone) -- not `disown`,
    which is only meaningful for an event her iPhone actually has its own
    copy of.

    Series widening (fix-round 2, finding N2 -- mirrors `cal adopt`'s own
    blocker-1 fix): the SAME resource-sharing rationale applies in
    reverse. If `args.id` carries an `external_href` shared by other
    `owner='hermes'` occurrences (typically: the whole series was
    adopted together in one `cal adopt` call), disowning only the one
    named occurrence would leave every sibling still `owner='hermes'`,
    quietly chained, while THIS occurrence goes back to owner='iphone'
    with no chain of its own and her phone's alarm for the whole
    resource already gone (stripped by the earlier adopt) -- a confusing
    half-revert, loud on the one occurrence she asked about and silent
    on the rest. Every OTHER `owner='hermes'` row sharing this href is
    flipped back to `owner='iphone'` in the same operation. This is not
    a network operation (disown never touches her iCloud copy at all,
    unlike adopt's VALARM strip), so widening it costs nothing extra on
    the wire.

    Fix-round 3, Important finding I1 (status asymmetry): fix-round 2's
    own widening query ADDED `AND status='active'` here (mirroring `cal
    adopt`'s minor (a), which skips an already-cancelled/done SIBLING so
    it does not get swept into a fresh adoption) -- but the two queries
    answer different questions, and mirroring the FILTER was wrong even
    though mirroring the WIDENING was right. `extcal._series_already_
    adopted` (the function a later sync tick uses to decide whether a
    BRAND-NEW occurrence of this resource should inherit owner='hermes')
    deliberately checks for ANY `owner='hermes'` sibling, regardless of
    its status -- see that function's own docstring. If one occurrence
    of an adopted series is later cancelled (the remote side cancels it;
    `owner` stays 'hermes', only `status` changes -- see `extcal._apply_
    event_cancel`), THEN she says "не напоминай про это" and `disown`
    runs: the OLD `status='active'` filter here left that one cancelled-
    but-still-`owner='hermes'` row untouched forever, since it is not
    "active". `_series_already_adopted` does not know or care that she
    disowned the rest of the series -- it still finds that one leftover
    `owner='hermes'` row and answers "yes, adopted" -- so every FUTURE
    occurrence of the series keeps silently inheriting `owner='hermes'`
    again, and a repeated `disown` on the new occurrence does not fix it
    either (the same stale cancelled row is still there, still
    `owner='hermes'`, still invisible to the status filter). The fix:
    `disown`'s widening matches ANY status, exactly like `_series_
    already_adopted` -- a status filter on THIS side can never be
    correct as long as that inheritance check has none. `cal adopt`'s
    OWN widening keeps its `status='active'` filter unchanged (a
    cancelled/done `owner='iphone'` sibling has nothing left to remind
    about, and leaving it `owner='iphone'` does not create this
    asymmetry -- `_series_already_adopted` only ever looks for
    `owner='hermes'` rows).
    """
    conn = famdb.connect()
    e = cal.get(conn, args.id)
    if e is None:
        raise ValueError(f"unknown event: {args.id}")
    if e["owner"] == "iphone":
        raise ValueError(
            f"event {args.id} is already owner=iphone -- nothing to disown"
        )
    if not (e.get("external_uid") or e.get("external_href")):
        raise ValueError(
            f"event {args.id} was never imported from her iPhone (no "
            f"external_uid/external_href) -- disown is only for an event "
            f"that originated on her iPhone; for a plain Hermes event use "
            f"`fam rem cancel {args.id}` to stop its reminders instead"
        )

    href = e.get("external_href")
    ids = [args.id]
    if href:
        # I1: no `AND status='active'` here -- see this command's own
        # docstring. `extcal._series_already_adopted` checks ANY
        # `owner='hermes'` sibling regardless of status; a filtered
        # widening here could leave a cancelled-but-still-`owner=
        # 'hermes'` row behind, which that check would keep finding
        # forever, silently re-adopting every future occurrence.
        sibling_rows = conn.execute(
            "SELECT id FROM events WHERE external_href=? AND owner='hermes'",
            (href,),
        ).fetchall()
        ids = sorted({row["id"] for row in sibling_rows} | {args.id})

    # Minor #4 (fix-round 1), now summed across every id widened onto
    # (fix-round 2): capture how many pending reminders actually get
    # dropped, the same way `cal adopt`'s own `reminders_created` already
    # surfaces its side of this -- rem.regenerate() itself only ever
    # returns the CREATED count (0 here, since owner is now 'iphone' --
    # see its own early-exit docstring), never touches rem.py to add a
    # second return value for this.
    removed_total = 0
    for eid in ids:
        removed_total += conn.execute(
            "SELECT COUNT(*) AS n FROM reminders WHERE event_id=? AND status='pending'",
            (eid,),
        ).fetchone()["n"]
        conn.execute(
            "UPDATE events SET owner='iphone' WHERE id=? AND owner='hermes'",
            (eid,),
        )
        rem.regenerate(conn, eid)

    audit_payload = {"id": args.id, "reminders_removed": removed_total}
    if len(ids) > 1:
        audit_payload["disowned_ids"] = ids
    audit.log(conn, "cal.disown", audit_payload)
    conn.commit()

    out = {"id": args.id, "owner": "iphone", "reminders_removed": removed_total}
    if len(ids) > 1:
        out["disowned_ids"] = ids
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(f"disowned event {args.id}: owner=iphone, {removed_total} pending "
              f"reminder(s) removed (her phone's own alarm, if any, is "
              f"not restored -- see cal disown's own limitation)")
        if len(ids) > 1:
            others = ", ".join(str(i) for i in ids if i != args.id)
            print(f"  also disowned {len(ids) - 1} other occurrence(s) of "
                  f"this recurring series (same iCloud resource): {others}")
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

def cmd_cal_detours(args):
    """Phase 7b, Task 3: `fam cal detours <event_id>` -- geo-matched open
    plans "on the way" to this event with a live TomTom detour figure
    (plans.detours), same filters as the first-prepare-stage offer in
    tick.reminders(). unknown event_id -> ValueError (exit 2), same
    contract as cal show/update/cancel.
    """
    conn = famdb.connect()
    e = cal.get(conn, args.id)
    if e is None:
        raise ValueError(f"unknown event: {args.id}")
    cfg = gate.load_config()
    offers = plans.detours(conn, e, cfg)
    rows = [{"plan_id": o["plan"]["id"], "title": o["plan"]["title"],
              "detour_min": o["detour_min"]} for o in offers]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        if not rows:
            print("no detour candidates")
        for r in rows:
            print(f"+{r['detour_min']} мин: {r['title']} (plan_id={r['plan_id']})")
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
    _refresh_pending_acks(conn)
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
    _refresh_pending_acks(conn)
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
    Best-effort: a failure to record must not mask the original error.

    `exc_type` (design 2026-08-01, §8): the exception class name, or None
    when the caller passes a pre-joined string (cli.py's offsite path).
    str(exc) alone is ambiguous -- "No item with that key" is a KeyError
    from sqlite3.Row and almost always means the prod schema lags the
    code, which the text does not say. The nightly reporter keys its
    diagnosis off this field, so it is worth the five lines."""
    try:
        conn = famdb.connect()
        try:
            audit.log(conn, "tick.error",
                      {"where": where,
                       "exc_type": type(exc).__name__ if isinstance(exc, BaseException) else None,
                       "error": str(exc)[:200]}, actor="tick")
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


# ---- cal-ext tick (Task 6) ------------------------------------------------
#
# Wires extcal.py's already-landed, independently-tested layers (discover /
# fetch_changes / parse_ics / expand / plan_changes / apply_changes -- see
# that module's own docstring) into one periodic sync, exactly the way
# cmd_tick_offsite above wires maint.offsite_backup in: extcal.py itself
# never touches a clock, a CLI arg, or scheduling -- only this glue does.
#
# Fix-round 1 (Opus review, Critical findings C1-C3 + I1/I2/I4/I5 + minors):
# the FIRST cut of this glue treated "no data this round" (an empty
# eligible-calendar list, a fetch error, a parse/expand failure) as
# indistinguishable from "her phone really has nothing here any more" --
# extcal.plan_changes' disappearance sweep then read that silence as real
# deletions and cancelled/dropped rows that were never given a fair chance
# to be re-confirmed. Every fix below follows the same one rule: a local
# owner='iphone' row is only ever a disappearance CANDIDATE when THIS round
# positively, successfully re-read the calendar it came from -- anything
# less (empty eligible list, a fetch error, a parse/expand problem, no
# calendar match at all) excludes that row from plan_changes' input
# entirely, rather than defaulting it in.
#
# This glue's job, and nothing more:
#   (a) turn cfg + "now" into the read/write window (design doc Sec.6);
#   (b) fan discover()'s calendars out through fetch_changes()/parse_ics()/
#       expand() per calendar, honoring extcal_read_calendars and the
#       write-target anti-echo belt (probe() already does both; reproduced
#       here via extcal._same_calendar, the SAME normalized comparison
#       probe() itself uses -- fix-round finding m1: the first cut
#       normalized the write-URL side but not the read-filter side, so a
#       trailing-slash mismatch in config could silently empty `eligible`
#       out and trip C1);
#   (c) attach external_href/external_etag/external_seq onto plan_changes()'
#       insert/update entries by uid -- documented as THIS task's job in
#       extcal._apply_event_insert/_apply_event_update's own docstrings;
#   (d) persist meta sync-token/last_ok/last_mode -- sync-token is gated on
#       there being no `apply_changes` error ANYWHERE this tick (fix-round
#       finding C2: advancing a calendar's token past a delta this tick
#       failed to fully apply means that delta's tombstone/update is gone
#       forever, since the NEXT delta is computed from the NEW token
#       onward), while last_ok is gated on there being NO error of ANY
#       kind this tick (its name means "last FULL success") -- last_mode
#       is pure telemetry and always updates;
#   (e) turn ANY error this tick -- a calendar fetch failure (whether every
#       calendar or just one), a parse/expand problem, or an apply-time
#       error -- into tick.error + exit 1 (fix-round finding I1: matching
#       cmd_tick_offsite's own "any non-empty errors list is a failure"
#       contract, not just a total wipeout).

# Anti-echo belt 2 (design doc invariant #4): an event whose UID matches
# this convention was written BY hermes into her calendar in the first
# place (mail.send_event_ics's `fam-<event_id>@hermes-home` convention) --
# never treated as "her" data even if the write-target URL in config is
# stale, missing, or renamed (belt 1 below is the other, independent leg).
_EXTCAL_ECHO_UID_RE = re.compile(r"^fam-.*@hermes-home$")

# Fix-round 2 (finding N1): `extcal.parse_ics`/`_finalize_component`
# ALREADY guarantee every returned Component carries a usable
# uid/dtstart_utc -- a per-component check for that AFTER parse_ics
# returns can never fire; the real silent loss happens ONE LEVEL BELOW,
# INSIDE parse_ics itself (a resource holding a recurring master AND its
# RECURRENCE-ID overrides together: an override with a broken/missing
# DTSTART is dropped there, invisibly, while a healthy sibling in the
# SAME resource survives). The only externally-observable signal is a
# component COUNT mismatch against the raw `BEGIN:VEVENT` blocks actually
# present in the resource text.
#
# Fix-round 3 (finding R1) tried to detect that mismatch with an
# INDEPENDENT line-counter here in cli.py (`_count_vevent_begin_blocks`,
# then a bare regex before that) -- and each cut disagreed with
# `parse_ics`'s own notion of "this is a VEVENT boundary" on some axis
# (case, line-ending convention, whitespace around the colon), silently
# undercounting and disabling the very guard this comment describes.
# Fix-round 4 removes the second implementation entirely: `extcal.
# parse_ics_with_count` returns the block count computed by the SAME
# parsing pass, inside `extcal.py`, that decides component boundaries in
# the first place -- see that function's own docstring. There is nothing
# left in this module to drift.

# Fix-round 2 (finding N2): `extcal.expand()`'s own per-master RRULE
# failure message embeds the master's uid via `repr()`
# (`_expand_master`: f"... for uid={master.get('uid')!r} ...") -- this
# lets a broken-RRULE error be attributed back to the ONE resource it
# came from (via `key_meta`) instead of degrading the whole calendar.
# Messages with no uid at all (e.g. "python-dateutil not installed",
# which zeroes out EVERY RRULE master in the batch at once) don't match,
# and stay a whole-calendar concern -- see `_cal_ext_sync`'s own
# docstring for why that ONE class can't be narrowed further.
_EXPAND_ERROR_UID_RE = re.compile(r"uid=['\"]([^'\"]*)['\"]")


def _extcal_window(cfg, now):
    """`(window_start, window_end)` = `[today-1d, today+horizon_weeks]`,
    the same formula extcal._time_range uses for the real iCloud query
    (design doc Sec.6) -- `horizon_weeks` always comes from cfg
    (`extcal_horizon_weeks`, default 8), never hardcoded, so a config
    change takes effect on the very next tick with no code change."""
    horizon_weeks = cfg.get("extcal_horizon_weeks", 8)
    return now - timedelta(days=1), now + timedelta(weeks=horizon_weeks)


def _extcal_url_base(url):
    """Normalized trailing-slash base for `urljoin` (fix-round finding m2):
    `urljoin` treats a base URL's last path segment as a "file" to be
    REPLACED, not a directory to append under, whenever the base doesn't
    end in "/" -- a calendar collection URL missing its trailing slash
    would silently resolve a relative href one level too high. Every href
    resolved against a calendar's own URL in this module goes through this
    helper first."""
    return url.rstrip("/") + "/" if url else url


def _extcal_eligible_calendars(cfg, calendars):
    """discover()'s full list -> the subset THIS tick actually reads: drops
    the write-target echo collection (anti-echo belt 1, design doc
    invariant #4) and applies `extcal_read_calendars` (empty = every
    non-echo calendar) -- matching by URL (via `extcal._same_calendar`, the
    SAME normalized/rstrip("/") comparison on BOTH the write-target check
    AND the read-filter check -- fix-round finding m1: the first cut only
    normalized the write-URL side) OR by display name, since Denis will
    actually populate the config with calendar names, not URLs."""
    write_url = cfg.get("extcal_write_calendar") or ""
    read_filter = set(cfg.get("extcal_read_calendars") or [])
    out = []
    for c in (calendars or []):
        url = c.get("url") or ""
        name = c.get("name")
        if write_url and extcal._same_calendar(url, write_url):
            continue
        if read_filter and not (
                any(extcal._same_calendar(url, rf) for rf in read_filter)
                or name in read_filter):
            continue
        out.append(c)
    return out


def _extcal_iphone_rows(conn, table, date_field, start_iso, end_iso):
    """Every `owner='iphone'` row (ANY status -- tombstones must stay
    visible to plan_changes, see its own docstring on "one-way ratchet")
    whose `date_field` falls inside this tick's window. `date_field` is
    `start_utc` for events, `deadline` for plans -- callers pass the
    matching bound (deadline is a plain YYYY-MM-DD Almaty-local date, not
    a UTC timestamp, so it needs its own, differently-formatted bounds --
    see _cal_ext_sync)."""
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE owner='iphone' "
        f"AND {date_field} IS NOT NULL AND {date_field} >= ? AND {date_field} <= ?",
        (start_iso, end_iso),
    ).fetchall()
    return [dict(r) for r in rows]


def _extcal_hermes_rows(conn, table, date_field, status, start_iso, end_iso):
    """Every `owner='hermes'` row in its live status, inside this tick's
    window -- fuzzy-match CANDIDATES ONLY (plan_changes' rule #2, "she
    added the same thing in both places"). Never a write target: rule #3
    is enforced structurally inside plan_changes itself (these rows are
    only ever read there, never keyed into events_by_key/plans_by_key)."""
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE owner='hermes' AND status=? "
        f"AND {date_field} IS NOT NULL AND {date_field} >= ? AND {date_field} <= ?",
        (status, start_iso, end_iso),
    ).fetchall()
    return [dict(r) for r in rows]


def _extcal_row_calendar_url(row, eligible):
    """Which eligible calendar's collection a local row's `external_href`
    lives under, by URL-prefix match (a CalDAV resource href is always a
    child of its own calendar collection's URL). None for a row with no
    `external_href`, or one under none of THIS tick's eligible calendars
    (dropped from extcal_read_calendars since it was imported, predates
    this task, or its calendar simply isn't in `eligible` this round)."""
    href = row.get("external_href")
    if not href:
        return None
    for c in eligible:
        url = c.get("url")
        if url and href.startswith(_extcal_url_base(url)):
            return url
    return None


def _dry_run_summary(changeset):
    """Redacted view of a Changeset for `--dry-run` printing (fix-round
    finding m3): the design doc's own logging discipline says "тела VEVENT
    целиком не логируются (только UID и счётчики)" -- the raw `changeset`
    carries her event TITLES and raw LOCATION text on insert entries, which
    a `--dry-run` print must not leak. Insert entries keep only
    `external_uid` (the machine identity, not her data); update/cancel/drop
    entries keep only the local `id` (already just a number); an aggregate
    `counts` block and `collisions` (a bare count) round it out."""
    if not isinstance(changeset, dict):
        return changeset
    events = changeset.get("events") or {}
    plans_ = changeset.get("plans") or {}
    ev_ins = events.get("insert") or []
    ev_upd = events.get("update") or []
    ev_can = events.get("cancel") or []
    pl_ins = plans_.get("insert") or []
    pl_upd = plans_.get("update") or []
    pl_drp = plans_.get("drop") or []
    return {
        "events": {
            "insert": [{"external_uid": e.get("external_uid")} for e in ev_ins],
            "update": [{"id": e.get("id")} for e in ev_upd],
            "cancel": [{"id": e.get("id")} for e in ev_can],
        },
        "plans": {
            "insert": [{"external_uid": e.get("external_uid")} for e in pl_ins],
            "update": [{"id": e.get("id")} for e in pl_upd],
            "drop": [{"id": e.get("id")} for e in pl_drp],
        },
        "collisions": len(changeset.get("collisions") or []),
        "counts": {
            "events_insert": len(ev_ins), "events_update": len(ev_upd),
            "events_cancel": len(ev_can), "plans_insert": len(pl_ins),
            "plans_update": len(pl_upd), "plans_drop": len(pl_drp),
        },
    }


_HREF_IN_TEXT_RE = re.compile(r"https?://\S+")
# Both of Python's `repr()` quoting conventions: single quotes (the common
# case), OR double quotes -- `repr()` switches to double quotes whenever
# the string contains an apostrophe but no `"` (fix-round 2, minor (b)):
# `_expand_master`'s `f"RRULE {master['rrule']!r} for uid=..."` uses `!r`
# on the raw RRULE VALUE text, and an Apple RRULE can itself legitimately
# contain an apostrophe (e.g. inside an X- extension or a stray comment
# some client appended) -- the OLD single-quote-only pattern silently let
# that one shape straight through.
_RRULE_IN_TEXT_RE = re.compile(r"RRULE\s+(?:'[^']*'|\"[^\"]*\")")
# `_expand_master`'s diagnostic also appends `({type(e).__name__}: {e})`
# -- `dateutil.rrulestr`'s OWN exception text (`{e}`), not `!r`-quoted at
# all, can independently echo a fragment of the same raw RRULE value
# (e.g. an "unsupported/invalid token: <fragment>"-shaped message) in a
# form the pattern above never sees, since it never touches the quoted
# literal. This targets that known, fixed diagnostic shape by structure
# (the literal string `_expand_master` builds it with) and drops
# everything after the exception TYPE name, rather than trying to guess
# what shape the un-quoted fragment might take.
#
# Fix-round 3, Minor finding M2: the ORIGINAL pattern closed its capture
# on `[^)]*(\))` -- the FIRST `)` after the exception type/colon. But
# `{e}`'s own free-form text can legitimately contain its OWN, unrelated
# parentheses (e.g. dateutil quoting the bad token as "invalid token
# (FREQ=SECRETLY)") -- the first `)` the old pattern found was that
# INNER one, not the outer diagnostic's real closing paren, so
# everything after it (a second leaked fragment, e.g. "trailing
# FREQ=LEAK") rode straight through unredacted, past where the
# substitution stopped. And if `{e}`'s text happened to contain NO `)`
# at all before the end of the string, `[^)]*(\))` could not match
# ANYTHING -- the whole clause, RRULE fragment included, passed through
# completely unredacted, not merely truncated. Anchored on the actual
# END of the string instead (one such clause per string at every call
# site -- see `_redact_extcal_text`'s own callers): `.*?` lazily
# consumes the whole tail, and the optional `(\)?)` captures a real
# trailing `)` when the text has one (the normal case -- `_expand_
# master`'s own f-string always ends with one) or nothing when it
# doesn't, so the substitution below never leaves a dangling unredacted
# fragment on either side of a nested paren, and never fully skips
# redaction for want of one.
#
# Final re-review (pre-prod hardening): `expand()` catches `Exception`
# broadly around `dateutil.rrulestr`, so `{e}`'s text is NOT guaranteed
# single-line -- a multi-line exception message (e.g. one `dateutil`
# variant that embeds `\n` in its own diagnostic) has bare `.` unable to
# cross the newline without `re.DOTALL`, so `$` (end of string) was
# never reachable and the WHOLE match failed -- not truncated, not
# partially redacted, just skipped entirely, leaking the full multi-line
# tail (RRULE fragment included) into `audit cal.ext.sync.sync_errors`,
# `tick.error`, and from there verbatim into `maint.problem_summary`'s
# nightly message to Denis. `re.DOTALL` makes `.` match `\n` too, so the
# lazy `.*?` can still reach the real end of the string across any
# embedded newlines; the anchor stays end-of-STRING (not `re.MULTILINE`,
# which would instead make `$` match before every internal `\n` and stop
# the redaction short at the first line break).
_EXPAND_ERROR_DETAIL_RE = re.compile(
    r"(could not be parsed/evaluated \(\w+: ).*?(\)?)$", re.DOTALL
)

# Cap on any single redacted diagnostic string landing in audit_log or a
# message to Denis -- matches the bound `cal.ext.export_error.error`
# already used (`extcal.py`'s `_export_commit_one`) for the "same view as
# neighboring channels" this final-review blocker asks for.
_REDACTED_TEXT_MAX = 300


def _redact_extcal_text(text):
    """One redaction pass, reused by every log/print/audit path that might
    carry a fragment of her raw iCloud data (final review, blocker 3):
    an absolute CalDAV resource href (hers, or the "Гермес" write-target's)
    -- `https?://...` -> `<href>` -- and a raw `RRULE` value quoted in an
    `expand()`/`_expand_master` diagnostic (`extcal.py`'s own `!r`-quoted
    `RRULE '<value>' for uid=...` shape, single OR double quoted -- see
    `_RRULE_IN_TEXT_RE`'s own comment) -- `RRULE '...'`/`RRULE "..."` ->
    `RRULE <redacted>`. Fix-round 2 (minor (b)) additionally drops the
    free-form exception-detail text `_expand_master` appends after that
    (`dateutil.rrulestr`'s own `{e}`, which is not `!r`-quoted and can
    independently echo a raw fragment of the RRULE value in a shape the
    quoted pattern never matches) -- only the exception's TYPE name
    survives (`ValueError`, etc.), not its message text (fix-round 3, M2:
    `_EXPAND_ERROR_DETAIL_RE` now anchors on the end of the string rather
    than the first `)` it finds, so a nested paren inside the exception's
    own text can no longer let a tail fragment leak past the
    substitution, and text with no trailing `)` at all still gets
    redacted instead of passing through untouched -- see that pattern's
    own comment). `uid=...` is left alone: it is an opaque identifier,
    not her data, and the design doc's own "только UID и счётчики" rule
    explicitly allows it through. Falsy input passes through unchanged
    (nothing to redact); never raises."""
    if not text:
        return text
    text = _HREF_IN_TEXT_RE.sub("<href>", text)
    text = _RRULE_IN_TEXT_RE.sub("RRULE <redacted>", text)
    # M2: group 1 already carries the trailing ": " (see
    # `_EXPAND_ERROR_DETAIL_RE`'s own comment) -- no extra ": " here.
    text = _EXPAND_ERROR_DETAIL_RE.sub(r"\1<redacted>\2", text)
    return text


def _redact_sync_errors(sync_errors):
    """`sync_errors` list -> redacted + length-capped copy (final review,
    blocker 3): previously only ever used for `--dry-run` printing
    (fix-round 2, minor #4, hence this function's former name
    `_redact_sync_errors_for_dry_run`) -- now ALSO used on the production
    (non-dry-run) `cal.ext.sync` audit write and the `tick.error` message
    built from the same list, which is what a real sync failure's
    diagnostic text rides all the way into `maint.problem_summary`'s
    nightly message to Denis on. One implementation, every caller that
    needs it, per this project's own "no second implementation" rule.
    The calendar name/mode/counts around each entry stay (that's the
    whole diagnostic point of printing/auditing these at all) -- only an
    embedded href or raw RRULE value is stripped, and the result is
    capped at `_REDACTED_TEXT_MAX` chars (a long fallback path -- e.g. a
    verbose foreign HTTP error body wrapped into the message -- must not
    make the audit row/nightly message unbounded either)."""
    return [_redact_extcal_text(e)[:_REDACTED_TEXT_MAX] for e in (sync_errors or [])]


# `extcal_full_resync_days` bounds -- final re-review (pre-prod
# hardening): the value comes straight from `fam-config.json` (`gate.py`
# default 1) and used to go directly into `timedelta(days=...)` below
# with no validation at all. Two unguarded failure modes: (1) `0` or a
# negative value makes `force_full` (`now - last_full_dt) >= timedelta
# (days=N)`) true on EVERY tick forever instead of roughly once a day --
# a full `calendar-query` re-baseline every 15 minutes, not the rare/
# cheap path the whole periodic-full design (fix-round 3, C1) counted
# on; (2) anything non-numeric (a stray string, `null`, etc.) blows up
# `timedelta(days=...)` with a `TypeError` that the broad `except
# Exception` in `cmd_tick_cal_ext` turns into a `tick.error` -- syncing
# stays dead every 15 minutes until a human edits the config by hand.
# Clamped to a sane [1, 30]-day range and defaulted (not raised) on
# anything that doesn't coerce to an int, so a bad config value degrades
# to "sync keeps working with the default cadence" instead of either
# runaway full-resyncs or a wedged tick.
_EXTCAL_FULL_RESYNC_DAYS_DEFAULT = 1
_EXTCAL_FULL_RESYNC_DAYS_MIN = 1
_EXTCAL_FULL_RESYNC_DAYS_MAX = 30


def _clamp_int_config(cfg, key, default, lo, hi):
    """Shared validation for a small-int config knob read straight from
    `fam-config.json`: coerce to `int`, clamp into `[lo, hi]`, and fall
    back to `default` (never raise) on anything that doesn't coerce --
    missing key, `None`, a non-numeric string, etc. Pulled out of
    `_extcal_full_resync_days` (see that function's own comment for the
    two concrete failure modes an unvalidated config value used to hit:
    a runaway zero/negative cadence, or a bare `TypeError` wedging the
    tick) so `_extcal_fail_streak_threshold` below reuses the exact same
    coercion/clamp/fallback shape instead of a second copy of it."""
    raw = cfg.get(key, default)
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, val))


def _extcal_full_resync_days(cfg):
    """`cfg["extcal_full_resync_days"]` -> a valid int in `[1, 30]`, never
    raises. A missing key or anything that doesn't coerce to `int`
    (non-numeric string, `None`, ...) falls back to the same default
    (`1`) `gate.CONFIG_DEFAULTS` already documents; an in-range numeric
    value (including a `float`, truncated) passes straight through;
    zero/negative or too-large values are clamped to the nearest bound
    rather than silently accepted (see the module-level comment above
    for why both directions matter)."""
    return _clamp_int_config(cfg, "extcal_full_resync_days",
                              _EXTCAL_FULL_RESYNC_DAYS_DEFAULT,
                              _EXTCAL_FULL_RESYNC_DAYS_MIN,
                              _EXTCAL_FULL_RESYNC_DAYS_MAX)


# `extcal_fail_streak_threshold` (streak-alerting hardening, 2026-08):
# a live-prod week (4 tick.error escalations across ~670 ticks, all four
# transient and self-healed by the very next tick -- a one-off iCloud
# stall on a single href, or one bad `discover()` round) showed that
# escalating EVERY tick error into the nightly `maint.problem_summary`
# trains Denis to ignore the channel, so a real multi-tick outage would
# be missed too. This is how many CONSECUTIVE failing ticks (per
# calendar URL, or per one of the two sentinel keys below) it takes
# before `cmd_tick_cal_ext` escalates to `tick.error` + exit 1 -- see
# `_extcal_record_failure`/`_extcal_record_success`. Validated with the
# exact same shape as `_extcal_full_resync_days` (clamp + safe default on
# garbage, never raise): a missing/non-numeric config value must not
# wedge the tick, and neither must an absurdly large one silently turn
# the whole feature off.
_EXTCAL_FAIL_STREAK_THRESHOLD_DEFAULT = 3
_EXTCAL_FAIL_STREAK_THRESHOLD_MIN = 1
_EXTCAL_FAIL_STREAK_THRESHOLD_MAX = 50

# Sentinel streak keys for failure classes that have no calendar URL of
# their own to hang a per-calendar counter on: `extcal_read_calendars`
# configured but 0 calendars matched (discover() degrades to `[]` on ANY
# failure -- missing credentials, timeout, 5xx, or a genuinely renamed
# calendar, see `_cal_ext_sync`'s own comment), and `apply_changes`/
# `export_own` per-row errors (no single calendar to blame -- these are
# keyed by branch/id or event_id, not calendar URL). Both share the
# double-underscore shape specifically so neither can ever collide with a
# real CalDAV URL (which always contains "://").
_EXTCAL_STREAK_DISCOVERY_KEY = "__discovery__"
_EXTCAL_STREAK_APPLY_KEY = "__apply__"

# Cap on how many bodies ONE tick may re-fetch one-by-one after a delta
# entry arrived without <C:calendar-data> (see `_cal_ext_sync`). A
# collection where every resource is unreadable must not turn every
# 15-minute tick into a hundred-request storm against iCloud. Nothing
# is lost by stopping at the cap: whatever is left over holds that
# calendar's sync-token back, so the server repeats the very same
# delta on the next tick instead of considering it acknowledged.
_BAD_HREF_REFETCH_LIMIT = 20


def _extcal_fail_streak_threshold(cfg):
    """`cfg["extcal_fail_streak_threshold"]` -> a valid int in `[1, 50]`,
    never raises -- same coercion/clamp/default shape as
    `_extcal_full_resync_days` via the shared `_clamp_int_config`
    helper."""
    return _clamp_int_config(cfg, "extcal_fail_streak_threshold",
                              _EXTCAL_FAIL_STREAK_THRESHOLD_DEFAULT,
                              _EXTCAL_FAIL_STREAK_THRESHOLD_MIN,
                              _EXTCAL_FAIL_STREAK_THRESHOLD_MAX)


def _extcal_streak_meta_key(key):
    return f"extcal_fail_streak:{key}"


def _extcal_alert_meta_key(key):
    return f"extcal_fail_alerted:{key}"


def _extcal_record_failure(conn, key, threshold):
    """Increment the persistent (meta-backed, survives restart)
    consecutive-failure streak for `key` (a calendar URL, or one of the
    `_EXTCAL_STREAK_*_KEY` sentinels above); return True iff this round's
    streak just crossed `threshold` AND no alert is outstanding for it
    yet. Edge-triggered exactly like `car.maybe_alert_staleness` /
    `health.maybe_alert_readiness` (ok/below-threshold -> above-threshold
    alerts once; staying above threshold stays silent; a call to
    `_extcal_record_success` below is the only thing that clears the
    alert flag, so the next failing streak can alert again)."""
    raw = famdb.meta_get(conn, _extcal_streak_meta_key(key), "0")
    try:
        streak = int(raw)
    except (TypeError, ValueError):
        streak = 0
    streak += 1
    famdb.meta_set(conn, _extcal_streak_meta_key(key), str(streak))
    if streak < threshold:
        return False
    if famdb.meta_get(conn, _extcal_alert_meta_key(key), "0") == "1":
        return False  # already alerted for this ongoing streak -- stay silent
    famdb.meta_set(conn, _extcal_alert_meta_key(key), "1")
    return True


def _extcal_record_success(conn, key):
    """Clear the streak and alert flag for `key` on a clean round --
    mirrors the reset half of `car.maybe_alert_staleness` /
    `health.maybe_alert_readiness`, so the next failing streak starts
    from zero and can alert again."""
    famdb.meta_set(conn, _extcal_streak_meta_key(key), "0")
    famdb.meta_set(conn, _extcal_alert_meta_key(key), "0")


def _cal_ext_sync(conn, cfg, now, dry_run):
    """The whole read -> reconcile -> (write) pipeline for one `fam tick
    cal-ext` run. Returns `{"counts": dict|None (None on --dry-run),
    "calendars": [{url,name,mode,reason}, ...], "changeset": Changeset,
    "sync_errors": [str, ...], "tokens": {calendar_url: token},
    "full_mode_urls": {calendar_url, ...}}` -- the last is every calendar
    that ran in ANY full mode this round (fix-round 3, C1 -- see
    `cmd_tick_cal_ext`'s own `extcal_last_full` persistence).

    ALWAYS runs the full pipeline through to `plan_changes` (and
    `apply_changes`, unless `dry_run`) -- fix-round 1 removed the earlier
    "every calendar errored -> return early" special case entirely. With
    the per-row snapshot exclusion described below, an empty/all-failed
    `eligible` list naturally produces an empty `local_snapshot`
    contribution and an empty changeset -- a safe no-op -- so there is no
    longer any need for a separate early-exit branch, and
    `cmd_tick_cal_ext` (not this function) is solely responsible for
    deciding pass/fail from `calendars`/`sync_errors`/`counts["errors"]`.

    Per-row snapshot-inclusion rule (fix-round 1 Critical findings C1/C3,
    NARROWED in fix-round 2 findings N1/N2 -- see below), replacing the
    first cut's "default to including a row unless we know better": an
    owner='iphone' row is only ever offered to plan_changes THIS round --
    i.e. only ever a disappearance CANDIDATE -- when ALL of the following
    hold, checked in `_include_iphone_row` below:
      0. either its own `external_href` is not in `bad_hrefs`, OR its own
         `external_uid` key WAS one of the occurrence keys this round
         actually reproduced from that href (`occ_keys_by_href`,
         fix-round 3 cheap fix #2 -- see below). `bad_hrefs` (fix-round
         2, findings N1/N2, case/newline-fixed in fix-round 3 finding R1)
         marks a specific RESOURCE this round could not fully trust:
         `parse_ics` returned FEWER components than the resource's own
         raw `BEGIN:VEVENT` block count (fix-round 1's per-component
         uid/dtstart_utc re-check after parse_ics returns could never
         actually fire -- parse_ics/`_finalize_component` already
         guarantee every returned Component has both; the real silent
         loss happens INSIDE parse_ics, e.g. a resource holding a
         recurring master together with its RECURRENCE-ID overrides,
         where one override's broken DTSTART is dropped there while a
         healthy master in the SAME resource survives), a live
         (non-deleted) item with NO `ics` text at all (fix-round 3,
         finding R2 -- a per-resource 403/500/507 or a missing/empty
         `<C:calendar-data>`, NOT a real deletion, despite
         `extcal.fetch_changes`'s docstring formerly claiming otherwise),
         or one `expand()` error attributable to this specific resource
         (a broken RRULE on ONE master, via the uid `_expand_master`'s
         own error message embeds). A block-count mismatch specifically
         is scoped down FURTHER, to the occurrence(s) actually missing
         (cheap fix #2): a healthy master sharing a bad resource with a
         lost override still produced its OWN occurrence this round, so
         its existing row stays a normal update candidate -- without
         this, that row would be excluded from the snapshot (and
         therefore stop accepting title/time edits) for as long as the
         resource stays broken, AND `apply_changes`' own idempotency
         guard would silently re-absorb its re-queued "insert" as
         `insert_skipped_duplicate` every 15 minutes, writing an audit
         row per recurring instance per tick into the same WAL file m6's
         `RandomizedDelaySec` exists to de-contend. Either way, a
         healthy sibling resource in the same calendar -- or a healthy
         occurrence sharing this SAME resource with a lost one -- must
         not be held hostage by it;
      1. its own calendar is identifiable at all (`external_href` matches
         one of THIS round's `eligible` calendars by URL prefix) -- a row
         under NO eligible calendar is EXCLUDED, not included by default
         (C1(b)/I4: removing a calendar from `extcal_read_calendars`, or
         discover()/fetch_changes failing so hard that `eligible` itself
         comes back empty, must never look like "she deleted everything"
         -- extcal.discover() degrades to `[]` on ANY failure -- missing
         credentials, timeout, 5xx, a renamed calendar no longer matching
         the config filter -- unconditionally, per its own docstring);
      2. that calendar's fetch succeeded this round (not in `errored_urls`)
         AND its ICS/expand pass had no BATCH-WIDE problem (not in
         `degraded_urls`) -- narrowed in fix-round 2 to ONLY the class of
         `expand()` error that genuinely cannot be attributed to one
         resource: `python-dateutil` not installed (zeroes out EVERY
         RRULE master in the batch at once), or a malformed-window/
         unexpected failure carrying no uid at all. Unlike `bad_hrefs`
         above, this is NOT a "this round only" state (fix-round 2,
         finding N2): nothing about a missing dependency or a genuinely
         malformed request self-heals between ticks, so a calendar
         marked here stays excluded -- and its token frozen (see the
         token-seeding note below) -- until an operator actually fixes
         the underlying problem;
      3. if that calendar ran in a FULL mode this round (calendar-query
         over the whole horizon -- exhaustive listing, so `plan_changes`'
         own "not seen in this batch -> vanished" logic is correct on its
         own terms) -- included; OR the calendar ran incrementally (REPORT
         sync-collection, RFC 6578: `items` are only what changed or was
         deleted since the last token) AND this row's own `external_href`
         is one this round's delta actually mentioned (changed OR deleted
         -- a sync-collection tombstone is an explicit 404 entry, see
         extcal._parse_multistatus_items, so a real phone-side deletion IS
         visible here, just as a bare href with no ICS to parse) --
         included. Otherwise (incremental mode, href simply wasn't touched
         by this round's small delta) -- EXCLUDED: an unscoped inclusion
         here would make every row untouched since the last sync look
         "vanished" on every single steady-state 15-minute tick forever,
         since an incremental delta's `items` were never a full listing to
         begin with.

    Sync-token seeding (live-probe finding, 2026-07-29): `fetch_changes`
    with `sync_token=None` (first sync for a calendar) returns
    mode="initial_full" with an EMPTY `new_token` -- sync-collection was
    never even attempted, so there is nothing fresh to persist from that
    call itself. The token to seed for NEXT time is `calendar["sync_token"]`
    -- discover()'s OWN read of the calendar's current sync-token,
    captured BEFORE this round's full read below runs (a token captured
    only after would silently miss any edit landing in the window between
    discover() and this tick finishing). Only "sync_collection" mode
    persists ITS OWN returned `new_token`. A calendar that errored OR was
    marked degraded this round never gets a candidate token at all here
    (`tokens` simply omits its URL) -- `cmd_tick_cal_ext` applies one
    FURTHER, blanket gate on top of that (fix-round C2): if
    `apply_changes` reported ANY per-row error this tick, NO token is
    persisted for ANY calendar, not just the one an error can be
    attributed to -- simpler and safer than trying to trace one
    `_apply_one` failure back to the specific href/calendar it came from,
    and an already-idempotent re-read next tick is harmless either way.
    """
    window_start, window_end = _extcal_window(cfg, now)
    window_start_iso, window_end_iso = window_start.isoformat(), window_end.isoformat()
    window_start_date = window_start.astimezone(extcal.ALMATY).date().isoformat()
    window_end_date = window_end.astimezone(extcal.ALMATY).date().isoformat()
    now_iso = now.isoformat()

    calendars = extcal.discover(cfg)
    eligible = _extcal_eligible_calendars(cfg, calendars)

    per_calendar = []
    combined_occurrences = []
    sync_errors = []
    key_meta = {}                  # bare ICS uid -> {"href", "etag"}
    full_mode_urls = set()
    incremental_hrefs = {}         # calendar url -> set of hrefs this batch touched
    errored_urls = set()
    degraded_urls = set()          # a BATCH-WIDE problem (fix-round 2: NOT attributable
                                    # to one resource -- see the per-calendar loop below)
    bad_hrefs = set()              # ONE resource's data is untrustworthy this round
                                    # (fix-round 2, findings N1/N2)
    tokens_to_persist = {}         # calendar url -> token (only clean, successful calendars)
    token_unsafe_urls = set()      # 2026-08-21: calendars holding at least one delta
                                    # entry we could not read AT ALL this round --
                                    # their sync-token must NOT advance (see the
                                    # "no calendar-data" branch below)
    refetched = 0                  # bodies re-fetched this tick, capped by
                                    # _BAD_HREF_REFETCH_LIMIT (whole tick, not per calendar)
    # Streak-alerting hardening: per-calendar error tracking THIS round,
    # keyed by calendar url -- fed to `cmd_tick_cal_ext`'s consecutive-
    # failure counters (`_extcal_record_failure`/`_extcal_record_success`)
    # so a transient, self-healing blip on one calendar doesn't escalate
    # to `tick.error` on the first occurrence, while still being scoped
    # to exactly that calendar (a healthy sibling calendar's own counter
    # must not be touched by it).
    calendar_had_error = {}        # calendar url -> bool
    calendar_error_msgs = {}       # calendar url -> [str, ...]

    def _note_calendar_error(url, msg):
        calendar_had_error[url] = True
        calendar_error_msgs.setdefault(url, []).append(msg)
        sync_errors.append(msg)

    # C1(a): extcal_read_calendars configured but nothing eligible survived
    # discover()/filtering -- discover() degrades to [] on ANY failure
    # (missing credentials, timeout, 5xx, malformed XML) as well as on a
    # genuinely-renamed calendar no longer matching the filter, and those
    # two cases must not be silently indistinguishable from "nothing to
    # do" when a filter is actually configured.
    discovery_error = bool(not eligible and cfg.get("extcal_read_calendars"))
    if discovery_error:
        sync_errors.append(
            "extcal_read_calendars is configured but matched 0 of "
            f"{len(calendars)} discovered calendar(s) -- check config "
            "spelling/case, credentials, or network reachability")

    # Fix-round 3, Critical finding C1 (the rolling-horizon gap): a
    # calendar left running on `REPORT sync-collection` deltas forever
    # never re-materializes an occurrence whose resource nobody has
    # touched since the window last moved past it -- a rolling `expand()`
    # window can silently stop inserting NEW occurrences of an untouched
    # recurring series. `extcal_full_resync_days` (default 1, gate.py)
    # bounds how long a calendar may go without a full re-baseline;
    # `meta["extcal_last_full:<url>"]` (below) is the per-calendar
    # watermark, advanced only when this round's full pass actually
    # completed cleanly (same gate as sync-token persistence -- see
    # `cmd_tick_cal_ext`). No marker yet for a calendar that DOES already
    # have a stored sync-token (e.g. right after this fix first deploys)
    # is treated the same as "overdue" -- self-healing, not a special
    # case: the safest assumption about an unknown-age token is that it
    # might already be stale. Final re-review: clamped/defaulted via
    # `_extcal_full_resync_days` (see that function's own comment) --
    # zero/negative or non-numeric config no longer forces a full pass
    # every tick or wedges the sync with a `TypeError`.
    full_resync_days = _extcal_full_resync_days(cfg)

    for calendar in eligible:
        url = calendar.get("url")
        calendar_had_error[url] = False
        stored_token = famdb.meta_get(conn, f"extcal_sync_token:{url}")
        # Captured BEFORE fetch_changes() below runs -- see this
        # function's own docstring, "Sync-token seeding".
        candidate_token = calendar.get("sync_token")

        force_full = False
        if stored_token:
            last_full_raw = famdb.meta_get(conn, f"extcal_last_full:{url}")
            if not last_full_raw:
                force_full = True
            else:
                try:
                    last_full_dt = datetime.fromisoformat(last_full_raw)
                except ValueError:
                    force_full = True
                else:
                    if last_full_dt.tzinfo is None:
                        last_full_dt = last_full_dt.replace(tzinfo=timezone.utc)
                    force_full = (now - last_full_dt) >= timedelta(days=full_resync_days)

        items, new_token, sync_info = extcal.fetch_changes(
            cfg, calendar, sync_token=stored_token, force_full=force_full)
        mode = sync_info.get("mode") if isinstance(sync_info, dict) else "error"
        reason = sync_info.get("reason") if isinstance(sync_info, dict) else "unknown"
        per_calendar.append({"url": url, "name": calendar.get("name"),
                              "mode": mode, "reason": reason})

        if mode == "error":
            errored_urls.add(url)
            _note_calendar_error(url, f"{calendar.get('name') or url}: {reason}")
            continue

        base = _extcal_url_base(url)
        batch_hrefs = set()
        components = []
        batch_wide_degraded = False
        for item in (items or []):
            href = item.get("href")
            abs_href = urljoin(base, href) if (base and href) else href
            if abs_href:
                batch_hrefs.add(abs_href)
            if item.get("deleted"):
                continue  # tombstone: no ICS to parse -- disappearance below handles it
            ics_text = item.get("ics")
            # Whether the "no calendar-data" branch below actually got
            # to try a re-fetch, so its error message can tell the two
            # very different situations apart: the resource is truly
            # unreadable, or this tick simply ran out of its re-fetch
            # budget. Both hold the token back; only the first is a
            # reason to go look at the server.
            refetch_attempted = False
            if not (ics_text or "").strip():
                # Fix-round 3, finding R2: extcal.fetch_changes' OWN
                # docstring used to claim `ics=None` only ever happens for
                # a deleted (tombstoned) item -- it doesn't.
                # _parse_multistatus_items returns `ics=None` for ANY
                # non-404 response too (a per-resource 403/500/507 inside
                # an otherwise-200 multistatus, or a 200 whose
                # <C:calendar-data> is simply missing/empty). Treating
                # this silently as "nothing to do" -- the fix-round 2 cut
                # of this line -- let such a resource's local row fall
                # straight through to plan_changes' disappearance sweep
                # with no error recorded at all: cancel, tombstone,
                # unrecoverable. Excluded BY HREF instead, same as any
                # other unreadable resource this round.
                #
                # Fix-round 4: `if not ics_text` alone does not catch a
                # whitespace-only string (e.g. a pretty-printed multistatus
                # XML response whose `<C:calendar-data>\n   </C:calendar-
                # data>` has indentation but no real content) -- that
                # string is truthy, so it used to fall through to
                # parse_ics_with_count below, which (correctly, per its own
                # contract) returns `([], 0)` for it: 0 == 0, no mismatch,
                # no error, and the href had already gone into batch_hrefs
                # above -- exactly the "normal, empty VTODO" shape in
                # sync_collection mode, silently clearing the way to the
                # disappearance sweep. `.strip()` closes that.
                #
                # 2026-08-21: a missing body is no longer accepted as
                # "nothing to do this round". The entry represents a REAL
                # change the server told us about EXACTLY ONCE -- RFC 6578
                # never repeats a delta whose sync-token we acknowledged --
                # so skipping it while still persisting that token loses
                # the change until the next `periodic_full`, a whole day
                # by default. Observed live on 2026-08-20: a booking
                # created in iCloud at 12:34 UTC was still absent from
                # assistant.db five hours and twenty clean ticks later.
                # The comment above is right that a local row must not be
                # tombstoned on unreadable data -- but that only protects
                # an UPDATE; an INSERT has no local row to protect, and
                # was silently dropped.
                #
                # So: first try to read the resource directly (one plain
                # GET, `extcal.fetch_resource`). Only if that fails too do
                # we fall back to skipping it -- and then this calendar's
                # sync-token is held back (`token_unsafe_urls`), so the
                # server repeats the same delta on the next tick rather
                # than considering it delivered.
                if abs_href and refetched < _BAD_HREF_REFETCH_LIMIT:
                    refetched += 1
                    refetch_attempted = True
                    ics_text = extcal.fetch_resource(cfg, abs_href)
            if not (ics_text or "").strip():
                bad_hrefs.add(abs_href)
                token_unsafe_urls.add(url)
                why = ("re-fetch failed too" if refetch_attempted else
                       f"not re-fetched, per-tick cap of "
                       f"{_BAD_HREF_REFETCH_LIMIT} reached")
                _note_calendar_error(
                    url,
                    f"{calendar.get('name') or url}: no calendar-data "
                    f"for {abs_href} ({why} -- treated as unreadable "
                    f"this round, not gone; sync-token held back so "
                    f"this delta is asked for again)")
                continue
            # Fix-round 4: `parse_ics_with_count` (extcal.py) -- NOT a
            # second, independent line-counter in this module (see the
            # comment above this loop) -- returns the VEVENT block count
            # computed by the SAME pass that decides component boundaries.
            parsed, begin_count = extcal.parse_ics_with_count(ics_text)
            # Fix-round 2, finding N1: a per-component uid/dtstart_utc
            # check AFTER parse_ics returns can never fire -- parse_ics/
            # _finalize_component already guarantee every returned
            # Component has both. The real silent loss happens INSIDE
            # parse_ics (a resource holding a recurring master together
            # with its RECURRENCE-ID overrides: an override with a
            # broken/missing DTSTART is dropped there while a healthy
            # master in the SAME resource survives) -- the only
            # externally-observable signal is a component COUNT mismatch
            # against this resource's own raw BEGIN:VEVENT blocks.
            if begin_count == 0:
                # Fix-round 3, cheap fix: a resource with ZERO BEGIN:VEVENT
                # blocks is only suspicious when the query that fetched it
                # PROMISED VEVENT content -- a FULL-mode read
                # (calendar-query) server-side filters by `comp-filter
                # name="VEVENT"` (extcal._QUERY_BODY_TMPL), so a 0-VEVENT
                # result there means something we used to be able to
                # parse no longer parses. REPORT sync-collection has NO
                # such filter (RFC 6578: it returns every changed resource
                # in the collection regardless of component type) -- a
                # changed VTODO/VJOURNAL sitting in the same subscribed
                # calendar is completely normal there and must not fail
                # the whole tick (the fix-round 2 cut flagged this
                # unconditionally, a false positive on any non-VEVENT
                # resource in an incremental delta).
                if mode != "sync_collection":
                    bad_hrefs.add(abs_href)
                    _note_calendar_error(
                        url,
                        f"{calendar.get('name') or url}: 0 VEVENT block(s) "
                        f"found for {abs_href} despite a VEVENT-filtered "
                        f"query (mode={mode})")
                continue
            if len(parsed) < begin_count:
                # Excluded BY HREF (finding N2), not the whole calendar --
                # a healthy SIBLING resource in the same calendar (or,
                # within THIS resource, a healthy master alongside a lost
                # override) must not be held hostage by one bad one. See
                # `occ_keys_by_href` below (fix-round 3, cheap fix #2):
                # this only ends up excluding the occurrence(s) we truly
                # have no fresh data on, not every occurrence sharing this
                # href.
                bad_hrefs.add(abs_href)
                _note_calendar_error(
                    url,
                    f"{calendar.get('name') or url}: parse_ics returned "
                    f"{len(parsed)} of {begin_count} VEVENT block(s) "
                    f"(dropped {begin_count - len(parsed)}) for {abs_href}")
            for comp in parsed:
                uid = comp.get("uid")
                if uid and _EXTCAL_ECHO_UID_RE.match(uid):
                    continue  # anti-echo belt 2 (design doc invariant #4)
                components.append(comp)
                key_meta.setdefault(uid, {"href": abs_href, "etag": item.get("etag")})

        expanded = extcal.expand(components, window_start, window_end)
        combined_occurrences.extend(expanded["occurrences"])
        for err in expanded["errors"]:
            _note_calendar_error(url, f"{calendar.get('name') or url}: {err}")
            m = _EXPAND_ERROR_UID_RE.search(err)
            meta = key_meta.get(m.group(1)) if m else None
            if meta and meta.get("href"):
                # Attributable to ONE specific resource (a broken RRULE
                # on ONE master, per _expand_master's own error format) --
                # excluded by href, same granularity as the parse_ics
                # drop above (finding N2).
                bad_hrefs.add(meta["href"])
            else:
                # NOT attributable to any one resource -- e.g. "python-
                # dateutil not installed" zeroes out EVERY RRULE master in
                # this calendar's batch at once, or a malformed-window
                # failure carrying no uid at all. This is the one class
                # of failure this per-href granularity genuinely cannot
                # narrow further -- the WHOLE calendar is marked degraded,
                # and (finding N2) stays that way UNTIL SOMEONE FIXES THE
                # UNDERLYING PROBLEM (installs dateutil, etc.) -- NOT
                # merely "this round": nothing about the environment or
                # the malformed data self-heals on its own between ticks.
                batch_wide_degraded = True

        # `token_unsafe_urls` joins `batch_wide_degraded` here rather
        # than getting a gate of its own: both mean the same thing to
        # the token -- this round did NOT fully consume what the server
        # handed us, so acknowledging it would lose data. Riding the
        # same `degraded_urls` set also keeps `extcal_last_full` in
        # step (`cmd_tick_cal_ext` gates that watermark on this very
        # `tokens_to_persist` membership).
        if batch_wide_degraded or url in token_unsafe_urls:
            degraded_urls.add(url)
        else:
            tokens_to_persist[url] = (
                new_token if mode == "sync_collection" else candidate_token)

        if mode == "sync_collection":
            incremental_hrefs[url] = batch_hrefs
        else:
            full_mode_urls.add(url)

    # Fan bare-uid href/etag out to every concrete occurrence key it
    # produced (extcal._occurrence_key -- the SAME length-prefixed
    # encoding local rows' own external_uid column uses, see extcal.py's
    # module note): one recurring master's single ICS resource expands
    # into MANY occurrences sharing one href/etag. `occ_keys_by_href`
    # (fix-round 3, cheap fix #2) is the SAME fan-out, indexed the other
    # way -- every occurrence key successfully produced FROM a given
    # href this round -- so a bad_hrefs exclusion below can be scoped to
    # just the occurrence(s) actually missing, not every occurrence that
    # happens to share a resource with them.
    occ_key_meta = {}
    occ_keys_by_href = {}
    for occ in combined_occurrences:
        uid = occ.get("uid")
        if not uid or uid not in key_meta:
            continue
        key = extcal._occurrence_key(uid, occ.get("recurrence_id"))
        meta = dict(key_meta[uid])
        meta["seq"] = occ.get("seq") or 0
        occ_key_meta[key] = meta
        href = meta.get("href")
        if href:
            occ_keys_by_href.setdefault(href, set()).add(key)

    def _include_iphone_row(row):
        href = row.get("external_href")
        if href and href in bad_hrefs:
            # Fix-round 3, cheap fix #2: bad_hrefs marks a RESOURCE, not
            # necessarily every occurrence inside it -- a healthy master
            # sharing a resource with a lost RECURRENCE-ID override (N1's
            # own scenario) still produced its OWN occurrence this round
            # (it's in occ_keys_by_href[href]) and should stay a normal
            # update candidate; only a row whose key was NOT reproduced
            # this round is the piece we actually have no fresh data on.
            # Without this narrowing, the healthy master's existing row
            # would be excluded from the snapshot every tick, its own
            # edits would stop applying while the resource stays broken,
            # AND plan_changes would re-queue it as a fresh "insert" that
            # apply_changes' own idempotency guard silently no-ops
            # (`insert_skipped_duplicate`) EVERY 15 MINUTES -- an audit
            # row per recurring instance, per tick, forever, in the same
            # WAL file m6's RandomizedDelaySec was trying to de-contend.
            if row.get("external_uid") not in occ_keys_by_href.get(href, ()):
                return False  # THIS occurrence's data was untrustworthy this round
        calendar_url = _extcal_row_calendar_url(row, eligible)
        if calendar_url is None:
            return False  # no eligible calendar claims this row this round
        if calendar_url in errored_urls or calendar_url in degraded_urls:
            return False  # no TRUSTWORTHY data this round -- leave it alone
        if calendar_url in full_mode_urls:
            return True  # exhaustive listing -- natural disappearance is correct
        return href in incremental_hrefs.get(calendar_url, ())

    iphone_events = [r for r in _extcal_iphone_rows(
                        conn, "events", "start_utc", window_start_iso, window_end_iso)
                     if _include_iphone_row(r)]
    iphone_plans = [r for r in _extcal_iphone_rows(
                        conn, "plans", "deadline", window_start_date, window_end_date)
                    if _include_iphone_row(r)]
    hermes_events = _extcal_hermes_rows(
        conn, "events", "start_utc", "active", window_start_iso, window_end_iso)
    hermes_plans = _extcal_hermes_rows(
        conn, "plans", "deadline", "open", window_start_date, window_end_date)

    events_id_to_key = {r["id"]: r.get("external_uid") for r in iphone_events}
    plans_id_to_key = {r["id"]: r.get("external_uid") for r in iphone_plans}

    local_snapshot = {
        "events": iphone_events + hermes_events,
        "plans": iphone_plans + hermes_plans,
    }

    changeset = extcal.plan_changes(combined_occurrences, local_snapshot, now_iso)

    # T6's job (extcal._apply_event_insert/_apply_event_update's own
    # docstrings): attach external_href/external_etag/external_seq -- a
    # Changeset's insert/update entries never carry these on their own.
    for entry in changeset["events"]["insert"]:
        meta = occ_key_meta.get(entry.get("external_uid"))
        if meta:
            entry["external_href"] = meta["href"]
            entry["external_etag"] = meta["etag"]
    for entry in changeset["events"]["update"]:
        meta = occ_key_meta.get(events_id_to_key.get(entry.get("id")))
        if meta:
            entry["external_href"] = meta["href"]
            entry["external_etag"] = meta["etag"]
            entry["external_seq"] = meta["seq"]
    for entry in changeset["plans"]["insert"]:
        meta = occ_key_meta.get(entry.get("external_uid"))
        if meta:
            entry["external_href"] = meta["href"]
            entry["external_etag"] = meta["etag"]
    for entry in changeset["plans"]["update"]:
        meta = occ_key_meta.get(plans_id_to_key.get(entry.get("id")))
        if meta:
            entry["external_href"] = meta["href"]
            entry["external_etag"] = meta["etag"]

    if dry_run:
        return {
            "counts": None, "calendars": per_calendar,
            "changeset": changeset, "sync_errors": sync_errors, "tokens": {},
            "export_counts": None, "full_mode_urls": set(),
            "calendar_had_error": calendar_had_error,
            "calendar_error_msgs": calendar_error_msgs,
            "discovery_error": discovery_error,
        }

    counts = extcal.apply_changes(conn, changeset, cfg)
    # Task 7: reverse write. Runs AFTER the import-side apply_changes above
    # (both share this same `conn` -- apply_changes already committed every
    # one of its own entries by the time this starts, so there is no
    # pending-transaction interaction between the two directions) and
    # regardless of whether apply_changes itself hit any errors: import and
    # export are independent directions over independent row sets
    # (owner='iphone' vs owner='hermes'), so a problem in one must not
    # withhold the other. `extcal.export_own` is itself a hard no-op (zero
    # DB reads, zero network calls) whenever `extcal_write_calendar` is
    # unset -- which it is on every VM until T10 (a separate, later task)
    # actually creates the "Гермес" collection and fills in the config key.
    export_counts = extcal.export_own(conn, cfg, now_utc=now)
    return {
        "counts": counts, "calendars": per_calendar,
        "changeset": changeset, "sync_errors": sync_errors,
        "tokens": tokens_to_persist, "export_counts": export_counts,
        # Fix-round 3, C1: every calendar that ran in ANY full mode this
        # round (initial_full/fallback_full/periodic_full -- anything
        # that isn't "sync_collection") -- `cmd_tick_cal_ext` advances
        # `meta["extcal_last_full:<url>"]` for whichever of these also
        # made it into `tokens_to_persist` (i.e. NOT batch-wide degraded
        # this round -- the same "healthy this round" set token
        # persistence already uses).
        "full_mode_urls": full_mode_urls,
        # Streak-alerting hardening -- see the module-level dict inits
        # above and `cmd_tick_cal_ext`'s own use of these three.
        "calendar_had_error": calendar_had_error,
        "calendar_error_msgs": calendar_error_msgs,
        "discovery_error": discovery_error,
    }


def cmd_tick_cal_ext(args):
    """`fam tick cal-ext [--now ISO] [--dry-run] [--json]` -- Task 6: wires
    extcal.py's discover/fetch_changes/parse_ics/expand/plan_changes/
    apply_changes layers into one periodic sync (fam-cal-ext.timer, every
    15 min).

    `extcal_enabled=false` (the default) is a hard, zero-action no-op --
    exit 0, discover()/fetch_changes() never even called (no network touch
    at all), matching cmd_tick_offsite's own disabled-is-a-noop contract
    above.

    `--dry-run` runs the FULL read -> reconcile pipeline for real (it is
    read-only/pure by construction: discover, fetch_changes, parse_ics,
    expand, plan_changes) but returns BEFORE apply_changes/meta writes --
    nothing lands in the DB or in iCloud. The printed changeset is
    REDACTED (`_dry_run_summary`, fix-round finding m3): external_uid/id
    and counts only, never her event titles or raw LOCATION text; the
    printed `sync_errors` are ALSO redacted (`_redact_sync_errors`,
    fix-round 2 minor #4) -- a full CalDAV resource href or a raw RRULE
    value would otherwise leak in plain text. `--dry-run` ALWAYS exits 0,
    deliberately, even when `sync_errors` is non-empty (fix-round 2,
    minor #4): it is a diagnostic preview, nothing was written either
    way, so there is nothing an exit code needs to protect here -- a
    human reads the printed output instead.

    Final-review blocker 3 (Important, privacy): `_redact_sync_errors`
    (renamed from `_redact_sync_errors_for_dry_run` -- it is no longer
    dry-run-only) is now ALSO applied to the production `cal.ext.sync`
    audit write and to the `sync_errors` fed into this tick's own
    `tick.error` message below: that message's `error` field is what
    `maint.problem_summary` copies VERBATIM into the nightly text sent to
    Denis, so an unredacted href/RRULE there was a real leak into a
    message she is not the audience for, not just an audit-log detail.

    Fix-round 1 (Opus review finding I1): ANY error this tick -- one
    calendar's fetch failing (partial) or every calendar's (total), a
    parse/expand problem, or an `apply_changes` per-row error -- is now
    treated as a failure: `_audit_tick_error("cal-ext", ...)` + exit 1,
    matching `cmd_tick_offsite`'s own "any non-empty errors list fails the
    tick" contract exactly (the FIRST cut of this only did this for a
    TOTAL wipeout, leaving a calendar stuck on a permanently-403ing
    app-password, or a single flaky apply, completely invisible). This
    does NOT stop the live/clean parts of the same tick's data from being
    applied, nor does it hold a HEALTHY calendar's own sync-token progress
    hostage to an unrelated calendar being down (fix-round C2 is scoped
    to `apply_changes` errors specifically -- a calendar-level fetch/parse
    problem already excludes only ITS OWN rows via `_cal_ext_sync`'s
    per-row snapshot scoping, see that function's docstring). Three
    things are gated: `meta["extcal_sync_token:<url>"]` is written for
    NO calendar at all when there is ANY `apply_changes` error anywhere
    this tick (simpler/safer than attributing one bad row back to a
    specific calendar); `meta["extcal_last_full:<url>"]` (fix-round 3,
    C1 -- the rolling-horizon periodic full re-baseline, see `_cal_ext_
    sync`'s own docstring) is gated the SAME way, one calendar at a time
    (only a calendar that both ran a full mode this round AND stayed out
    of `apply_errors`' blanket gate advances its watermark); `meta
    ["extcal_last_ok"]` only advances on a tick with NO error anywhere
    (its name means "last FULL success").

    Final-review blocker 2 (Important): a real (non-`--dry-run`) `fam
    tick cal-ext --now ...` invocation from the actual CLI is REFUSED by
    `main()` (exit 2, before this function is even called -- see that
    guard's own comment there for why it lives in `main()` and not here).
    Reason: this function's own `now` is threaded into the LOCAL snapshot
    window (`_extcal_window`), but the ACTUAL CalDAV query window for a
    full/fallback read (`extcal._time_range`, used by `_calendar_query`)
    always uses the REAL system clock -- it takes no `now` parameter at
    all. A `--now` far from the real clock therefore scopes the local
    snapshot to one window while the remote read is scoped to a
    DIFFERENT one; every local `owner='iphone'` row outside the overlap
    looks to `plan_changes` like it disappeared from iCloud and gets
    cancelled with an irreversible tombstone -- a real, silent,
    unrecoverable data loss, not a preview quirk. `--dry-run` makes this
    safe again (nothing is written regardless of the mismatch), so it
    remains the only sanctioned way to use `--now` with this tick from
    the CLI. (This function itself still accepts `now`/`dry_run` on
    `args` unconditionally -- this project's own tests call it directly,
    bypassing `main()`, with a fixed `now` and fully-mocked transport,
    where the real mismatch this guard protects against cannot occur.)

    `meta["extcal_last_mode:<url>"]` is pure telemetry (not a safety-
    relevant value) but is only WRITTEN when it actually changes
    (fix-round 2, minor #6 -- this docstring previously said "every run,"
    which stopped being true the moment that minor landed: an
    unconditional write here would add another 96/day to the very WAL
    contention m6's own `RandomizedDelaySec` was trying to reduce). Its
    change is still one of the triggers for writing `audit cal.ext.sync`
    at all (fix-round finding I5: that audit is no longer unconditional --
    a perfectly healthy, zero-change, steady-state tick would otherwise
    write 96 near-identical rows a day forever; `meta["extcal_last_ok"]`
    already covers the heartbeat).
    """
    cfg = gate.load_config()
    if not cfg.get("extcal_enabled"):
        out = {"ok": True, "enabled": False}
        if getattr(args, "json", False):
            print(json.dumps(out, ensure_ascii=False))
        else:
            print("cal-ext disabled; skipping")
        return 0

    # Blocker 2's CLI-usage guard ("--now requires --dry-run") lives in
    # `main()`, not here -- see that guard's own comment for why: this
    # function is also called DIRECTLY (bypassing argparse/`main()`
    # entirely) by this project's own `cmd_tick_cal_ext`-level tests with
    # a fixed `now` for determinism, transport fully mocked, so the real
    # danger (a real, unmocked `extcal._time_range` desyncing from an
    # injected `now`) can never occur there -- only an actual `fam tick
    # cal-ext --now ...` CLI invocation goes through `main()`.
    dry_run = getattr(args, "dry_run", False)

    now = None
    if getattr(args, "now", None):
        try:
            now = datetime.fromisoformat(args.now)
        except ValueError as e:                   # fix-round finding m4
            _audit_tick_error("cal-ext", f"invalid --now {args.now!r}: {e}")
            print(f"cal-ext failed: invalid --now value: {e}")
            return 1
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)

    conn = famdb.connect()

    try:
        result = _cal_ext_sync(conn, cfg, now, dry_run)
    except Exception as e:                        # noqa: BLE001
        _audit_tick_error("cal-ext", e)
        print(f"cal-ext failed: {e}")
        return 1

    if dry_run:
        out = {"ok": True, "dry_run": True, "calendars": result["calendars"],
               "sync_errors": _redact_sync_errors(result["sync_errors"]),
               "changeset": _dry_run_summary(result["changeset"])}
        print(json.dumps(out, ensure_ascii=False))
        # Fix-round 2, minor #4: --dry-run ALWAYS returns 0 here,
        # deliberately, even when sync_errors is non-empty -- it is a
        # diagnostic preview only, nothing was written either way (no DB
        # row, no meta key, no iCloud write), so there is nothing an exit
        # code needs to protect; a human reads the printed sync_errors
        # instead of the tick being paged over it.
        return 0

    counts = result["counts"]
    export_counts = result.get("export_counts") or {}
    calendar_errors = [c for c in result["calendars"] if c["mode"] == "error"]
    apply_errors = counts.get("errors") or []
    # Task 7: export_own's own errors (`{event_id, action, error}` --
    # shaped like apply_errors' entries but keyed by event_id instead of
    # branch/id/external_uid, since export has no branch of its own and no
    # remote identity to report) fold into the SAME has_error/tick.error
    # path apply_errors already uses (requirement #10: an export failure
    # must reach the nightly problem_summary exactly like an import
    # failure does, not silently only via journald).
    export_errors = export_counts.get("errors") or []
    has_error = (bool(calendar_errors) or bool(result["sync_errors"])
                 or bool(apply_errors) or bool(export_errors))

    # extcal_last_mode: pure telemetry, but (fix-round 2, minor #6) only
    # WRITTEN when it actually changes -- this key lands in the SAME WAL
    # sqlite file fam-reminders touches every minute, and an unconditional
    # write here would add another 96/day to the very contention m6's
    # RandomizedDelaySec was trying to reduce. Also the "mode changed"
    # trigger for the audit below (fix-round finding I5).
    mode_changed = False
    for c in result["calendars"]:
        prev_mode = famdb.meta_get(conn, f"extcal_last_mode:{c['url']}")
        if prev_mode != c["mode"]:
            mode_changed = True
            famdb.meta_set(conn, f"extcal_last_mode:{c['url']}", c["mode"])

    nonzero_counts = (any(v for k, v in counts.items() if k != "errors")
                       or any(v for k, v in export_counts.items()
                              if k not in ("errors", "unchanged")))
    if has_error or nonzero_counts or mode_changed:
        audit_payload = dict(counts)
        audit_payload["calendars"] = result["calendars"]
        if result["sync_errors"]:
            # Blocker 3: this row is written on the REAL (non-dry-run)
            # path -- same redaction the dry-run print already applies,
            # not a second, laxer copy of the same list.
            audit_payload["sync_errors"] = _redact_sync_errors(result["sync_errors"])
        # Task 7: export counts ride along in the SAME audit row rather
        # than a second, separate `cal.ext.export` summary row per tick --
        # one sync, one row, matching how import/export are already one
        # combined tick (fam-cal-ext.timer), not two.
        audit_payload["export"] = export_counts
        audit.log(conn, "cal.ext.sync", audit_payload)

    # Fix-round finding C2: token persistence is gated ONLY on apply-time
    # errors (a calendar that itself failed to fetch/parse never got a
    # candidate token in the first place -- see _cal_ext_sync's own
    # tokens_to_persist construction) -- a healthy calendar's incremental
    # progress in a MIXED (partial-failure) tick must not be held hostage
    # by a completely unrelated calendar being down. This is deliberately
    # narrower than `has_error` below, which also trips on calendar-level
    # fetch/parse problems for EXIT CODE / alerting purposes only.
    if not apply_errors:
        for url, token in result["tokens"].items():
            if token:
                famdb.meta_set(conn, f"extcal_sync_token:{url}", token)
        # Fix-round 3, C1: same gate as sync-token persistence above, on
        # purpose -- a calendar that completed a full pass (initial_full/
        # fallback_full/periodic_full) THIS round but is not in `result
        # ["tokens"]` was batch-wide degraded (`_cal_ext_sync`'s own
        # `degraded_urls`), i.e. NOT a trustworthy full read, so its
        # `extcal_last_full` watermark must not advance either -- the
        # very next tick should try again, not wait out the full interval
        # on data nobody actually trusted.
        for url in result.get("full_mode_urls") or ():
            if url in result["tokens"]:
                famdb.meta_set(conn, f"extcal_last_full:{url}", now.isoformat())

    # extcal_last_ok: only a FULLY clean tick counts (matches the exit
    # code -- "last_ok" means "last full success", not "last time SOME
    # calendar happened to sync").
    if not has_error:
        famdb.meta_set(conn, "extcal_last_ok", now.isoformat())

    conn.commit()

    # Streak-alerting hardening (2026-08): a live-prod week showed every
    # one of ~4 tick.error escalations across ~670 ticks was a single,
    # self-healing blip (see the module-level comment on
    # `_EXTCAL_FAIL_STREAK_THRESHOLD_DEFAULT`). `has_error` above still
    # drives every DATA-SAFETY decision unchanged (the `cal.ext.sync`
    # audit write with its `sync_errors` list, the token-persistence /
    # `extcal_last_full` / `extcal_last_ok` gates) -- so a below-threshold
    # failure is still fully recorded, nothing is hidden, only the
    # ESCALATION to `tick.error` + exit 1 (the thing that lands in
    # Denis's nightly `maint.problem_summary`) is now gated on a
    # per-key consecutive-failure streak instead of firing on the very
    # first occurrence. Three independent streak classes, each edge-
    # triggered (`_extcal_record_failure`/`_extcal_record_success`,
    # meta-persisted so a restart doesn't reset them):
    #   - one per calendar URL (`calendar_had_error` from `_cal_ext_sync`,
    #     set for a fetch error, a bad/unreadable resource, a parse-count
    #     mismatch, or an expand() error attributed to that calendar) --
    #     a flaky calendar never masks or is masked by a healthy sibling;
    #   - `_EXTCAL_STREAK_DISCOVERY_KEY` for the "extcal_read_calendars
    #     configured but 0 matched" class -- there is no calendar to
    #     blame, so it gets its own counter, not folded into any real
    #     calendar's;
    #   - `_EXTCAL_STREAK_APPLY_KEY` for `apply_changes`/`export_own`
    #     per-row errors. These are folded into the SAME streak-gated
    #     path (not escalated immediately) deliberately: they already
    #     freeze every calendar's sync-token progress this tick (the
    #     blanket gate below, untouched by this change) and the design
    #     doc's own recorded live case is `database is locked` from the
    #     per-minute `fam-reminders` timer contending on the same WAL
    #     file -- exactly the transient, self-healing shape this whole
    #     feature exists to stop paging Denis over. A GENUINE apply
    #     outage still escalates by the Nth tick, same as a calendar
    #     outage, and stays covered independently by `health.
    #     extcal_staleness` if the sync-token freeze holds it back for
    #     longer than `extcal_stale_hours`.
    threshold = _extcal_fail_streak_threshold(cfg)
    escalate_messages = []

    for c in result["calendars"]:
        c_url = c["url"]
        if result.get("calendar_had_error", {}).get(c_url):
            if _extcal_record_failure(conn, c_url, threshold):
                msgs = result.get("calendar_error_msgs", {}).get(c_url) or [
                    f"{c.get('name') or c_url}: {c.get('reason') or 'cal-ext error'}"]
                escalate_messages.extend(_redact_sync_errors(msgs))
        else:
            _extcal_record_success(conn, c_url)

    if result.get("discovery_error"):
        if _extcal_record_failure(conn, _EXTCAL_STREAK_DISCOVERY_KEY, threshold):
            escalate_messages.extend(_redact_sync_errors(
                [e for e in result["sync_errors"]
                 if e.startswith("extcal_read_calendars is configured")]))
    else:
        _extcal_record_success(conn, _EXTCAL_STREAK_DISCOVERY_KEY)

    if apply_errors or export_errors:
        if _extcal_record_failure(conn, _EXTCAL_STREAK_APPLY_KEY, threshold):
            escalate_messages += [
                f"{e.get('branch')}.{e.get('action')} id={e.get('id')}: {e.get('error')}"
                for e in apply_errors]
            # Task 7: export_own's errors have no "branch" (there is only
            # one kind of row on this side, events) -- reported as
            # "export.<action> event_id=<id>: <error>" instead, same
            # overall shape.
            escalate_messages += [
                f"export.{e.get('action')} event_id={e.get('event_id')}: {e.get('error')}"
                for e in export_errors]
    else:
        _extcal_record_success(conn, _EXTCAL_STREAK_APPLY_KEY)

    conn.commit()

    if escalate_messages:
        # Blocker 3: redacted -- this string is what maint.problem_summary
        # copies verbatim into the nightly message to Denis, so an
        # unredacted href/RRULE here is a leak into HER message, not just
        # an audit-log detail.
        _audit_tick_error(
            "cal-ext", "; ".join(escalate_messages) or "cal-ext sync had errors")
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, **counts, "export": export_counts,
                               "calendars": result["calendars"]},
                              ensure_ascii=False))
        else:
            print(f"cal-ext failed: {len(calendar_errors)} calendar error(s), "
                  f"{len(result['sync_errors'])} sync issue(s), "
                  f"{len(apply_errors)} apply error(s), "
                  f"{len(export_errors)} export error(s)")
        return 1

    if getattr(args, "json", False):
        print(json.dumps({"ok": not has_error, **counts, "export": export_counts,
                           "calendars": result["calendars"]},
                          ensure_ascii=False))
    else:
        print(" ".join(f"{k}={v}" for k, v in counts.items() if k != "errors")
              + " " + " ".join(f"export_{k}={v}" for k, v in export_counts.items()
                                if k != "errors"))
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

def cmd_cal_ext_probe(args):
    """`fam cal-ext probe` -- Task 0 read-only recon over her iCloud
    calendars (Task 1 CalDAV transport underneath). Never writes to the
    DB or to iCloud: gate.load_config() is the only I/O besides the
    network reads inside extcal.probe(). Always exits 0 -- a probe
    failure (missing password, network error, ...) is reported inside
    the result's `errors` list, not as a CLI error, since this command
    IS the diagnostic for exactly those conditions.
    """
    cfg = gate.load_config()
    result = extcal.probe(cfg)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 0
    counts = result["counts"]
    print(f"cal-ext probe: {len(result['calendars'])} calendar(s), "
          f"{counts['total']} event(s) "
          f"(timed={counts['timed']}, all_day={counts['all_day']}, "
          f"rrule={counts['with_rrule']}, valarm={counts['with_valarm']})")
    for cal_info in result["calendars"]:
        print(f"  - {cal_info['name']}: ctag={cal_info['ctag']} "
              f"sync_token={'yes' if cal_info['supports_sync_token'] else 'no'}")
    if result["errors"]:
        print("errors:")
        for e in result["errors"]:
            print(f"  - {e}")
    return 0

def _fmt_plan(p):
    line = f"{p['id']}\t{p['title']}\t[{p['status']}]"
    if p.get("deadline"):
        line += f"\tdue={p['deadline']}"
    if p.get("place"):
        line += f"\t@{p['place']['name']}"
    if p.get("person"):
        line += f"\tfor:{p['person']['name']}"
    if p.get("prep_for_event_id"):
        marker = p["deadline"] if p.get("prep_when") == "date" else "departure"
        line += f"\tprep:{p['prep_for_event_id']}/{marker}"
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
    # Final review Finding 3 (now superseded, Phase 7b Task 2): the
    # amina-fam skill promises the event's road leave_at "recomputes
    # automatically" after an attach. That used to be bolted on here as a
    # best-effort post-call to cal.recompute_road (never routing through
    # the newly attached plan's place -- a deliberate backlog item at the
    # time). plans.attach() now does the recompute (waypoint-aware,
    # through every attached OPEN plan's place) AND the reminder-chain
    # regenerate itself, in the same transaction as the attach write --
    # cal.recompute_road is itself best-effort/never-raises and
    # self-audits (road.computed/road.hook_error), so nothing extra needs
    # wrapping here anymore.
    plans.attach(conn, args.id, args.event)
    conn.commit()
    p = plans.get(conn, args.id)
    if args.json:
        print(json.dumps(p, ensure_ascii=False))
    else:
        print(f"attached plan {args.id} -> event {args.event}")
    return 0

def _fmt_goal(g):
    line = f"{g['id']}\t{g['title']}\t[{g['status']}]\t{g['period_type']}:{g['period']}"
    if g.get("parent_goal_id"):
        line += f"\tparent:{g['parent_goal_id']}"
    if g.get("notes"):
        line += f"\tnotes={g['notes']}"
    return line


def _parent_hint(conn, g):
    """Task 3 spec: after `goal done`, if the goal has a parent AND the
    parent is an open quarter goal, surface a hint line prompting the
    caller (skill/human) to ask whether the quarter goal is fully done
    too. Returns the hint text, or None when there's no parent or the
    parent isn't an open quarter goal.
    """
    parent_id = g.get("parent_goal_id")
    if not parent_id:
        return None
    parent = goals.get(conn, parent_id)
    if parent is None:
        return None
    if parent["period_type"] != "quarter" or parent["status"] != "open":
        return None
    return (f"parent: #{parent['id']} «{parent['title']}» (quarter, open) "
            f"— спроси, закрыта ли квартальная целиком.")


def cmd_goal_add(args):
    conn = famdb.connect()
    goal_id = goals.add(conn, args.title, period=args.period,
                         parent=args.parent, notes=args.notes)
    conn.commit()
    g = goals.get(conn, goal_id)
    if args.json:
        print(json.dumps(g, ensure_ascii=False))
    else:
        print(f"added goal: {g['title']} (id={g['id']})")
    return 0


def cmd_goal_list(args):
    conn = famdb.connect()
    rows = goals.list_goals(conn, period=args.period, include_closed=args.all)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        for g in rows:
            print(_fmt_goal(g))
    return 0


def cmd_goal_show(args):
    conn = famdb.connect()
    g = goals.get(conn, args.id)
    if g is None:
        raise ValueError(f"unknown goal: {args.id}")
    if args.json:
        print(json.dumps(g, ensure_ascii=False))
    else:
        print(_fmt_goal(g))
    return 0


def cmd_goal_done(args):
    conn = famdb.connect()
    if not goals.mark(conn, args.id, "done"):
        raise ValueError(f"unknown goal: {args.id}")
    conn.commit()
    g = goals.get(conn, args.id)
    hint = _parent_hint(conn, g)
    if args.json:
        out = dict(g)
        out["parent_hint"] = hint
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(f"done goal: {g['title']} (id={g['id']})")
        if hint:
            print(hint)
    return 0


def cmd_goal_decline(args):
    conn = famdb.connect()
    if not goals.mark(conn, args.id, "declined"):
        raise ValueError(f"unknown goal: {args.id}")
    conn.commit()
    g = goals.get(conn, args.id)
    if args.json:
        print(json.dumps(g, ensure_ascii=False))
    else:
        print(f"declined goal: {g['title']} (id={g['id']})")
    return 0


def cmd_goal_reopen(args):
    conn = famdb.connect()
    if not goals.mark(conn, args.id, "open"):
        raise ValueError(f"unknown goal: {args.id}")
    conn.commit()
    g = goals.get(conn, args.id)
    if args.json:
        print(json.dumps(g, ensure_ascii=False))
    else:
        print(f"reopened goal: {g['title']} (id={g['id']})")
    return 0


def cmd_goal_take(args):
    conn = famdb.connect()
    if not goals.take(conn, args.id, args.period):
        raise ValueError(f"unknown goal: {args.id}")
    conn.commit()
    g = goals.get(conn, args.id)
    if args.json:
        print(json.dumps(g, ensure_ascii=False))
    else:
        print(f"took goal: {g['title']} (id={g['id']}) -> {g['period']}")
    return 0


def _fmt_plan_info(info):
    lines = [f"target: {info['target_month']} (state: {info['state'] or '—'})"]
    if info["quarter"]:
        lines.append(f"quarter: {info['quarter']} "
                      f"({len(info['quarter_goals_open'])} open)")
    lines.append(f"tails open: {len(info['tails_open'])}, "
                 f"declined: {len(info['tails_declined'])}")
    for g in info["quarter_goals_open"]:
        lines.append(f"  Q  #{g['id']} {g['title']}")
    for g in info["tails_open"]:
        lines.append(f"  open      #{g['id']} {g['title']}")
    for g in info["tails_declined"]:
        lines.append(f"  declined  #{g['id']} {g['title']}")
    return "\n".join(lines)


def cmd_goal_plan_info(args):
    conn = famdb.connect()
    cfg = gate.load_config()
    today = goals.today_almaty()
    info = goals.plan_info(conn, today, cfg["goal_ritual_window_days"])
    if args.json:
        print(json.dumps(info, ensure_ascii=False))
    else:
        print(_fmt_plan_info(info))
    return 0


def cmd_goal_plan_mark(args):
    conn = famdb.connect()
    cfg = gate.load_config()
    today = goals.today_almaty()
    month = args.month
    if month is None:
        month = goals.compute_target_month(conn, today, cfg["goal_ritual_window_days"])
    goals.plan_state_set(conn, month, args.status, today)
    conn.commit()
    state = goals.plan_state_get(conn, month)
    if args.json:
        print(json.dumps({"month": month, "status": state[0], "date": state[1]},
                          ensure_ascii=False))
    else:
        print(f"plan-mark: {month} -> {args.status}")
    return 0


def cmd_goal_plan_status(args):
    conn = famdb.connect()
    cfg = gate.load_config()
    today = goals.today_almaty()
    info = goals.plan_info(conn, today, cfg["goal_ritual_window_days"])
    print(_fmt_plan_info(info))
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
    acks.write(conn, cfg=gate.load_config())
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
    acks.write(conn, cfg=gate.load_config())
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"skipped: intake {args.id}")
    return 0

def _resolve_until_utc(raw):
    """CLI-layer parsing for `med defer --until`: HH:MM resolves against
    *today* in Asia/Almaty (reusing cal.ALMATY, same tz meds.defer itself
    uses internally); anything else is treated as ISO-8601 and normalized
    to UTC. meds.defer's own _parse_iso already tolerates both "...Z" and
    "...+00:00", so either form works here -- ValueError on garbage
    propagates to main()'s existing except ValueError -> exit 2, same
    contract as an unknown/non-pending intake_id.
    """
    m = re.fullmatch(r"(\d{2}):(\d{2})", raw)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if hh > 23 or mm > 59:
            raise ValueError(f"bad --until time '{raw}'")
        today_local = datetime.now(cal.ALMATY).replace(
            hour=hh, minute=mm, second=0, microsecond=0)
        return today_local.astimezone(timezone.utc).isoformat(timespec="seconds")
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"bad --until value '{raw}'")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")

def cmd_med_defer(args):
    """`fam med defer <intake_id> --until HH:MM|ISO` -- push a pending
    dose's series_next_utc out without acking it (Task 2, meds-defer).
    All validation (unknown/non-pending intake_id, past time, at/after
    local midnight) lives in meds.defer (T1); this layer only resolves
    --until to a UTC string first. Same ValueError -> exit 2 contract
    (via main()) as cmd_med_taken/cmd_med_skip.
    """
    until_utc = _resolve_until_utc(args.until)
    conn = famdb.connect()
    result = meds.defer(conn, args.id, until_utc)
    conn.commit()
    acks.write(conn, cfg=gate.load_config())
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"ок, напомню про приём в {result['until_local']}")
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

    sp = sub.add_parser("resolve")
    resolve_sub = sp.add_subparsers(dest="resolve_cmd", required=True)
    sprt = resolve_sub.add_parser("turn")
    sprt.set_defaults(func=cmd_resolve_turn)
    sprt.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="machine-readable output")

    sp = sub.add_parser("react-hook",
                        help="apply a WhatsApp reaction event read from stdin")
    sp.set_defaults(func=cmd_react_hook)

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
    spa.add_argument("--allow-overlap", dest="allow_overlap", action="store_true",
                      help="record this event even though the slot is already "
                           "taken (only after Amina confirmed she wants both)")
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
    spu.add_argument("--allow-overlap", dest="allow_overlap", action="store_true",
                      help="move this event onto a slot that is already taken "
                           "(only after Amina confirmed she wants both)")
    spu.add_argument("--prep-asked", dest="prep_asked", action="store_true",
                      help="mark this event as having already been asked "
                           "about prep (sets events.prep_asked=1)")
    spu.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spc = cal_sub.add_parser("cancel"); spc.set_defaults(func=cmd_cal_cancel)
    spc.add_argument("id", type=int)
    spc.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spser = cal_sub.add_parser("series")
    series_sub = spser.add_subparsers(dest="series_cmd", required=True)
    spsl = series_sub.add_parser("list"); spsl.set_defaults(func=cmd_cal_series_list)
    spsl.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="machine-readable output")
    spsc = series_sub.add_parser("cancel"); spsc.set_defaults(func=cmd_cal_series_cancel)
    spsc.add_argument("id", type=int)
    spsu = series_sub.add_parser("update"); spsu.set_defaults(func=cmd_cal_series_update)
    spsu.add_argument("id", type=int)
    spsu.add_argument("--add-person", dest="add_person", action="append", default=[],
                       help="participant ref to add to the series and its future untouched occurrences (repeatable)")
    spsu.add_argument("--rm-person", dest="rm_person", action="append", default=[],
                       help="participant ref to remove from the series and its future untouched occurrences (repeatable)")
    spsu.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="machine-readable output")
    spd = cal_sub.add_parser("done"); spd.set_defaults(func=cmd_cal_done)
    spd.add_argument("id", type=int)
    spd.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spado = cal_sub.add_parser("adopt"); spado.set_defaults(func=cmd_cal_adopt)
    spado.add_argument("id", type=int)
    spado.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="machine-readable output")

    spdis = cal_sub.add_parser("disown"); spdis.set_defaults(func=cmd_cal_disown)
    spdis.add_argument("id", type=int)
    spdis.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="machine-readable output")

    sps = cal_sub.add_parser("show"); sps.set_defaults(func=cmd_cal_show)
    sps.add_argument("id", type=int)
    sps.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                      help="machine-readable output")

    spdet = cal_sub.add_parser("detours"); spdet.set_defaults(func=cmd_cal_detours)
    spdet.add_argument("id", type=int)
    spdet.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
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

    spce = tick_sub.add_parser("cal-ext"); spce.set_defaults(func=cmd_tick_cal_ext)
    spce.add_argument("--now", help="ISO-8601 UTC override for \"now\" (testing/ops -- "
                       "REQUIRES --dry-run: extcal._time_range's ACTUAL iCloud query "
                       "window always uses the real clock, so on a real run a --now far "
                       "from it desyncs the local snapshot and permanently cancels rows "
                       "that never actually disappeared; refused with exit 2 without "
                       "--dry-run)")
    spce.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS,
                       help="compute and print the changeset without writing to DB or iCloud")
    spce.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
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

    sp = sub.add_parser("goal")
    goal_sub = sp.add_subparsers(dest="goal_cmd", required=True)

    spga = goal_sub.add_parser("add"); spga.set_defaults(func=cmd_goal_add)
    spga.add_argument("title")
    spga.add_argument("--period", help="YYYY-MM or YYYY-Qn; default: current month")
    spga.add_argument("--parent", type=int, help="parent quarter goal id")
    spga.add_argument("--notes", default="")
    spga.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="machine-readable output")

    spgl = goal_sub.add_parser("list"); spgl.set_defaults(func=cmd_goal_list)
    spgl.add_argument("--period", help="YYYY-MM or YYYY-Qn; "
                       "default: current month + current quarter")
    spgl.add_argument("--all", action="store_true",
                       help="include done/declined goals (default: open only)")
    spgl.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="machine-readable output")

    spgs = goal_sub.add_parser("show"); spgs.set_defaults(func=cmd_goal_show)
    spgs.add_argument("id", type=int)
    spgs.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="machine-readable output")

    spgd = goal_sub.add_parser("done"); spgd.set_defaults(func=cmd_goal_done)
    spgd.add_argument("id", type=int)
    spgd.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="machine-readable output")

    spgdc = goal_sub.add_parser("decline"); spgdc.set_defaults(func=cmd_goal_decline)
    spgdc.add_argument("id", type=int)
    spgdc.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="machine-readable output")

    spgr = goal_sub.add_parser("reopen"); spgr.set_defaults(func=cmd_goal_reopen)
    spgr.add_argument("id", type=int)
    spgr.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="machine-readable output")

    spgt = goal_sub.add_parser("take"); spgt.set_defaults(func=cmd_goal_take)
    spgt.add_argument("id", type=int)
    spgt.add_argument("--period", required=True, help="target month YYYY-MM")
    spgt.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="machine-readable output")

    spgpi = goal_sub.add_parser("plan-info"); spgpi.set_defaults(func=cmd_goal_plan_info)
    spgpi.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="machine-readable output")

    spgpm = goal_sub.add_parser("plan-mark"); spgpm.set_defaults(func=cmd_goal_plan_mark)
    spgpm.add_argument("status", choices=["done", "declined"])
    spgpm.add_argument("--month", help="target month YYYY-MM; default: computed target")
    spgpm.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="machine-readable output")

    spgps = goal_sub.add_parser("plan-status"); spgps.set_defaults(func=cmd_goal_plan_status)

    sp = sub.add_parser("road"); sp.set_defaults(func=cmd_road)
    sp.add_argument("event_id", type=int)
    sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                     help="machine-readable output")

    sp = sub.add_parser("whereami")
    wai_sub = sp.add_subparsers(dest="whereami_cmd", required=True)
    spw = wai_sub.add_parser("show"); spw.set_defaults(func=cmd_whereami_show)
    spw.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                     help="machine-readable output")
    spw = wai_sub.add_parser("set"); spw.set_defaults(func=cmd_whereami_set)
    spw.add_argument("--lat", type=float, required=True)
    spw.add_argument("--lon", type=float, required=True)
    spw.add_argument("--source", choices=("shared", "manual"), default="shared",
                     help="shared = прислала Амина, manual = поставили руками")
    spw.add_argument("--label", default="", help="как назвать точку в напоминании")
    spw.add_argument("--ttl-min", type=int, default=None,
                     help="сколько минут точка считается актуальной")
    spw.add_argument("--notify", action="store_true",
                     help="отправить Амине пересчитанное время (в диалоге не "
                          "нужно -- агент отвечает сам)")
    spw.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                     help="machine-readable output")
    spw = wai_sub.add_parser("clear"); spw.set_defaults(func=cmd_whereami_clear)
    spw.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                     help="machine-readable output")

    sp = sub.add_parser("cal-ext")
    cal_ext_sub = sp.add_subparsers(dest="cal_ext_cmd", required=True)

    spp = cal_ext_sub.add_parser("probe"); spp.set_defaults(func=cmd_cal_ext_probe)
    spp.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
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

    spd = med_sub.add_parser("defer"); spd.set_defaults(func=cmd_med_defer)
    spd.add_argument("id", type=int, help="med_intakes row id")
    spd.add_argument("--until", required=True,
                      help="HH:MM (Almaty, today) or ISO-8601 UTC")
    spd.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
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
    # Final-review blocker 2 (Important): a real CLI invocation of `fam
    # tick cal-ext --now <ISO>` WITHOUT `--dry-run` can permanently
    # tombstone imported rows (see cmd_tick_cal_ext's own docstring for
    # the full mechanism: extcal._time_range, the REAL iCloud query
    # window, ignores --now entirely, desyncing it from the local
    # snapshot window that DOES honor --now). Refused here, at the
    # actual CLI entry point, BEFORE any network/DB touch -- deliberately
    # NOT inside cmd_tick_cal_ext itself, which this project's own test
    # suite also calls directly (bypassing main()) with a fixed --now and
    # fully-mocked transport, a pattern the real bug can never reach.
    if (getattr(args, "func", None) is cmd_tick_cal_ext
            and getattr(args, "now", None)
            and not getattr(args, "dry_run", False)):
        print(
            "fam tick cal-ext: --now requires --dry-run -- a real run's "
            "actual iCloud query window always uses the real clock "
            "(extcal._time_range ignores --now), so combining a real run "
            "with --now can desync the local snapshot from what iCloud "
            "returns and permanently cancel rows that never actually "
            "disappeared. Use --dry-run to preview with --now, or omit "
            "--now for a real run.",
            file=sys.stderr,
        )
        return 2
    try:
        return args.func(args)
    except cal.UnknownRefError as e:
        print(str(e), file=sys.stderr); return 2
    except ValueError as e:
        print(str(e), file=sys.stderr); return 2
    except famdb.sqlite3.Error as e:
        print(f"db error: {e}", file=sys.stderr); return 2
