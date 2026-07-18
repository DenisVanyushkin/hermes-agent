"""Reminder rules engine: leave_at, applicable rules, chain generation.

Domain functions never commit — callers (tests, CLI, cal.py hooks) own the
transaction, mirroring cal.py/people.py/places.py's pattern.

Reminders anchor on either the event's start_utc or its leave_at time
(start_utc minus travel minutes). fire_at_utc is stored as a UTC ISO
string with second precision -- the same canonical format as
events.start_utc -- so lexical comparison against "now" works directly for
the tick (see idx_reminders_fire).
"""
import json
from datetime import datetime, timedelta, timezone

from fam import audit

KIND_PREPARE = "prepare"
KIND_LEAVE = "leave"


def build_stages(lead_min):
    """Эскалационная цепочка для лида `lead_min` минут до выхода (leave_at).

    Формула (решение Дениса, 2026-07-12): offsets {D, D-5, D-15} ∪
    ({30, 15} если < D) ∪ {0}. При коллизии offset'ов countdown-лейбл
    («выходить через …») побеждает prepare-лейбл: за 15 минут до выхода
    важнее сказать «выходить через 15 минут», чем «не отвлекаемся».
    kind: prepare-стадии гасятся ack'ом «собираемся», leave-стадии — только
    «выходим» (см. ack_chain scope, Task 4).
    """
    d = lead_min
    stages = {0: ("пора выходить", KIND_LEAVE)}
    if 0 < 30 < d:
        stages[30] = ("выходить через полчаса", KIND_LEAVE)
    if 0 < 15 < d:
        stages[15] = ("выходить через 15 минут", KIND_LEAVE)
    stages.setdefault(d, ("пора собираться", KIND_PREPARE))
    if d - 5 > 0:
        stages.setdefault(d - 5, ("уже начали собираться?", KIND_PREPARE))
    if d - 15 > 0:
        stages.setdefault(d - 15, ("не отвлекаемся, собираемся", KIND_PREPARE))
    return [
        {"anchor": "leave_at", "offset_min": -off, "label": label, "kind": kind}
        for off, (label, kind) in sorted(stages.items(), reverse=True)
    ]


DEFAULT_STAGES = build_stages(30)
TAYA_STAGES = build_stages(60)
AMINA_STAGES = []  # inert reserve: no stages until an admin populates it


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_utc(value):
    """Parse an ISO-8601 string into an aware UTC datetime. A naive string
    (no tzinfo) is treated as already UTC -- fire_at_utc/start_utc/leave_at
    strings in this module are always produced with an explicit UTC
    offset, but this keeps the parser tolerant of hand-written test/admin
    values that omit it.
    """
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def leave_at(conn, event):
    """UTC ISO string: event["start_utc"] minus travel minutes.

    Priority (3a): event["travel_min_road"] (computed, with traffic) beats
    event["travel_min"] (manual override) beats the event's place
    travel_min beats 0. Product decision (Denis 2026-07-12): a computed
    road estimate with live traffic is more trustworthy than the user's
    off-hand "ехать 40 минут" guess, so it takes precedence; the manual
    figure remains the fallback when no road computation exists yet. With
    travel 0 (e.g. no place), leave_at == start_utc.
    """
    travel = event.get("travel_min_road")
    if travel is None:
        travel = event.get("travel_min")
    if travel is None:
        place = event.get("place")
        travel = place["travel_min"] if place else 0
    start_dt = _parse_utc(event["start_utc"])
    return (start_dt - timedelta(minutes=travel)).isoformat(timespec="seconds")


def applicable_rules(conn, event):
    """Enabled reminder_rules whose scope is 'default' or 'slug:<slug>'
    for any participant of the event. Each rule's stages field is parsed
    from JSON into a list of dicts.

    A rule whose stages column is not valid JSON (e.g. hand-edited/
    corrupted by an admin) is skipped rather than raised -- a bad rule
    must never block event creation, since this runs inside cal.add's/
    cal.update's regenerate hook. It is audited as rem.rule_error so an
    admin can find and fix it via `fam rem rules`.

    Precedence (2c): a specific (slug-scoped) rule with non-empty stages
    REPLACES the default rule rather than stacking with it. A slug rule
    with empty stages (an inert reserve, e.g. slug:amina) does not claim
    precedence -- the default rule still applies alongside it.
    """
    slugs = {p["slug"] for p in event.get("participants", []) if p.get("slug")}
    scopes = {"default"} | {f"slug:{s}" for s in slugs}
    placeholders = ",".join("?" for _ in scopes)
    rows = conn.execute(
        f"SELECT * FROM reminder_rules WHERE enabled=1 AND scope IN "
        f"({placeholders}) ORDER BY id",
        tuple(scopes),
    ).fetchall()
    rules = []
    for row in rows:
        d = dict(row)
        try:
            d["stages"] = json.loads(d["stages"])
        except (json.JSONDecodeError, TypeError):
            audit.log(conn, "rem.rule_error", {"rule_id": d["id"]})
            continue
        rules.append(d)

    # Прецедентность (2c): slug-правило с непустыми stages ЗАМЕЩАЕТ
    # default, а не дополняет его -- иначе Тая-событие получило бы
    # default-цепочку D=30 плюс Тая-цепочку D=60 (дубли каждые 5-15
    # минут). Пустое slug-правило (slug:amina -- инертный резерв)
    # прецедентность не захватывает: событие с одной Аминой получает
    # default, а не ноль напоминаний.
    specific = [r for r in rules
                if r["scope"] != "default" and r["stages"]]
    if specific:
        return [r for r in rules if r["scope"] != "default"]
    return rules


