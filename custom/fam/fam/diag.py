"""Nightly diagnostics digest for fam (design 2026-08-01).

Collects a structured 24h picture -- error signatures, probes, activity
counters, timers, backups -- into ~/.hermes/diagnostics/fam-digest-latest.json
for the agent's `fam-nightly-report` cron job to render via LLM. Collects
only; never delivers. Delivery and the watermark contract live in maint.py.
"""
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from . import db as famdb
from . import gate
from . import health

MAX_EXAMPLES = 3
MAX_CONTEXT_VALUES = 10
SIGNATURE_MAX = 300
# health.py's probe `detail` is unowned free text from gateway.log -- a file
# this repo does not own, with no allow-list of its own. health.py's other
# callers (tick readiness alert, maybe_alert_readiness) legitimately need
# the full line, so the cap lives here instead of in health.py. Today's
# benign bridge marker line could grow an identifier or a message preview
# tomorrow with no change to fam at all; re-imposing the same allow-list
# discipline that governs audit_log payloads (ERROR_SPEC above) by length
# is the only guard that survives that kind of drift.
PROBE_DETAIL_MAX = 120

_HEX_RE = re.compile(r"[0-9a-fA-F]{8,}")
_NUM_RE = re.compile(r"\d+")
_WS_RE = re.compile(r"\s+")

# Per-kind ALLOW-list (design §7). A deny-list would leak the day someone
# adds a field: gate.error already carries `raw` and `final` -- the full
# message text to Amina -- and this digest is handed to an external LLM.
#   sig  -- fields that are part of the signature (a different value is a
#           different defect)
#   ctx  -- fields collected as context (a different value is the SAME
#           defect hitting another row; intake_id must not split findings)
#   text -- whether payload["error"] may be used for signature/examples
ERROR_SPEC = {
    "tick.error": {"sig": ("where", "exc_type"), "ctx": ("intake_id",), "text": True},
    "gate.error": {"sig": ("kind",), "ctx": ("attempt",), "text": False},
    "mail.error": {"sig": (), "ctx": ("event_id",), "text": True},
    "road.error": {"sig": (), "ctx": ("event_id", "via"), "text": False},
}
ERROR_KINDS = tuple(ERROR_SPEC)


def normalize_signature(text):
    text = _HEX_RE.sub("<hex>", text or "")
    text = _NUM_RE.sub("<n>", text)
    return _WS_RE.sub(" ", text).strip()[:SIGNATURE_MAX]


def collect_errors(conn, since):
    """Fold rows of the four kinds in ERROR_SPEC (tick.error, gate.error,
    mail.error, road.error) since `since` into deduplicated findings.

    This is NOT every `*.error` row fam writes: cal.ext.apply_error,
    cal.ext.export_error, rem.rule_error, road.hook_error and
    rem.cancel_error_cap are outside ERROR_SPEC and invisible to this
    digest -- widening the SQL to cover them is a design decision (each
    needs its own allow-list entry), not something this function does on
    its own.

    Returns findings sorted by descending count. A row whose payload is
    not a JSON object still produces a finding keyed on its kind alone --
    silently dropping a malformed error row would hide exactly the kind
    of breakage this digest exists to surface."""
    placeholders = ",".join("?" * len(ERROR_KINDS))
    rows = conn.execute(
        f"SELECT kind, payload FROM audit_log WHERE ts_utc >= ? "
        f"AND kind IN ({placeholders}) ORDER BY id",
        (since, *ERROR_KINDS)).fetchall()
    buckets = {}
    for row in rows:
        spec = ERROR_SPEC[row["kind"]]
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        parts = [row["kind"]]
        for field in spec["sig"]:
            if payload.get(field) is not None:
                parts.append(f"{field}={payload[field]}")
        text = normalize_signature(str(payload.get("error") or "")) if spec["text"] else ""
        if text:
            parts.append(text)
        signature = "|".join(parts)
        bucket = buckets.get(signature)
        if bucket is None:
            bucket = {"signature": signature, "kind": row["kind"], "count": 0,
                      "context": {}, "examples": []}
            for field in spec["sig"]:
                if payload.get(field) is not None:
                    # p_ prefix: payload field names share a namespace with
                    # the finding's own keys, and gate.error's payload has
                    # a "kind" of its own (reminder/med/digest) that would
                    # otherwise overwrite the audit event kind above.
                    bucket[f"p_{field}"] = payload[field]
            buckets[signature] = bucket
        bucket["count"] += 1
        for field in spec["ctx"]:
            value = payload.get(field)
            if value is None:
                continue
            values = bucket["context"].setdefault(field, [])
            if value in values:
                continue
            if len(values) < MAX_CONTEXT_VALUES:
                values.append(value)
            else:
                # Never truncate silently (mirrors the reporter's
                # findings_truncated/resolved_truncated): 40 distinct
                # broken intake_ids capped at 10 with no marker is
                # indistinguishable from exactly 10 -- count what the cap
                # drops instead of just dropping it.
                dropped = bucket.setdefault("_context_dropped", {})
                dropped[field] = dropped.get(field, 0) + 1
        if spec["text"]:
            example = str(payload.get("error") or "")[:200]
            if example and example not in bucket["examples"]:
                if len(bucket["examples"]) < MAX_EXAMPLES:
                    bucket["examples"].append(example)
                else:
                    bucket["_examples_dropped"] = bucket.get("_examples_dropped", 0) + 1
    findings = []
    for bucket in buckets.values():
        context_dropped = bucket.pop("_context_dropped", None)
        if context_dropped:
            bucket["context_truncated"] = context_dropped
        examples_dropped = bucket.pop("_examples_dropped", 0)
        if examples_dropped:
            bucket["examples_truncated"] = examples_dropped
        findings.append(bucket)
    return sorted(findings, key=lambda b: -b["count"])


