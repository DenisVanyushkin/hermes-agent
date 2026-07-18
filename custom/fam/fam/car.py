"""StarLine integration: telemetry poll + engine warmup.

Never raises outward for network/API problems (like road.py): failures
become audit rows + silent degradation. Credentials never touch git,
audit, or exceptions. Token store holds only tokens (no password)."""
import json
import os
import stat
from datetime import datetime, timezone

from fam import audit, gate

TOKEN_PATH = "/home/denis/.hermes/private/amina/starline-token.json"
# The fam CLI also runs inside the docker sandbox, where the private dir
# is mounted under /root instead of /home/denis (same dual-path situation
# as db.resolve_db_path's HOST_DB/SANDBOX_DB).
SANDBOX_TOKEN_PATH = "/root/.hermes/private/amina/starline-token.json"


def _resolve_token_path():
    for p in (TOKEN_PATH, SANDBOX_TOKEN_PATH):
        if os.path.exists(p):
            return p
    return TOKEN_PATH

_SET_PARAM_URL = "https://developer.starline.ru/json/v1/device/{}/set_param"


def _http_post(url, json, headers, timeout=20):
    import requests
    return requests.post(url, json=json, headers=headers, timeout=timeout)


class AuthExpired(Exception):
    """slid_token no longer valid -> operator must re-run `fam car auth-init`."""


def _now_ts():
    return int(datetime.now(timezone.utc).timestamp())


class StarlineClient:
    def __init__(self, token_path=None, _auth=None, _api_factory=None):
        self._path = token_path or _resolve_token_path()
        self.last_error = None
        if _auth is None:
            from starline import StarlineAuth
            _auth = StarlineAuth()
        self._auth = _auth
        if _api_factory is None:
            from starline import StarlineApi
            _api_factory = StarlineApi
        self._api_factory = _api_factory

    def load_store(self):
        with open(self._path, encoding="utf-8") as f:
            return json.load(f)

    def save_store(self, store):
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False)
        os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)  # 600

    def ensure_slnet(self, now_ts=None, poll_interval_min=30):
        now_ts = now_ts if now_ts is not None else _now_ts()
        store = self.load_store()
        margin = poll_interval_min * 60
        if store.get("slnet_token") and now_ts + margin < store.get("slnet_expires", 0):
            return
        try:
            slnet, expires, uid = self._auth.get_user_id(store["slid_token"])
        except Exception as e:
            raise AuthExpired(str(e))
        store["slnet_token"] = slnet
        store["slnet_expires"] = expires
        store["user_id"] = uid
        self.save_store(store)

    def list_devices(self):
        """Discover devices reachable with the current credentials ->
        {device_id: alias}. Used by `fam car set-device` since the
        token store starts with device_id=None and nothing else ever
        populates it."""
        self.ensure_slnet()
        store = self.load_store()
        api = self._api_factory(store.get("user_id"), store.get("slnet_token"))
        api.update()
        return {dev_id: (getattr(dev, "_alias", None) or getattr(dev, "alias", None))
                for dev_id, dev in api.devices.items()}

    def set_device(self, device_id):
        store = self.load_store()
        store["device_id"] = str(device_id)
        self.save_store(store)

    def _device_data(self):
        store = self.load_store()
        api = self._api_factory(store.get("user_id"), store.get("slnet_token"))
        api.update()
        try:
            api.update_obd()  # fuel + mileage; tolerate failure (some units/None)
        except Exception:
            pass
        dev = api.devices.get(str(store.get("device_id")))
        if dev is None:
            return {}
        return {
            "battery": getattr(dev, "_battery", None),
            "ctemp": getattr(dev, "_ctemp", None),
            "etemp": getattr(dev, "_etemp", None),
            "status": getattr(dev, "_status", None),
            "gsm_lvl": getattr(dev, "_gsm_lvl", None),
            "car_state": getattr(dev, "_car_state", {}) or {},
            "position": getattr(dev, "_position", {}) or {},
            "fuel": getattr(dev, "_fuel", {}) or {},
            "mileage": getattr(dev, "_mileage", {}) or {},
        }

    def poll(self):
        try:
            self.ensure_slnet()
            data = self._device_data()
            return normalize(data)
        except Exception:
            return None

    def start_engine(self):
        self.last_error = None
        try:
            self.ensure_slnet()
            store = self.load_store()
            resp = _http_post(
                _SET_PARAM_URL.format(store["device_id"]),
                json={"type": "ign", "ign": 1},
                headers={"Cookie": "slnet=" + store["slnet_token"]},
            )
            body = resp.json()
            if int(body.get("code", 0)) == 200:
                return True
            # codedesc/body never carries credentials -- safe for audit
            self.last_error = f"api code={body.get('code')} desc={body.get('codestring') or body.get('desc')}"
            return False
        except Exception as e:  # noqa: BLE001 -- never raise; warmup guard treats False as failure
            self.last_error = f"{type(e).__name__}: {e}"
            return False

    def stop_engine(self):
        self.last_error = None
        try:
            self.ensure_slnet()
            store = self.load_store()
            resp = _http_post(
                _SET_PARAM_URL.format(store["device_id"]),
                json={"type": "ign", "ign": 0},
                headers={"Cookie": "slnet=" + store["slnet_token"]},
            )
            body = resp.json()
            if int(body.get("code", 0)) == 200:
                return True
            self.last_error = f"api code={body.get('code')} desc={body.get('codestring') or body.get('desc')}"
            return False
        except Exception as e:  # noqa: BLE001 -- never raise; do_stop treats False as failure
            self.last_error = f"{type(e).__name__}: {e}"
            return False


