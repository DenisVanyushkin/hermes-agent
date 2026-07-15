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


class AuthExpired(Exception):
    """slid_token no longer valid -> operator must re-run `fam car auth-init`."""


def _now_ts():
    return int(datetime.now(timezone.utc).timestamp())


class StarlineClient:
    def __init__(self, token_path=None, _auth=None, _api_factory=None):
        self._path = token_path or TOKEN_PATH
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
        try:
            self.ensure_slnet()
            store = self.load_store()
            api = self._api_factory(store.get("user_id"), store.get("slnet_token"))
            return bool(api.set_car_state(store["device_id"], "engine", True))
        except Exception:
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
        if t is not None and (t < cfg["car_cabin_temp_low_c"] or t > cfg["car_cabin_temp_high_c"]):
            hooks.append(f"в салоне {t}°, можно завести на прогрев заранее")
    return hooks


def warmup_count_today(conn, now=None):
    """Count car.warmup audit rows with payload.result=='started' in
    today's Asia/Almaty day (reuses gate's day-bounds helper so the
    warmup daily limit resets at Almaty local midnight, same as the
    proactive-message budget)."""
    from fam.gate import _almaty_day_utc_bounds, _now
    frm, to = _almaty_day_utc_bounds(now or _now())
    rows = conn.execute(
        "SELECT payload FROM audit_log WHERE kind='car.warmup' "
        "AND ts_utc >= ? AND ts_utc < ?", (frm, to)).fetchall()
    return sum(1 for r in rows if json.loads(r["payload"]).get("result") == "started")


def _latest_engine_on(conn):
    r = conn.execute(
        "SELECT engine_on FROM car_metrics WHERE engine_on IS NOT NULL "
        "ORDER BY ts_utc DESC LIMIT 1").fetchone()
    return bool(r["engine_on"]) if r else False


def do_warmup(conn, client, cfg, requester, now=None):
    """Remote-start the engine, guarded in this mandatory order (spec
    §6): (1) daily limit, (2) engine already on, else (3) audit the
    attempt BEFORE calling the StarLine API, then audit the outcome and
    notify Denis. The audit-before-engine ordering is locked by a test
    (2 audit rows exist before start_engine() runs on the happy path) so
    a failed/ambiguous StarLine call never leaves us without a record
    that a warmup was attempted."""
    if warmup_count_today(conn, now=now) >= cfg["car_warmup_daily_limit"]:
        audit.log(conn, "car.warmup", {"requester": requester, "result": "limit"}, actor="agent")
        return {"ok": False, "reason": "limit"}
    if _latest_engine_on(conn):
        audit.log(conn, "car.warmup", {"requester": requester, "result": "already_on"}, actor="agent")
        return {"ok": False, "reason": "already_on"}
    audit.log(conn, "car.warmup", {"requester": requester, "result": "attempt"}, actor="agent")
    conn.commit()  # attempt row must be durable before the physical engine start (spec §6.4)
    ok = client.start_engine()
    audit.log(conn, "car.warmup",
              {"requester": requester, "result": "started" if ok else "failed"}, actor="agent")
    n = warmup_count_today(conn, now=now)
    gate.notify_denis(f"Прогрев машины: {requester}, "
                       f"{'ок' if ok else 'НЕ УДАЛОСЬ'} ({n}/{cfg['car_warmup_daily_limit']})")
    return {"ok": ok, "reason": "started" if ok else "failed"}