STATE_KEY = "maint_known_issues"


def load_state(conn):
    """Known-issue state from the meta table. Corrupt state degrades to
    empty rather than raising: a bad value must cost one night of age
    tracking, not the whole nightly sweep."""
    raw = famdb.meta_get(conn, STATE_KEY)
    if not raw:
        return {}
    try:
        state = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(state, dict):
        return {}
    # Leaf shape matters as much as the top level: a hand-edited or
    # half-written value like {"sig": "oops"} would otherwise sail past
    # this check and blow up in diff_known_issues' prior.get(). Dropping
    # the bad entry costs one night of age tracking for that signature;
    # raising here would cost the whole nightly sweep.
    return {k: v for k, v in state.items() if isinstance(v, dict)}


def save_state(conn, state):
    famdb.meta_set(conn, STATE_KEY, json.dumps(state, ensure_ascii=False))


def diff_known_issues(state, findings, now):
    """Annotate findings with new/known/age_days against prior state.

    Returns (annotated, resolved, new_state). Signatures absent from this
    window drop out of new_state entirely -- otherwise state would grow
    without bound and every long-gone defect would keep re-reporting as
    'resolved' every night."""
    now_iso = now.isoformat(timespec="seconds")
    annotated = []
    new_state = {}
    for finding in findings:
        signature = finding["signature"]
        prior = state.get(signature) or {}
        first_seen = prior.get("first_seen")
        if first_seen:
            status = "known"
            try:
                age_days = (now - datetime.fromisoformat(first_seen)).days
            except (TypeError, ValueError):
                first_seen, age_days, status = now_iso, 0, "new"
        else:
            first_seen, age_days, status = now_iso, 0, "new"
        annotated.append({**finding, "status": status,
                          "first_seen": first_seen, "age_days": age_days})
        new_state[signature] = {"first_seen": first_seen, "last_seen": now_iso,
                                "count": finding["count"]}
    resolved = [{"signature": sig, "first_seen": meta.get("first_seen"),
                 "last_seen": meta.get("last_seen")}
                for sig, meta in state.items() if sig not in new_state]
    return annotated, resolved, new_state


SYSTEMCTL_TIMEOUT = 30


def reported_day_bounds(now):
    """UTC bounds + label of the last COMPLETE Asia/Almaty day.

    The collector runs at 22:30 UTC -- 03:30 Almaty, barely three hours
    into a day that has not happened yet. Neither window already at hand
    answers "как прошли сутки": the collector's own local day would cover
    only those three hours, and the errors window covers 48h (it overlaps
    by one night on purpose, so an undelivered digest is never lost). For
    medication counters that ambiguity is not cosmetic -- it is what made
    one daily dose read as "создано 3, принято 2". Activity is therefore
    reported against the day that just ended, and only that day.
    """
    yesterday = (now - timedelta(days=1)).astimezone(timezone.utc)
    day_from, day_to = gate._almaty_day_utc_bounds(yesterday.isoformat())
    return day_from, day_to, yesterday.astimezone(gate.ALMATY).date().isoformat()


