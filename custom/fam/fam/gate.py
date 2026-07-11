"""Style gate: quiet hours, daily budget, LLM rewrite, delivery.

Domain function never commits -- deliver(conn, ...) uses the caller's
connection/transaction, mirroring cal.py/rem.py's pattern. The reminders
tick (fam tick reminders, Task 6) owns the commit; tests commit explicitly
before asserting on audit rows.

Every real hermes invocation goes through the module-level HERMES prefix
via subprocess.run -- tests monkeypatch subprocess.run and never touch a
real hermes process.
"""
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fam import audit

ALMATY = ZoneInfo("Asia/Almaty")

# Same venv/interpreter fam itself runs under on the host.
HERMES = ["/home/denis/.hermes/hermes-agent/venv/bin/python", "-m", "hermes_cli.main"]

CONFIG_PATH = Path("/home/denis/.hermes/private/amina/fam-config.json")
CONFIG_EXAMPLE_PATH = Path(__file__).resolve().parent.parent / "fam-config.example.json"

# Keys added to fam-config.example.json after a live config may already
# exist on disk. load_config() default-merges any of these missing from
# the loaded JSON so an older live config keeps working without a manual
# edit -- the file on disk is never rewritten for this, only the in-memory
# dict returned to the caller.
CONFIG_DEFAULTS = {"reminder_max_age_min": 120}

GATE_STYLE_INSTRUCTION = (
    "Ты пишешь как Гермес — тёплый, лаконичный ассистент семьи. Перепиши "
    "заново в 1-3 коротких предложения. Тон тёплый, без канцелярита и "
    "приветствий-вступлений («привет», «вот твоё напоминание» и т.п.) — "
    "сразу суть. Сохраняй только цифры, которые несут решение (время, "
    "адрес, сумма) — остальные детали убирай."
)

# Live-found bug: a real digest went out with the weather/events summary
# but without its closing question -- the LLM rewrite's own brevity rules
# ("1-3 коротких предложения") won out over preserving raw["question"].
# Fix: the digest's closing question is no longer the LLM's job at all.
# This instruction tells the rewrite not to write one of its own, and
# deliver() appends the real question (raw["question"]) itself,
# deterministically, after the rewrite -- see _ensure_trailing_question.
GATE_DIGEST_NO_QUESTION_INSTRUCTION = (
    "Не задавай вопросов и не добавляй призывов — только сводка. "
    "Если в сводке есть погода — обязательно укажи диапазон температур (минимум…максимум). "
    "Лаконичность не должна терять факты: каждое поле сводки должно быть отражено в тексте."
)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_utc(value):
    """Parse an ISO-8601 string into an aware UTC datetime. A naive string
    (no tzinfo) is treated as already UTC.
    """
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_config(config_path=None, example_path=None):
    """Load fam-config.json. If the live file doesn't exist yet, it is
    created by copying the git-tracked example template first (an
    idempotent first-run bootstrap), then read back.

    config_path/example_path are injection points for tests; they default
    to the real live path (~/.hermes/private/amina/fam-config.json) and
    the git template (custom/fam/fam-config.example.json).

    Any key in CONFIG_DEFAULTS missing from the loaded JSON (a live config
    that predates that key) is default-merged into the returned dict --
    see CONFIG_DEFAULTS's docstring.
    """
    cfg_path = Path(config_path) if config_path is not None else CONFIG_PATH
    ex_path = Path(example_path) if example_path is not None else CONFIG_EXAMPLE_PATH
    if not cfg_path.exists():
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(ex_path.read_text(encoding="utf-8"), encoding="utf-8")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    for key, value in CONFIG_DEFAULTS.items():
        cfg.setdefault(key, value)
    return cfg