def _iso_now(now=None):
    return now or datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize(device_data, now=None):
    """Map a StarLine device dict (as returned by
    StarlineClient._device_data) to car_metrics columns. Every field is
    optional -- StarLine/OBD shape varies by car (spec §13 discovery);
    missing -> None, never raises. Full input kept in raw_json."""
    d = device_data or {}
    state = d.get("car_state") or {}
    pos = d.get("position") or {}
    fuel = d.get("fuel") or {}
    mileage = d.get("mileage") or {}
    status = d.get("status")

    fuel_pct = fuel_liters = None
    fuel_type = fuel.get("type")
    if fuel_type == "percents":
        fuel_pct = fuel.get("val")
    elif fuel_type in ("litres", "liters"):
        fuel_liters = fuel.get("val")

    return {
        "ts_utc": _iso_now(now),
        "fuel_pct": fuel_pct,
        "fuel_liters": fuel_liters,
        "odometer_km": mileage.get("val"),
        "engine_on": bool(state["run"]) if "run" in state else None,
        "ignition_on": bool(state["ign"]) if "ign" in state else None,
        "cabin_temp_c": d.get("ctemp"),
        "coolant_temp_c": d.get("etemp"),
        "battery_v": d.get("battery"),
        "gsm_online": (status == 1) if status is not None else None,
        "gps_lat": pos.get("x"),
        "gps_lon": pos.get("y"),
        "raw_json": json.dumps(d, ensure_ascii=False),
    }