def collect_activity(conn, cfg, day_from, day_to):
    """Counters only -- never message text. `sent_by_kind` uses the inner
    payload["kind"] (reminder/med/digest), which is a fixed vocabulary,
    not user content.

    Bounded by the reported calendar day (reported_day_bounds), NOT by the
    errors window: that one overlaps by a night on purpose, and counters
    summed over it are read as a day's worth by anyone the report reaches.
    """
    def _count(kind):
        return conn.execute(
            "SELECT COUNT(*) FROM audit_log "
            "WHERE kind=? AND ts_utc >= ? AND ts_utc < ?",
            (kind, day_from, day_to)).fetchone()[0]

    sent_by_kind = {}
    for row in conn.execute(
            "SELECT payload FROM audit_log WHERE kind='gate.sent' "
            "AND ts_utc >= ? AND ts_utc < ?",
            (day_from, day_to)):
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        kind = payload.get("kind") or "?"
        sent_by_kind[kind] = sent_by_kind.get(kind, 0) + 1
    # Doses come from med_intakes, not from audit_log. Two reasons the
    # audit route was wrong: kind="tick.med" is a reminder SEND (one dose
    # gets several when the med gate holds it, which is how one dose came
    # out as three), and meds_gen fires at 19:00 UTC == 00:00 Almaty, i.e.
    # exactly on the day boundary -- a second of timer drift would file
    # the dose under the wrong day. plan_ts_utc has neither problem: it
    # answers "what was due that day", which is the question asked.
    planned, taken = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(status = 'taken'), 0) FROM med_intakes "
        "WHERE plan_ts_utc >= ? AND plan_ts_utc < ?",
        (day_from, day_to)).fetchone()
    return {
        "sent_by_kind": sent_by_kind,
        "messages_sent": sum(sent_by_kind.values()),
        "meds_planned": planned,
        "meds_taken": taken,
        "reminder_chains_built": _count("rem.regenerate"),
        # day_from, not `now`: budget_spent_today resolves its own Almaty
        # day from the instant given, and `now` is 03:30 Almaty -- three
        # hours into a fresh budget, which is why a busy day was reported
        # as "0 из 8". day_from is midnight Almaty of the reported day, so
        # the bounds it derives are exactly [day_from, day_to). It also
        # satisfies that function's string contract (its _parse_utc calls
        # datetime.fromisoformat directly).
        "budget_spent": gate.budget_spent_today(conn, now_utc=day_from),
        "budget_limit": cfg.get("daily_budget", 8),
    }


def collect_calendar(conn, since):
    """Counter only. The design's audit rule for iPhone-controlled data
    is UID-and-counts-only: no event titles, calendar names or URLs ever
    leave the host through this digest."""
    total = 0
    for row in conn.execute(
            "SELECT payload FROM audit_log WHERE kind='cal.ext.sync' AND ts_utc >= ?",
            (since,)):
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            continue
        collisions = payload.get("collisions") if isinstance(payload, dict) else None
        if isinstance(collisions, int):
            total += collisions
    return {"collisions": total}


def _systemctl(runner, *args):
    result = runner(["systemctl", "--user", "--no-legend", "--no-pager", *args],
                    capture_output=True, text=True, timeout=SYSTEMCTL_TIMEOUT)
    if result.returncode != 0:
        # Not an exception path: systemctl answers a dead session bus with a
        # non-zero code and EMPTY stdout, which would otherwise parse into
        # "no failed units, no timers" -- a monitoring collector inventing
        # health it never observed. Raise so build_digest's per-section
        # guard records it in section_errors instead.
        raise RuntimeError(
            f"systemctl {' '.join(args)} exited {result.returncode}: "
            f"{(result.stderr or '').strip()[:200]}")
    return result


def collect_timers(runner=None):
    """Failed fam units + the roster of loaded fam timers.

    Uses `list-units` rather than `list-timers`: list-timers output is
    six positional columns whose widths shift with locale and schedule,
    while list-units puts the unit name first on every line."""
    runner = runner or subprocess.run
    failed = []
    result = _systemctl(runner, "list-units", "--failed", "--all", "fam-*.service")
    for line in (result.stdout or "").splitlines():
        parts = line.replace("●", " ").split()
        if not parts:
            continue
        sub = parts[3] if len(parts) > 3 else "failed"
        description = " ".join(parts[4:])
        # SUB is usually the literal "failed" again for a failed unit --
        # redundant with the ACTIVE column already implying failure. Fall
        # back to the unit's Description text so the digest carries
        # something a human can actually act on.
        detail = description if sub == "failed" and description else sub
        failed.append({"unit": parts[0], "detail": detail})
    ok = []
    result = _systemctl(runner, "list-units", "--type=timer", "--all", "fam-*.timer")
    for line in (result.stdout or "").splitlines():
        parts = line.replace("●", " ").split()
        if parts:
            ok.append(parts[0])
    return {"failed": failed, "ok": ok}


