"""Append-only audit trail. Every mutation logs in the caller's transaction."""
import json
from datetime import datetime, timezone

def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def log(conn, kind, payload, actor="agent"):
    cur = conn.execute(
        "INSERT INTO audit_log(ts_utc, kind, actor, payload) VALUES(?,?,?,?)",
        (_now(), kind, actor, json.dumps(payload, ensure_ascii=False)))
    return cur.lastrowid

def query(conn, since_utc, kind_prefix, grep, limit=50):
    if limit is None or limit <= 0:
        raise ValueError("limit must be positive")
    sql = "SELECT * FROM audit_log WHERE 1=1"
    params = []
    if since_utc:
        sql += " AND ts_utc >= ?"; params.append(since_utc)
    if kind_prefix:
        sql += " AND kind LIKE ?"; params.append(kind_prefix + "%")
    if grep:
        sql += " AND payload LIKE ?"; params.append(f"%{grep}%")
    sql += " ORDER BY id DESC LIMIT ?"; params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) | {"payload": json.loads(r["payload"])} for r in rows]
