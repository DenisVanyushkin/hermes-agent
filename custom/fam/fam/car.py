"""StarLine integration: telemetry poll + engine warmup.

Never raises outward for network/API problems (like road.py): failures
become audit rows + silent degradation. Credentials never touch git,
audit, or exceptions. Token store holds only tokens (no password)."""
import json
import os
import stat
from datetime import datetime, timezone

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