# The fam-offsite timer's own cadence (OnCalendar=Sun 23:30 UTC), so the
# digest can tell "waiting for the weekly run" from "the weekly run
# stopped happening". Overridable per-install via offsite_period_days.
DEFAULT_OFFSITE_PERIOD_DAYS = 7


def collect_backups(cfg, now, verify):
    """Newest dated backup of the assistant DB + its integrity verdict.
    `verify` is injected (maint.verify_backup) so diag never imports maint
    -- maint imports diag, and the reverse edge would be a cycle.

    backup_dir deliberately holds a second, unrelated backup target too:
    maint.backup_db also drops state-YYYYMMDD.db (the Hermes dialogue DB,
    cfg["state_db_path"]) in there every night. A broad "*-????????.db"
    glob would silently pick whichever name sorts last -- "state" beats
    "assistant" alphabetically -- and verify a DB that was never ours,
    which is exactly the bug this fix closes (state.db has no meta table,
    so verify_backup always failed it, producing a nightly false alarm).
    Narrowing the glob to the assistant DB's own stem is the fix."""
    stem = Path(famdb.resolve_db_path()).stem
    backup_dir = Path(cfg["backup_dir"])
    files = sorted(backup_dir.glob(f"{stem}-????????.db")) if backup_dir.is_dir() else []
    if not files:
        return {"last_path": None, "verify": "missing", "schema_version": None,
                "offsite_age_days": None, "offsite_overdue": None}
    newest = files[-1]
    ok, detail = verify(newest)
    result = {"last_path": newest.name,
              "verify": "ok" if ok else str(detail.get("integrity")),
              "schema_version": detail.get("schema_version"),
              "offsite_age_days": None,
              "offsite_overdue": False}
    if cfg.get("offsite_enabled"):
        offsite = Path(cfg["offsite_dir"])
        dumps = sorted(offsite.glob("*-????????.db.age")) if offsite.is_dir() else []
        if dumps:
            stamp = dumps[-1].name.split("-")[-1][:8]
            try:
                written = datetime.strptime(stamp, "%Y%m%d").replace(tzinfo=now.tzinfo)
                result["offsite_age_days"] = (now - written).days
            except ValueError:
                result["offsite_age_days"] = None
        # A raw age is not a verdict. The offsite timer is WEEKLY, so on the
        # Saturday before it fires the newest dump is legitimately six days
        # old -- and the reporter, told only "растущий offsite_age_days",
        # cried data-loss risk every weekend. Compare against the cadence
        # instead: age > period is late, and a missing dump (age None) is
        # the worst case, not an unknown one.
        age = result["offsite_age_days"]
        period = cfg.get("offsite_period_days") or DEFAULT_OFFSITE_PERIOD_DAYS
        result["offsite_overdue"] = age is None or age > period
    state_db_path = cfg.get("state_db_path")
    if state_db_path:
        # Presence/age only -- never verify() this one. Its schema isn't
        # ours (it's the Hermes dialogue DB), so an integrity/meta check
        # here would just be the same false alarm this fix removed, aimed
        # at a database this digest was never meant to police.
        state_stem = Path(state_db_path).stem
        state_files = (sorted(backup_dir.glob(f"{state_stem}-????????.db"))
                       if backup_dir.is_dir() else [])
        state_backup = {"last_path": None, "age_days": None}
        if state_files:
            newest_state = state_files[-1]
            state_backup["last_path"] = newest_state.name
            stamp = newest_state.name.split("-")[-1][:8]
            try:
                written = datetime.strptime(stamp, "%Y%m%d").replace(tzinfo=now.tzinfo)
                state_backup["age_days"] = (now - written).days
            except ValueError:
                pass
        result["state_backup"] = state_backup
    return result


DEFAULT_DIAGNOSTICS_DIR = "/home/denis/.hermes/diagnostics"
ROTATE_DAYS = 14
DIGEST_LATEST = "fam-digest-latest.json"