def in_quiet_hours(now_utc, cfg):
    """True if now_utc (ISO-8601 UTC string) falls in the quiet window,
    expressed in Asia/Almaty local time. The window may cross midnight
    (e.g. 21:30-07:30): quiet_start is inclusive, quiet_end is exclusive.
    """
    local_time = _parse_utc(now_utc).astimezone(ALMATY).time()
    start = datetime.strptime(cfg["quiet_start"], "%H:%M").time()
    end = datetime.strptime(cfg["quiet_end"], "%H:%M").time()
    if start <= end:
        return start <= local_time < end
    return local_time >= start or local_time < end


def _almaty_day_utc_bounds(now_utc):
    local = _parse_utc(now_utc).astimezone(ALMATY)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(timezone.utc).isoformat(timespec="seconds"),
        end_local.astimezone(timezone.utc).isoformat(timespec="seconds"),
    )


def budget_spent_today(conn, now_utc=None):
    """Count of gate.sent audit rows within "today" in Asia/Almaty local
    time (relative to now_utc, defaulting to the real current time),
    EXCLUDING kind="digest" rows -- the digest is delivered with force=True
    outside the daily budget (plan: "дайджест вне бюджета"), so its own
    gate.sent row must not shrink the budget available to reminders. The
    payload's inner "kind" (reminder/digest/...) is what's filtered on,
    not audit_log.kind (which is always the literal string "gate.sent").
    """
    from_utc, to_utc = _almaty_day_utc_bounds(now_utc or _now())
    rows = conn.execute(
        "SELECT payload FROM audit_log WHERE kind='gate.sent' "
        "AND ts_utc >= ? AND ts_utc < ?",
        (from_utc, to_utc),
    ).fetchall()
    return sum(1 for r in rows if json.loads(r["payload"]).get("kind") != "digest")


def _build_prompt(raw, kind=None):
    """Build the rewrite prompt for `raw`. For kind=="digest", the style
    instruction gets GATE_DIGEST_NO_QUESTION_INSTRUCTION appended -- the
    LLM must never write its own closing question/CTA for a digest;
    deliver() owns that deterministically (see _ensure_trailing_question).
    """
    instruction = GATE_STYLE_INSTRUCTION
    if kind == "digest":
        instruction = f"{instruction} {GATE_DIGEST_NO_QUESTION_INSTRUCTION}"
    return (
        f"{instruction}\n"
        "Перепиши следующий факт для отправки пользователю: "
        f"{json.dumps(raw, ensure_ascii=False)}"
    )


def _shorten_prompt(text, max_len):
    return f"Сократи до {max_len} знаков: {text}"


def _strip_trailing_question(text, question):
    """Remove a trailing occurrence of `question` from `text` (and any
    whitespace/newline separator right before it), if present. The
    return value never ends with `question`.

    Used so the digest's closing question can always be appended in
    canonical form (see _ensure_trailing_question) without ever
    double-including it -- regardless of whether `text` is an LLM
    rewrite (instructed to never add its own question -- see
    GATE_DIGEST_NO_QUESTION_INSTRUCTION -- so this is normally a no-op
    for it) or the deterministic human_fallback (which already ends with
    the question by construction, in tick._build_digest_fallback).
    """
    stripped = text.rstrip()
    if stripped.endswith(question):
        stripped = stripped[: -len(question)].rstrip()
    return stripped


def _ensure_trailing_question(text, question):
    """Return `text` with `question` guaranteed to be present as its own
    final line, exactly once -- strip-then-append rather than a presence
    check, so formatting differences (e.g. a single vs double newline
    before it) always collapse to the same canonical form.
    """
    return f"{_strip_trailing_question(text, question)}\n\n{question}"


