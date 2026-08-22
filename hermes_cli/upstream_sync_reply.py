"""Detect operator upstream-sync merge decisions in a Slack thread reply.

The upstream-sync conflict report asks the operator to reply with one decision
per feature, e.g. ``1: merge both, 2: merge both, 3: keep local``. The gateway
uses these helpers to recognize such a reply and check that a decision is
actually pending, so it can route the reply to the upstream-sync skill instead
of the generic pipeline orchestrator.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import re
from pathlib import Path
from typing import Optional

_VALID_OPTIONS = ("merge both", "keep local", "take upstream")


@contextlib.contextmanager
def _state_lock(state: Path):
    """Serialize every state transition and the shared finalize request slot."""
    state.mkdir(parents=True, exist_ok=True)
    with (state / "state.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


# Ack ids are content-addressed short ids, never path:symbol tokens. The
# delimiter is intentionally forbidden: it was the source of false positives
# for times, URLs, and arbitrary ``3:1`` prose.
_ACK_RE = re.compile(r"^ack\s+([A-Za-z0-9][A-Za-z0-9._/-]{1,63})$", re.IGNORECASE)


def parse_upstream_sync_ack_reply(text: Optional[str]) -> Optional[str]:
    if not isinstance(text, str):
        return None
    match = _ACK_RE.fullmatch(text.strip())
    if not match:
        return None
    finding_id = match.group(1)
    if ":" in finding_id or "://" in finding_id:
        return None
    return finding_id


def _load_invariant_pending(state: Path) -> dict:
    try:
        data = json.loads((state / "invariants-pending.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def has_pending_upstream_invariant_ack(state_dir: Path | str) -> bool:
    """Fail closed: unreadable or malformed state is not an armed intercept."""
    data = _load_invariant_pending(Path(state_dir))
    return data.get("schema") == "upstream-sync-invariants-pending/v1" and data.get("status") == "awaiting_ack"


def record_invariant_ack(state_dir: Path | str, finding_id: str, source: dict) -> dict:
    """Record one fingerprint-bound receipt and enqueue exactly one host request."""
    state = Path(state_dir)
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    with _state_lock(state):
        data = _load_invariant_pending(state)
        if data.get("schema") != "upstream-sync-invariants-pending/v1" or data.get("status") != "awaiting_ack":
            return {"requested": False, "reason": "the invariant gate is not armed"}
        expected_origin = data.get("origin") or {}
        for key in ("platform", "chat_id", "thread_id", "user_id"):
            expected = expected_origin.get(key)
            actual = source.get(key)
            if expected and actual and str(expected) != str(actual):
                return {"requested": False, "reason": f"receipt source does not match the armed {key}"}
        finding = next((f for f in data.get("findings", []) if f.get("finding_id") == finding_id), None)
        if finding is None:
            return {"requested": False, "reason": "unknown or stale invariant finding id"}
        if finding.get("kind") in {"unparseable", "unreadable_parent"}:
            return {"requested": False, "reason": "hard invariant findings cannot be acknowledged"}
        receipts = data.setdefault("receipts", [])
        if any(r.get("finding_id") == finding_id and r.get("fingerprint_sha256") == (finding.get("fingerprint") or {}).get("sha256") for r in receipts):
            return {"requested": False, "reason": "that finding was already acknowledged", "duplicate": True}
        receipt = {
            "finding_id": finding_id,
            "fingerprint_sha256": (finding.get("fingerprint") or {}).get("sha256"),
            "acknowledged_at": now,
            "source": {"platform": source.get("platform"), "chat_id": source.get("chat_id"), "thread_id": source.get("thread_id"), "user_id": source.get("user_id")},
        }
        receipts.append(receipt)
        data.setdefault("journal", []).append({"event": "ack", **receipt})
        data["updated_at"] = now
        _write_json_atomic(state / "invariants-pending.json", data)
        request = state / "finalize-request.json"
        processing = state / "finalize-request.processing.json"
        if request.exists() or processing.exists():
            return {"requested": False, "reason": "a finalize is already in flight; the receipt is recorded"}
        _write_json_atomic(request, {
            "action": "ack-invariant",
            "finding_id": finding_id,
            "receipt": receipt,
            "requested_at": now,
            "origin": {"platform": _normalize_platform(source.get("platform")), "chat_id": source.get("chat_id"), "thread_id": source.get("thread_id"), "user_id": source.get("user_id")},
        })
        return {"requested": True, "receipt": receipt}


# Stable aliases for callers that name the gate rather than the storage file.
has_pending_upstream_sync_ack = has_pending_upstream_invariant_ack
record_upstream_sync_ack = record_invariant_ack

# Matches ``<n>: <option>`` where option is one of the allowed phrases. The two
# option words may be joined by whitespace, a hyphen, or an underscore, so the
# skill canonical tokens the conflict report presents (merge-both / keep-local /
# take-upstream, per pending.json ``options``) are recognized as well as the
# spelled-out forms. Surrounding prose and separators (commas, newlines, "and")
# are tolerated.
_DECISION_RE = re.compile(
    r"(\d+)\s*:\s*(merge[\s_-]+both|keep[\s_-]+local|take[\s_-]+upstream)",
    re.IGNORECASE,
)


def parse_upstream_sync_decision_reply(text: Optional[str]) -> Optional[dict[int, str]]:
    """Parse an operator decision reply into ``{feature_id: option}``.

    Returns ``None`` when the text contains no recognizable ``N: <option>``
    pair, so a plain conversational message is never mistaken for a decision.
    """
    if not isinstance(text, str) or not text.strip():
        return None

    decisions: dict[int, str] = {}
    for raw_id, raw_option in _DECISION_RE.findall(text):
        try:
            feature_id = int(raw_id)
        except ValueError:
            continue
        option = re.sub(r"[\s_-]+", " ", raw_option.lower()).strip()
        if option in _VALID_OPTIONS:
            decisions[feature_id] = option

    return decisions or None


def has_pending_upstream_decision(state_dir: Path | str) -> bool:
    """Return True when ``<state_dir>/pending.json`` awaits an operator decision.

    The gateway runs as an unprivileged user, but the sandbox writes
    pending.json under a root-0700 home the gateway user cannot even traverse.
    When the status can't be read (PermissionError anywhere in the path), assume
    a decision is pending -- the queued one-shot re-checks the authoritative
    status as root inside the sandbox. The narrow decision-reply pattern is the
    primary gate, so this cannot fire on ordinary messages.
    """
    pending = Path(state_dir) / "pending.json"
    try:
        raw = pending.read_text(encoding="utf-8")
    except PermissionError:
        return True
    except OSError:
        return False
    try:
        data = json.loads(raw)
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    return data.get("status") == "awaiting_decision"

import datetime as _dt
import os

_CANON = {"merge both": "merge-both", "keep local": "keep-local", "take upstream": "take-upstream"}


def _canon(option: str) -> str:
    key = re.sub(r"[\s_-]+", " ", (option or "").strip().lower())
    return _CANON.get(key, key.replace(" ", "-"))


def _write_json_atomic(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def record_operator_decisions(state_dir: Path | str, decisions: dict, source: dict) -> dict:
    """Write the operator's answers into pending.json; request apply-decisions
    once every feature has one.

    The host owns the apply now (no one-shot agent): this only records and
    hands over. ``decisions`` maps the F-number to an option in any accepted
    spelling; ``source`` carries the Slack thread so the host can report there.

    Returns {"applied": [ids], "still_awaiting": [ids], "unknown": [numbers],
             "requested": bool, "reason": str|None}.
    """
    state = Path(state_dir)
    pending_path = state / "pending.json"
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    features = pending.get("features", [])
    by_number = {}
    for f in features:
        m = re.fullmatch(r"F(\d+)", str(f.get("id") or ""))
        if m:
            by_number[int(m.group(1))] = f
    applied, unknown = [], []
    for num, option in sorted(decisions.items()):
        feat = by_number.get(int(num))
        if feat is None:
            unknown.append(int(num))
            continue
        feat["decision"] = _canon(option)
        feat["status"] = "decided"
        feat["source"] = "operator"
        applied.append(feat["id"])
    still = [f["id"] for f in features if not f.get("decision")]
    if source.get("chat_id"):
        pending["slack_channel"] = source.get("chat_id")
    if source.get("thread_id"):
        pending["slack_thread_ts"] = source.get("thread_id")
    pending["status"] = "awaiting_decision" if still else "auto_apply"
    pending["decided_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    _write_json_atomic(pending_path, pending)

    requested, reason = False, None
    if not still:
        if any((state / n).exists() for n in ("finalize-request.json", "finalize-request.processing.json")):
            reason = "a finalize is already in flight; the decision is recorded and will be picked up"
        else:
            _write_json_atomic(state / "finalize-request.json", {
                "action": "apply-decisions",
                "requested_at": pending["decided_at"],
                "origin": {"platform": _normalize_platform(source.get("platform")),
                           "chat_id": source.get("chat_id"), "thread_id": source.get("thread_id"),
                           "user_id": source.get("user_id")},
            })
            requested = True
    return {"applied": applied, "still_awaiting": still, "unknown": unknown,
            "requested": requested, "reason": reason}


_record_operator_decisions_unlocked = record_operator_decisions


def record_operator_decisions(state_dir: Path | str, decisions: dict, source: dict) -> dict:
    with _state_lock(Path(state_dir)):
        return _record_operator_decisions_unlocked(state_dir, decisions, source)


_STATE_SUFFIX = "state/upstream-sync"


def default_upstream_sync_state_dir() -> Path:
    """Resolve the host-side upstream-sync state dir.

    Mirrors upstream-sync-finalize.sh: honor ``HERMES_SYNC_STATE_DIR`` when set,
    else derive from ``HERMES_HOME``. The state deliberately sits outside the
    sandbox mirror: that tree is provisioned as root, and a chmod there
    recalculates the POSIX ACL mask and voids the traverse grant the host user
    needs -- which killed the sync cron on 2026-08-18.
    """
    override = os.getenv("HERMES_SYNC_STATE_DIR")
    if override:
        return Path(override)
    hermes_home = Path(os.getenv("HERMES_HOME") or (Path.home() / ".hermes"))
    return hermes_home / _STATE_SUFFIX


def _normalize_platform(value) -> "str | None":
    """Normalize a platform value to its lowercase name.

    The gateway passes ``source.platform`` which stringifies to an enum repr
    like ``Platform.SLACK``; cron origin-delivery compares lowercase platform
    names, so an un-normalized value silently breaks delivery back to the
    thread.
    """
    s = str(value or "").strip()
    if not s:
        return None
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    return s.lower()


def build_upstream_sync_decision_job_spec(
    message: str,
    source: dict,
    decisions: dict[int, str],
) -> dict:
    """Build ``create_job`` kwargs for a one-shot upstream-sync Mode B apply.

    The operator reply is carried verbatim into the prompt so the skill matches
    decisions to feature ids; ``role="engineer"`` pins the role (bypassing the
    keyword cascade), and ``deliver="origin"`` routes the report back to the
    reply thread.
    """
    decision_line = ", ".join(f"{fid}: {opt}" for fid, opt in sorted(decisions.items()))
    prompt = (
        "Operator has replied with upstream-sync merge decisions. "
        "Load the upstream-sync skill Mode B: read pending.json and apply these "
        f"decisions, then finalize.\n\nOperator decisions: {decision_line}\n\n"
        f"Original reply:\n{message}"
    )
    origin = {
        "platform": _normalize_platform(source.get("platform")),
        "chat_id": source.get("chat_id"),
        "thread_id": source.get("thread_id"),
        "user_id": source.get("user_id"),
    }
    return {
        "prompt": prompt,
        "schedule": "1m",
        "name": "upstream-sync apply (operator decision)",
        "skills": ["upstream-sync"],
        "role": "engineer",
        "deliver": "origin",
        "origin": origin,
    }


def build_progress_reporter_argv(origin, *, repo, hermes_bin, script_path):
    """Build argv for the detached progress reporter, or ``None`` if the origin
    lacks a thread target (reporter posts into the operator's reply thread, so
    it needs platform:chat_id:thread_id)."""
    platform = _normalize_platform(origin.get("platform"))
    chat_id = origin.get("chat_id")
    thread_id = origin.get("thread_id")
    if not (platform and chat_id and thread_id):
        return None
    target = f"{platform}:{chat_id}:{thread_id}"
    return [
        script_path,
        "--target", target,
        "--repo", repo,
        "--hermes-bin", hermes_bin,
    ]


# ---------------------------------------------------------------------------
# Triage gate — the host proposes a test patch, the operator answers one word
# ---------------------------------------------------------------------------
#
# When the fork-test gate goes red on an upstream merge, the host diagnoses and
# PROPOSES a patch to the failing test; the operator decides. Automation that
# edits a test to make a red gate green would eventually paper over a real
# regression — the fork tests are the only sensor for a merge that silently
# dropped local behaviour.
#
# The reply is matched WHOLE-MESSAGE, the way ops_gate_service.parse_ops_reply
# matches its approval words: normalize (strip, collapse whitespace, casefold,
# drop trailing punctuation, cap the length) and then require exact equality.
# A sentence that merely contains "apply fix" — a quote, a plan, a reply with a
# trailing clause — is not an answer. This parser runs BEFORE the decision-reply
# parser precisely because it is the strict one: a strict parser cannot steal a
# message meant for the looser gate, but the reverse is not true.

_TRIAGE_APPLY = ("apply fix", "apply the fix", "применить правку", "применяй правку")
_TRIAGE_KEEP = ("keep test", "keep the test", "оставить тест", "оставь тест")
_TRIAGE_MAX_LEN = 40


def _normalize_word_reply(text) -> str:
    if not isinstance(text, str):
        return ""
    s = " ".join(text.split()).strip()
    if len(s) > _TRIAGE_MAX_LEN:
        return ""
    return s.rstrip(".!,;:").strip().casefold()


def parse_upstream_sync_triage_reply(text) -> Optional[str]:
    """``"apply_fix"`` / ``"keep_test"`` for an exact one-word answer, else None."""
    s = _normalize_word_reply(text)
    if not s:
        return None
    if s in _TRIAGE_APPLY:
        return "apply_fix"
    if s in _TRIAGE_KEEP:
        return "keep_test"
    return None


TRIAGE_FILE = "gate-triage.json"


def load_triage(state_dir: Path | str) -> dict:
    try:
        data = json.loads((Path(state_dir) / TRIAGE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def has_pending_upstream_triage(state_dir: Path | str) -> bool:
    """True only while a proposal is armed and unanswered."""
    return load_triage(state_dir).get("status") == "awaiting_triage"


def _record_triage_decision_unlocked(state_dir: Path | str, answer: str, source: dict) -> dict:
    """Record the operator answer; request apply-triage-fixes for ``apply_fix``.

    Returns {"status": <new status>, "requested": bool, "reason": str|None}.
    """
    state = Path(state_dir)
    triage = load_triage(state)
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    triage["answered_at"] = now
    if answer == "keep_test":
        triage["status"] = "rejected"
        _write_json_atomic(state / TRIAGE_FILE, triage)
        return {"status": "rejected", "requested": False, "reason": None}

    proposals = [p for p in (triage.get("proposals") or []) if p.get("patch")]
    if not proposals:
        # Diagnosis only: validation refused every patch, so there is nothing to
        # apply and the merge stays where it is.
        triage["status"] = "rejected"
        _write_json_atomic(state / TRIAGE_FILE, triage)
        return {"status": "rejected", "requested": False,
                "reason": "the triage carries no patch to apply (diagnosis only)"}
    if any((state / n).exists() for n in ("finalize-request.json", "finalize-request.processing.json")):
        return {"status": triage.get("status"), "requested": False,
                "reason": "a finalize is already in flight; answer again once it reports"}

    # "applying", not "applied": the gate is disarmed the moment it is answered
    # (a duplicate reply must not arm a second request), but only the finalizer
    # gets to say the fix actually landed.
    triage["status"] = "applying"
    _write_json_atomic(state / TRIAGE_FILE, triage)
    _write_json_atomic(state / "finalize-request.json", {
        "action": "apply-triage-fixes",
        "requested_at": now,
        "origin": {"platform": _normalize_platform(source.get("platform")),
                   "chat_id": source.get("chat_id"), "thread_id": source.get("thread_id"),
                   "user_id": source.get("user_id")},
    })
    return {"status": "applying", "requested": True, "reason": None}



def record_triage_decision(state_dir: Path | str, answer: str, source: dict) -> dict:
    with _state_lock(Path(state_dir)):
        return _record_triage_decision_unlocked(state_dir, answer, source)