def bootstrap(auth, app_id, app_secret, login, password,
              prompt_sms, prompt_captcha):
    """Full StarLine login chain -> token-store dict. Handles SMS
    (state==2) and captcha (state==0 + captchaSid). Password stays in
    memory only. Returns dict WITHOUT slnet fields when the caller will
    persist; ensure_slnet() fills slnet on first poll. Here we also fetch
    user_id/slnet once so the store is immediately usable."""
    code = auth.get_app_code(app_id, app_secret)
    app_token = auth.get_app_token(app_id, app_secret, code)
    sms_code = captcha_sid = captcha_code = None
    for _ in range(3):
        state, desc = auth.get_slid_user_token(
            app_token, login, password, sms_code=sms_code,
            captcha_sid=captcha_sid, captcha_code=captcha_code)
        if state == 1:
            slid = desc.get("user_token") or desc.get("slid_token")
            slnet, expires, uid = auth.get_user_id(slid)
            return {"app_id": app_id, "app_token": app_token,
                    "slid_token": slid, "user_id": uid,
                    "slnet_token": slnet, "slnet_expires": expires,
                    "device_id": None}
        if state == 2:
            sms_code = prompt_sms()
        elif state == 0 and "captchaSid" in desc:
            captcha_sid = desc["captchaSid"]
            _, captcha_code = prompt_captcha(desc.get("captchaImg", ""))
            captcha_sid = desc["captchaSid"]
        else:
            raise RuntimeError(f"StarLine login failed: state={state}")
    raise RuntimeError("StarLine login: too many challenge retries")


def _meta_get(conn, key, default=None):
    r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def _meta_set(conn, key, value):
    conn.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def record_metrics(conn, metrics, now=None):
    """Insert one car_metrics row and audit the tick. Returns the new
    row id. Negative decisions (StarLine unavailable) are audited by the
    caller (tick.car), not here -- this fn only runs when there IS data."""
    cols = ("ts_utc", "fuel_pct", "fuel_liters", "odometer_km", "engine_on",
            "ignition_on", "cabin_temp_c", "coolant_temp_c", "battery_v",
            "gsm_online", "gps_lat", "gps_lon", "raw_json")
    cur = conn.execute(
        f"INSERT INTO car_metrics({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
        tuple(metrics.get(c) for c in cols))
    audit.log(conn, "tick.car",
              {"fuel_pct": metrics.get("fuel_pct"), "engine_on": metrics.get("engine_on")},
              actor="tick")
    return cur.lastrowid


def update_fuel_flag(conn, fuel_pct, cfg):
    """Hysteresis: below car_fuel_low_pct sets the flag, above
    car_fuel_low_pct + car_fuel_hysteresis clears it; the band between
    the two thresholds holds whatever the flag already was (no
    flapping on borderline fuel readings). fuel_pct=None (no fresh
    reading) leaves the flag untouched. Returns the flag's current
    value after this call."""
    if fuel_pct is None:
        return fuel_is_low(conn)
    low = cfg["car_fuel_low_pct"]
    hyst = cfg["car_fuel_hysteresis"]
    flag = fuel_is_low(conn)
    if fuel_pct < low:
        flag = True
    elif fuel_pct > low + hyst:
        flag = False
    _meta_set(conn, "car_fuel_low", "1" if flag else "0")
    return flag


def fuel_is_low(conn):
    return _meta_get(conn, "car_fuel_low", "0") == "1"


def check_staleness(conn, cfg, now=None):
    """True when the newest car_metrics row is older than
    car_staleness_hours, or when there is no row at all (never polled
    successfully -> definitely stale)."""
    row = conn.execute("SELECT MAX(ts_utc) AS m FROM car_metrics").fetchone()
    if not row or not row["m"]:
        return True
    now_dt = datetime.now(timezone.utc) if now is None else now
    if isinstance(now_dt, str):
        now_dt = datetime.fromisoformat(now_dt)
    last = datetime.fromisoformat(row["m"])
    from datetime import timedelta
    return (now_dt - last) > timedelta(hours=cfg["car_staleness_hours"])


def maybe_alert_staleness(conn, cfg, now=None):
    """One-shot alert on the not-stale -> stale transition (meta
    car_stale_alerted), so a tick every 30 min doesn't spam Denis every
    run while data stays stale; clears the flag once fresh data is
    recorded again so the next staleness episode alerts anew."""
    stale = check_staleness(conn, cfg, now=now)
    alerted = _meta_get(conn, "car_stale_alerted", "0") == "1"
    if stale and not alerted:
        gate.notify_denis(f"StarLine: нет данных о машине > {cfg['car_staleness_hours']}ч")
        _meta_set(conn, "car_stale_alerted", "1")
    elif not stale and alerted:
        _meta_set(conn, "car_stale_alerted", "0")