def regenerate(conn, event_id, now_utc=None):
    """Delete this event's pending reminders and regenerate them from the
    applicable rules. sent/acked/cancelled rows are untouched. If the
    event is missing or not active, this only performs the delete (0
    created). Only future stages (fire_at > now) are created. Returns the
    number of reminders created. Audits rem.regenerate.
    """
    from fam import cal  # deferred: avoid import-time cycle with cal.py

    now = now_utc or _now()
    now_dt = _parse_utc(now)

    conn.execute(
        "DELETE FROM reminders WHERE event_id=? AND status='pending'",
        (event_id,),
    )

    event = cal.get(conn, event_id)
    created = 0
    if event is not None and event["status"] == "active":
        anchors = {
            "start": _parse_utc(event["start_utc"]),
            "leave_at": _parse_utc(leave_at(conn, event)),
        }
        created_at = _now()
        # Task 4 (phase 7): an explicit event.prep_min overrides the rule
        # engine entirely -- event > slug > default precedence. It is
        # synthesized as a single rule_id=None rule (reminders.rule_id is
        # nullable, see db.py) rather than picking/mutating a DB row,
        # since this lead applies to exactly this one event.
        if event.get("prep_min"):
            rules = [{"id": None, "stages": build_stages(int(event["prep_min"]))}]
        else:
            rules = applicable_rules(conn, event)
        for rule in rules:
            for stage_idx, stage in enumerate(rule["stages"]):
                fire_dt = anchors[stage["anchor"]] + timedelta(
                    minutes=stage["offset_min"])
                if fire_dt <= now_dt:
                    continue
                conn.execute(
                    "INSERT INTO reminders(event_id, rule_id, stage_idx, "
                    "label, anchor, kind, fire_at_utc, status, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (event_id, rule["id"], stage_idx, stage["label"],
                     stage["anchor"], stage.get("kind", KIND_LEAVE),
                     fire_dt.isoformat(timespec="seconds"), "pending",
                     created_at),
                )
                created += 1

    audit.log(conn, "rem.regenerate", {"event_id": event_id, "created": created})
    return created


def _transition_pending(conn, event_id, new_status, audit_kind):
    cur = conn.execute(
        "UPDATE reminders SET status=? WHERE event_id=? AND status='pending'",
        (new_status, event_id),
    )
    count = cur.rowcount
    audit.log(conn, audit_kind, {"event_id": event_id, "count": count})
    return count


def ack_chain(conn, event_id, scope="all"):
    """Mark this event's pending reminders acked. Returns the count.

    scope="prepare" гасит только стадии сборов (kind='prepare') --
    «уже собираемся» не должно отменять «пора выходить»; scope="all"
    (default, обратная совместимость) гасит всю цепочку («уже выходим»).
    """
    if scope not in ("all", "prepare"):
        raise ValueError(f"unknown ack scope: {scope}")
    sql = "UPDATE reminders SET status='acked' WHERE event_id=? AND status='pending'"
    params = [event_id]
    if scope == "prepare":
        sql += " AND kind='prepare'"
    cur = conn.execute(sql, params)
    audit.log(conn, "rem.ack",
              {"event_id": event_id, "count": cur.rowcount, "scope": scope})
    return cur.rowcount


def cancel_chain(conn, event_id):
    """Mark this event's pending reminders cancelled. Returns the count."""
    return _transition_pending(conn, event_id, "cancelled", "rem.cancel_chain")


# ---- CLI-facing queries ----

def list_reminders(conn, event_id=None, due=False, now_utc=None):
    """List reminders ordered by fire_at_utc ascending. Optionally
    filtered to one event_id, and/or to "due" reminders (status='pending'
    AND fire_at_utc <= now) -- the exact selection the reminders tick
    (Task 6) will use.
    """
    sql = "SELECT * FROM reminders WHERE 1=1"
    params = []
    if event_id is not None:
        sql += " AND event_id=?"
        params.append(event_id)
    if due:
        sql += " AND status='pending' AND fire_at_utc <= ?"
        params.append(now_utc or _now())
    sql += " ORDER BY fire_at_utc"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def list_rules(conn):
    """List all reminder_rules (enabled or not), for admin visibility.

    Unlike applicable_rules(), this does NOT skip a rule with malformed
    stages JSON -- it surfaces it (stages left as the raw string, plus
    stages_error=True) so an admin can see and fix it directly instead of
    it silently vanishing from view.
    """
    rows = conn.execute("SELECT * FROM reminder_rules ORDER BY id").fetchall()
    out = []
    for row in rows:
        d = dict(row)
        try:
            d["stages"] = json.loads(d["stages"])
        except (json.JSONDecodeError, TypeError):
            d["stages_error"] = True
        out.append(d)
    return out