def build_digest(conn, cfg, since, now, delivery, state, verify, runner=None):
    """Assemble the digest. Returns (digest, new_state).

    Every section is isolated: one collector raising lands in
    section_errors and the rest of the digest still ships. If the error
    section itself fails, new_state is returned unchanged -- losing age
    tracking on a bad night would silently reset every 'known' finding
    back to 'new'."""
    sections, section_errors = {}, {}
    new_state = dict(state)

    def _section(name, fn):
        try:
            sections[name] = fn()
        except Exception as exc:                     # noqa: BLE001 -- isolate
            section_errors[name] = f"{type(exc).__name__}: {exc}"

    def _errors():
        nonlocal new_state
        annotated, resolved, new_state = diff_known_issues(
            state, collect_errors(conn, since), now)
        return {"findings": annotated, "resolved": resolved}

    def _probes():
        # Re-truncate here, not in health.py: health.all_probes' other
        # callers (tick readiness alert) need the full line, and this
        # digest leaves the host for an external LLM. name/status/
        # last_ok_ts are the allow-listed technical fields; detail is
        # free text from gateway.log and gets capped, not trusted.
        out = []
        for probe in health.all_probes(conn, cfg, now=now):
            out.append({
                "name": probe["name"],
                "status": probe["status"],
                "detail": (probe.get("detail") or "")[:PROBE_DETAIL_MAX],
                "last_ok_ts": probe.get("last_ok_ts"),
            })
        return out

    _section("errors", _errors)
    _section("probes", _probes)
    _section("calendar", lambda: collect_calendar(conn, since))
    day_from, day_to, day_label = reported_day_bounds(now)
    _section("activity", lambda: collect_activity(conn, cfg, day_from, day_to))
    _section("timers", lambda: collect_timers(runner=runner))
    _section("backups", lambda: collect_backups(cfg, now, verify))
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        # Two windows, deliberately: `since` is the overlapping error
        # window (delivery safety), `day` is the calendar day the
        # activity counters actually describe. The reporter must not
        # have to guess which one a number belongs to.
        "window": {"since": since, "day": day_label},
        "fam_schema_version": famdb.meta_get(conn, "schema_version"),
        "delivery": delivery,
        "sections": sections,
        "section_errors": section_errors,
    }, new_state


def write_digest(digest, dest_dir, now):
    """Publish the digest atomically and rotate dated copies.

    os.replace, not a plain write: the agent's cron job may read the file
    at any moment, and a half-written digest would surface to Denis as a
    bogus DIGEST MISSING."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_dir.chmod(0o700)                 # same PII posture as the DB backups
    body = json.dumps(digest, ensure_ascii=False, indent=1)
    latest = dest_dir / DIGEST_LATEST
    tmp = dest_dir / (DIGEST_LATEST + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, latest)
    dated = dest_dir / f"fam-digest-{now.strftime('%Y%m%d')}.json"
    dated.write_text(body, encoding="utf-8")
    dated.chmod(0o600)
    for old in sorted(dest_dir.glob("fam-digest-????????.json"))[:-ROTATE_DAYS]:
        old.unlink()
    return latest


def _aware(value):
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def report_delivery_status(jobs_path, job_name, previous_digest_at):
    """Did the agent's reporter job deliver the previous digest?

    Returns (ok, detail). Every uncertain case answers False: the cost of
    a false negative is one redundant raw message, the cost of a false
    positive is a silently burnt watermark and a lost day of problems."""
    if not previous_digest_at:
        return False, "first run: no previous digest"
    try:
        data = json.loads(Path(jobs_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        # TypeError too: a config with "report_jobs_path": null reaches us as
        # None (cfg.get only substitutes its default for an ABSENT key), and
        # Path(None) raises. Returning False costs one redundant raw message;
        # raising would take the whole nightly tick down with it.
        return False, f"jobs.json unreadable: {type(exc).__name__}"
    jobs = data.get("jobs", data) if isinstance(data, dict) else data
    if isinstance(jobs, dict):
        jobs = list(jobs.values())
    if not isinstance(jobs, list):
        return False, "jobs.json: unexpected shape"
    job = next((j for j in jobs
                if isinstance(j, dict) and j.get("name") == job_name), None)
    if job is None:
        return False, f"job {job_name!r} not found"
    if job.get("last_status") != "ok":
        return False, f"last_status={job.get('last_status')!r}"
    if job.get("last_delivery_error"):
        return False, f"delivery error: {str(job['last_delivery_error'])[:120]}"
    try:
        ran, previous = _aware(job.get("last_run_at")), _aware(previous_digest_at)
    except (TypeError, ValueError):
        return False, f"unparseable last_run_at={job.get('last_run_at')!r}"
    if ran <= previous:
        return False, f"last_run_at={job.get('last_run_at')} predates digest"
    return True, f"delivered at {job.get('last_run_at')}"