def _latest_cabin_temp(conn):
    r = conn.execute(
        "SELECT cabin_temp_c FROM car_metrics "
        "WHERE cabin_temp_c IS NOT NULL ORDER BY ts_utc DESC LIMIT 1"
    ).fetchone()
    return r["cabin_temp_c"] if r else None


def departure_hooks(conn, event, cfg):
    """Departure-time piggyback hooks for a leave/prepare reminder --
    fuel-low nudge and a cabin-temp warmup suggestion. Non-car events
    never qualify (no route to a car). Never raises; callers (tick.py)
    still wrap this in try/except per the hot-path guard, but every
    branch here is a plain read."""
    if event.get("transport") != "car":
        return []
    hooks = []
    if fuel_is_low(conn):
        hooks.append("заправься — топлива мало")
    if cfg.get("car_cabin_suggest_enabled"):
        t = _latest_cabin_temp(conn)
        if t is not None:
            # Live-found bug (F3): a live cabin reading of 41.0C ("в
            # салоне 41.0°") produced a "прогрев" (warmup) suggestion --
            # wrong direction for a HOT cabin. Below the low threshold
            # the cabin is cold and warming up makes sense; above the
            # high threshold it's hot and the useful suggestion is to
            # cool it down instead.
            if t < cfg["car_cabin_temp_low_c"]:
                hooks.append(f"в салоне {t}°, можно завести на прогрев заранее")
            elif t > cfg["car_cabin_temp_high_c"]:
                hooks.append(f"в салоне {t}°, можно заранее завести машину, чтобы остудить")
    return hooks


def warmup_count_today(conn, now=None):
    """Count car.warmup audit rows with payload.result=='attempt' in
    today's Asia/Almaty day (reuses gate's day-bounds helper so the
    warmup daily limit resets at Almaty local midnight, same as the
    proactive-message budget).

    Counts 'attempt' rows minus 'failed' rows: the attempt row commits
    before the physical start (see do_warmup), so an in-flight attempt
    is conservatively counted -- two racers can not both pass the check
    between each other's attempt and started rows (finding 12). A
    'failed' outcome refunds its attempt (decision 2026-07-16): a failed
    call never reached the physical actuator's happy path, and eating
    the daily limit on e.g. a config error locked out real warmups."""
    from fam.gate import _almaty_day_utc_bounds, _now
    frm, to = _almaty_day_utc_bounds(now or _now())
    rows = conn.execute(
        "SELECT payload FROM audit_log WHERE kind='car.warmup' "
        "AND ts_utc >= ? AND ts_utc < ?", (frm, to)).fetchall()
    results = [json.loads(r["payload"]).get("result") for r in rows]
    return max(0, results.count("attempt") - results.count("failed"))


def _latest_engine_on(conn):
    # An auto-started S96v2 reports ign=true while run stays false
    # (phase-4 field notes), so engine_on alone under-reports a
    # warmed-up engine. Each flag is read at its own latest non-NULL
    # row: the two fields can go stale independently across polls
    # (normalize() sets each only when the device reported it), and a
    # newer row that reported only one flag must not mask the other
    # flag's last known value. Conservative direction for an actuator
    # guard: any flag's latest "on" refuses the start.
    row = conn.execute(
        "SELECT "
        "(SELECT engine_on FROM car_metrics WHERE engine_on IS NOT NULL "
        " ORDER BY ts_utc DESC LIMIT 1) AS run_flag, "
        "(SELECT ignition_on FROM car_metrics WHERE ignition_on IS NOT NULL "
        " ORDER BY ts_utc DESC LIMIT 1) AS ign_flag").fetchone()
    return bool(row["run_flag"]) or bool(row["ign_flag"])