def active_chains(conn, now_utc=None):
    """Distinct events that currently have >=1 pending reminder -- i.e. a
    reminder chain still in progress.

    This is what a conversational reaction ("уже выходим/едем/собираемся")
    resolves against when the fired reminder that triggered it is NOT in
    the agent's session context -- reminders are delivered out-of-band by
    the tick (a separate `hermes send`), never by the conversation itself.
    See the amina-fam skill's Reminder Reactions section: it runs `fam rem
    active` first, then acks the one event it names (or asks, if several).

    now_utc is accepted for signature symmetry with the other CLI-facing
    queries in this module (list_reminders/regenerate); it is not used to
    filter here since status='pending' already captures exactly "not yet
    acted on", independent of whether fire_at_utc itself is past or
    future.

    For each event: event_id, title, start_local (Asia/Almaty),
    next_fire_local (the soonest still-pending fire, Asia/Almaty),
    pending_count, sent_count. Ordered by next fire ascending (soonest
    chain first) -- events with 0 pending reminders (fully acked/
    cancelled, or never had any) never appear.
    """
    from fam import cal  # deferred: avoid import-time cycle with cal.py

    rows = conn.execute(
        "SELECT e.id AS event_id, e.title AS title, e.start_utc AS start_utc,"
        "  SUM(CASE WHEN r.status='pending' THEN 1 ELSE 0 END) AS pending_count,"
        "  SUM(CASE WHEN r.status='sent' THEN 1 ELSE 0 END) AS sent_count,"
        "  MIN(CASE WHEN r.status='pending' THEN r.fire_at_utc END) AS next_fire_utc"
        " FROM events e JOIN reminders r ON r.event_id = e.id"
        " GROUP BY e.id"
        " HAVING SUM(CASE WHEN r.status='pending' THEN 1 ELSE 0 END) >= 1"
        " ORDER BY next_fire_utc"
    ).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        out.append({
            "event_id": d["event_id"],
            "title": d["title"],
            "start_local": cal._to_local_iso(d["start_utc"]),
            "next_fire_local": cal._to_local_iso(d["next_fire_utc"]),
            "pending_count": d["pending_count"],
            "sent_count": d["sent_count"],
        })
    return out


def _seed_rule(conn, scope, stages):
    existing = conn.execute(
        "SELECT 1 FROM reminder_rules WHERE scope=?", (scope,)
    ).fetchone()
    if existing is not None:
        return
    conn.execute(
        "INSERT INTO reminder_rules(scope, stages, enabled, created_at) "
        "VALUES (?,?,?,?)",
        (scope, json.dumps(stages, ensure_ascii=False), 1, _now()),
    )
    audit.log(conn, "rem.seed", {"scope": scope, "stages": stages})


def seed_default_rules(conn):
    """Idempotently insert the default, slug:taya, and slug:amina reminder
    rules. A scope that already has a rule is left untouched (no
    re-insert, no audit entry) -- safe to call on every startup (fam init
    calls this).

    slug:amina ships with empty stages -- an inert reserve for a future
    Amina-specific reminder cadence; applicable_rules() picks it up like
    any other rule (0 stages -> 0 reminders) once an admin populates it.
    """
    _seed_rule(conn, "default", DEFAULT_STAGES)
    _seed_rule(conn, "slug:taya", TAYA_STAGES)
    _seed_rule(conn, "slug:amina", AMINA_STAGES)


def migrate_rules_2c(conn, now_utc=None):
    """Одноразовый (meta-гвард rules_version='2c') пересев default- и
    Тая-правил на эскалационные цепочки + перегенерация напоминаний всех
    активных будущих событий. Старые stages пишутся в audit
    (rem.migrate_2c) перед перезаписью -- админ-правки не теряются молча.
    """
    row = conn.execute(
        "SELECT value FROM meta WHERE key='rules_version'").fetchone()
    if row is not None and row["value"] == "2c":
        return 0
    now = now_utc or _now()
    new_stages = {"default": DEFAULT_STAGES, "slug:taya": TAYA_STAGES}
    for scope, stages in new_stages.items():
        old = conn.execute(
            "SELECT stages FROM reminder_rules WHERE scope=?", (scope,)
        ).fetchone()
        audit.log(conn, "rem.migrate_2c",
                  {"scope": scope, "old": old["stages"] if old else None})
        conn.execute(
            "UPDATE reminder_rules SET stages=? WHERE scope=?",
            (json.dumps(stages, ensure_ascii=False), scope))
    regenerated = 0
    rows = conn.execute(
        "SELECT id FROM events WHERE status='active' AND start_utc > ?",
        (now,)).fetchall()
    for r in rows:
        regenerate(conn, r["id"], now_utc=now)
        regenerated += 1
    conn.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES('rules_version','2c')")
    return regenerated