def _call_rewrite(prompt, cfg):
    """Run `hermes -z PROMPT -m MODEL --provider PROVIDER`. Returns the
    stripped stdout text, or None on ANY failure (timeout, process error,
    non-zero exit, empty output) -- callers fall back to a human-written
    text rather than propagating an exception.
    """
    try:
        result = subprocess.run(
            HERMES + ["-z", prompt, "-m", cfg["gate_model"],
                      "--provider", cfg["gate_provider"]],
            capture_output=True, text=True, timeout=90,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    text = (result.stdout or "").strip()
    return text or None


def _call_send(text, cfg):
    """Run `hermes send -t TARGET` with text on stdin. Returns True on a
    zero exit code, False on ANY failure (timeout, process error,
    non-zero exit).
    """
    try:
        result = subprocess.run(
            HERMES + ["send", "-t", cfg["target"]],
            input=text, capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def deliver(conn, kind, raw, human_fallback, cfg, force=False, now_utc=None):
    """Rewrite raw into Hermes's voice and deliver it. Returns one of:
    "sent", "quiet", "budget", "error".

    Pipeline (force=True skips the quiet-hours and budget gates):
      1. quiet hours -> audit gate.skip{reason:"quiet"}, return "quiet".
      2. daily budget reached -> audit gate.skip{reason:"budget"},
         return "budget".
      3. rewrite via hermes -z; any failure/empty output falls back to
         human_fallback (attempt="fallback" vs "rewrite").
      3b. digest closing question (kind=="digest" and raw["question"] is
          a non-empty string): final_text is guaranteed to end with
          raw["question"] as its own last line, exactly once, on BOTH
          the rewrite and fallback paths (_ensure_trailing_question) --
          see GATE_DIGEST_NO_QUESTION_INSTRUCTION's docstring for why
          this is deterministic rather than left to the LLM. Any other
          kind (or a digest with no/blank raw["question"]) skips this
          step entirely.
      4. length ceiling (max_len_reminder for kind="reminder",
         max_len_digest otherwise), checked against the combined text:
         over -> one "Сократи до N знаков:" retry; still over -> send
         as-is with long=True in the audit. When step 3b applied, the
         shorten-retry targets the informational part only (the question
         stripped back off first) so the question is never sent through
         a "shorten to N chars" instruction, then is re-appended after --
         the question itself is never truncated away, even when the
         combined text is still over max_len afterwards (long=True
         covers that case, same as the no-question path).
      5. send via hermes send; failure -> audit gate.error, return
         "error". Success -> audit gate.sent{kind,raw,final,attempt[,long]},
         return "sent".
    """
    now = now_utc or _now()

    if not force and in_quiet_hours(now, cfg):
        audit.log(conn, "gate.skip", {"kind": kind, "reason": "quiet"})
        return "quiet"

    if not force and budget_spent_today(conn, now_utc=now) >= cfg["daily_budget"]:
        audit.log(conn, "gate.skip", {"kind": kind, "reason": "budget"})
        return "budget"

    rewritten = _call_rewrite(_build_prompt(raw, kind), cfg)
    if rewritten is not None:
        final_text = rewritten
        attempt = "rewrite"
    else:
        final_text = human_fallback
        attempt = "fallback"

    question = raw.get("question") if kind == "digest" else None
    if not (isinstance(question, str) and question.strip()):
        question = None
    if question is not None:
        final_text = _ensure_trailing_question(final_text, question)

    max_len = cfg["max_len_reminder"] if kind == "reminder" else cfg["max_len_digest"]
    long_flag = False
    if len(final_text) > max_len:
        if question is not None:
            informational = _strip_trailing_question(final_text, question)
            shortened = _call_rewrite(_shorten_prompt(informational, max_len), cfg)
            if shortened is not None:
                final_text = _ensure_trailing_question(shortened, question)
            if len(final_text) > max_len:
                long_flag = True
        else:
            shortened = _call_rewrite(_shorten_prompt(final_text, max_len), cfg)
            if shortened is not None and len(shortened) <= max_len:
                final_text = shortened
            else:
                long_flag = True

    if not _call_send(final_text, cfg):
        audit.log(conn, "gate.error",
                   {"kind": kind, "raw": raw, "final": final_text, "attempt": attempt})
        return "error"

    payload = {"kind": kind, "raw": raw, "final": final_text, "attempt": attempt}
    if long_flag:
        payload["long"] = True
    audit.log(conn, "gate.sent", payload)
    return "sent"