def do_warmup(conn, client, cfg, requester, now=None):
    """Remote-start the engine, guarded in this mandatory order (spec
    §6): (1) daily limit, (2) engine already on, else (3) audit the
    attempt BEFORE calling the StarLine API, then audit the outcome and
    notify Denis. The audit-before-engine ordering is locked by a test
    (2 audit rows exist before start_engine() runs on the happy path) so
    a failed/ambiguous StarLine call never leaves us without a record
    that a warmup was attempted.

    BEGIN IMMEDIATE takes SQLite's single write lock up front: the
    count-check plus the attempt-audit below become atomic against any
    concurrent warmup caller (check-then-act race, finding 12). Every
    exit path commits (the limit/already_on early returns as well as the
    final started/failed outcome), so the connection is never left with
    an open transaction between calls -- required both for the write
    lock to actually release and so a second do_warmup call on the same
    conn can take its own BEGIN IMMEDIATE without "cannot start a
    transaction within a transaction"."""
    # BEGIN IMMEDIATE takes SQLite's single write lock up front: the
    # count-check plus the attempt-audit below become atomic against any
    # concurrent warmup caller (check-then-act race, finding 12).
    conn.execute("BEGIN IMMEDIATE")
    if warmup_count_today(conn, now=now) >= cfg["car_warmup_daily_limit"]:
        audit.log(conn, "car.warmup", {"requester": requester, "result": "limit"}, actor="agent")
        conn.commit()
        return {"ok": False, "reason": "limit"}
    if _latest_engine_on(conn):
        audit.log(conn, "car.warmup", {"requester": requester, "result": "already_on"}, actor="agent")
        conn.commit()
        return {"ok": False, "reason": "already_on"}
    audit.log(conn, "car.warmup", {"requester": requester, "result": "attempt"}, actor="agent")
    conn.commit()  # attempt row must be durable before the physical engine start (spec §6.4)
    ok = client.start_engine()
    outcome = {"requester": requester, "result": "started" if ok else "failed"}
    if not ok and getattr(client, "last_error", None):
        outcome["error"] = client.last_error
    audit.log(conn, "car.warmup", outcome, actor="agent")
    n = warmup_count_today(conn, now=now)
    conn.commit()  # close out the outcome row too -- no dangling transaction on any path
    gate.notify_denis(f"Прогрев машины: {requester}, "
                       f"{'ок' if ok else 'НЕ УДАЛОСЬ'} ({n}/{cfg['car_warmup_daily_limit']})")
    return {"ok": ok, "reason": "started" if ok else "failed"}


def do_stop(conn, client, cfg, requester, now=None):
    """Remote engine stop -- do_warmup's mirror minus the daily limit
    (stopping an engine is physically harmless, unlike retry-hammering a
    starter). Freshness first: the latest car_metrics row is up to a
    poll interval (30 min) old and routinely predates a remote start, so
    the already_off guard re-polls live telemetry before trusting the
    DB. The attempt row commits before the physical stop, same
    durability rule as warmup (spec §6.4)."""
    data = client.poll()
    if data:
        record_metrics(conn, data)
        conn.commit()
    if not _latest_engine_on(conn):
        audit.log(conn, "car.stop", {"requester": requester, "result": "already_off"}, actor="agent")
        conn.commit()
        return {"ok": False, "reason": "already_off"}
    audit.log(conn, "car.stop", {"requester": requester, "result": "attempt"}, actor="agent")
    conn.commit()
    ok = client.stop_engine()
    outcome = {"requester": requester, "result": "stopped" if ok else "failed"}
    if not ok and getattr(client, "last_error", None):
        outcome["error"] = client.last_error
    audit.log(conn, "car.stop", outcome, actor="agent")
    conn.commit()
    gate.notify_denis(f"Глушение машины: {requester}, {'ок' if ok else 'НЕ УДАЛОСЬ'}")
    return {"ok": ok, "reason": "stopped" if ok else "failed"}
