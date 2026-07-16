# custom/fam/fam/brevity.py
"""Phase 6c weekly brevity audit: corpus builder (pure read) + deterministic
stats. The LLM reviewer (aux model) lives in review() (Task 2); stats are
computed HERE, in code, never by the model."""
import json
from datetime import datetime, timezone, timedelta

def _now_utc():
    return datetime.now(timezone.utc)

def _raw_text(raw):
    if not isinstance(raw, dict):
        return str(raw)
    for key in ("label", "text", "question", "summary"):
        v = raw.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return json.dumps(raw, ensure_ascii=False)

def collect_corpus(conn, cfg, now=None):
    now = now or _now_utc()
    days = cfg.get("brevity_window_days", 7)
    since = (now - timedelta(days=days)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT ts_utc, payload FROM audit_log WHERE kind='gate.sent' "
        "AND ts_utc >= ? ORDER BY id", (since,)).fetchall()
    items = []
    for r in rows:
        p = json.loads(r["payload"])
        final = p.get("final")
        if not final:
            continue
        items.append({"kind": p.get("kind"), "raw_text": _raw_text(p.get("raw")),
                      "final": final, "ts_utc": r["ts_utc"]})
    total = len(items)
    rewritten = sum(1 for i in items if i["raw_text"] != i["final"])
    avg_len = (sum(len(i["final"]) for i in items) / total) if total else 0.0
    stats = {"total": total, "days": days,
             "per_day": round(total / days, 2) if days else 0.0,
             "rewrite_ratio": round(rewritten / total, 2) if total else 0.0,
             "avg_len": round(avg_len, 1)}
    return {"items": items, "stats": stats}


import subprocess
from . import gate

REVIEW_INSTRUCTION = (
    "Ты — ревьюер стиля ассистента Гермеса. Ниже — ЖЕЛАЕМАЯ ПЕРСОНА Гермеса "
    "(каким он ДОЛЖЕН быть) и его исходящие сообщения за неделю (kind, "
    "raw_text — до шлюза, final — как отправлено).\n\n"
    "ПЕРСОНА (эталон, к которому приводим стиль):\n{soul}\n\n"
    "Твоя задача — оценить, насколько сообщения соответствуют ЭТОЙ персоне, "
    "а НЕ сделать их максимально сухими. Гермес лаконичен (1–3 предложения), "
    "но остаётся тёплым, дружелюбным и живым: уместный юмор, умеренные "
    "эмодзи, проактивные предложения — это ЧАСТЬ персоны, а не дефекты. НЕ "
    "рекомендуй убирать теплоту, эмодзи, юмор или проактивность и не своди "
    "всё к одной сухой фразе.\n\n"
    "Флагуй ТОЛЬКО настоящие отклонения от персоны:\n"
    "— реальное многословие и служебный мусор (простыни, лишние цифры, "
    "внутренняя диагностика вроде «погода не указана»);\n"
    "— дословное дублирование (одна фраза повторена в сообщении дважды);\n"
    "— подмену намерения сообщения (например, дайджест о планах "
    "превратился в погодный отчёт);\n"
    "— выдуманные факты, адресаты или события (шлюз добавил то, чего нет "
    "в raw_text).\n"
    "Отдельно отметь, где шлюз переписывал (raw_text≠final): полезно "
    "уточнял (время/место/действие) или искажал/выдумывал.\n\n"
    "Выбери 3–5 худших примеров и перепиши их короче/честнее, НО с "
    "сохранением тёплого живого тона персоны (не в сухого робота). Предложи "
    "правки стиль-промпта/SOUL.md в том же духе: «сохранить характер, "
    "убрать лишнее», а не «сделать суше».\n\n"
    "Ответь СТРОГО одним JSON-объектом с ключами: assessment (str), "
    "rewrite_gap (str), examples (list of {{before, after}}), edits (list of "
    "str). Без текста вне JSON."
)

_PERSONA_FALLBACK = (
    "Гермес — тёплый, дружелюбный личный ассистент Амины (мужской род, на «ты», "
    "по-русски). Лаконичен: 1–3 предложения. Но живой — уместный юмор, умеренные "
    "эмодзи, проактивные предложения. Не выдумывает факты; техпроблемы сообщает "
    "Денису, не грузит ими Амину."
)

def _load_persona(cfg):
    """Read the desired persona (SOUL.md) for the reviewer; fall back to a short
    embedded summary if the file is missing/unreadable so the reviewer always
    gets the character."""
    import os
    path = cfg.get("brevity_soul_path")
    if path:
        try:
            with open(os.path.expanduser(path), encoding="utf-8") as f:
                text = f.read().strip()
            if text:
                return text
        except OSError:
            pass
    return _PERSONA_FALLBACK

def _call_reviewer(prompt, cfg):
    """Run the aux model via the same security-pinned path as
    gate._call_rewrite (-t clarify). Returns stdout text or None on any
    failure (timeout, OSError, non-zero exit, empty)."""
    try:
        result = subprocess.run(
            gate.HERMES + ["-z", prompt, "-m", cfg["brevity_model"],
                           "--provider", cfg["brevity_provider"], "-t", "clarify"],
            capture_output=True, text=True, timeout=180)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None

def _extract_json(text):
    """Parse the first {...} block; tolerate model chatter around it.
    Unparseable -> None."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None

def review(corpus, cfg, caller=None):
    """Aux-LLM review of the corpus. Returns the parsed dict on success, or
    None on any failure (caller down / unparseable / wrong shape) so the
    orchestrator emits a 'skipped' note instead of a fabricated report."""
    caller = caller or _call_reviewer
    persona = _load_persona(cfg)
    prompt = (f"{REVIEW_INSTRUCTION.format(soul=persona)}\nДанные: "
              f"{json.dumps(corpus['items'], ensure_ascii=False)}")
    raw = caller(prompt, cfg)
    if not raw:
        return None
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict) or "examples" not in parsed:
        return None
    return parsed

from . import db as famdb

def _format_report(stats, review_out):
    lines = ["Гермес — аудит лаконичности за неделю:",
             f"сообщений: {stats['total']} (~{stats.get('per_day', 0)}/день), "
             f"правок шлюзом: {int(stats.get('rewrite_ratio', 0) * 100)}%, "
             f"средняя длина: {stats.get('avg_len', 0)} зн."]
    if review_out.get("assessment"):
        lines.append(f"\nОценка: {review_out['assessment']}")
    if review_out.get("rewrite_gap"):
        lines.append(f"Шлюз: {review_out['rewrite_gap']}")
    if review_out.get("examples"):
        lines.append("\nБыло → надо:")
        for ex in review_out["examples"][:5]:
            lines.append(f"— «{ex.get('before','')}» → «{ex.get('after','')}»")
    if review_out.get("edits"):
        lines.append("\nПравки (совет, применяешь ты):")
        for e in review_out["edits"]:
            lines.append(f"• {e}")
    return "\n".join(lines)

def run_audit(cfg, now=None, notify=None, caller=None):
    """Weekly brevity audit: build corpus, review via aux LLM, deliver ONE
    message to Denis. Returns {sent, reason}. reason in {empty, llm_skip, ok}.
    Never fabricates a report (empty week / LLM failure send a short note)."""
    notify = notify or gate.notify_denis
    conn = famdb.connect()
    try:
        corpus = collect_corpus(conn, cfg, now=now)
    finally:
        conn.close()
    if corpus["stats"]["total"] == 0:
        notify("Гермес: за неделю исходящих Амине нет — аудит лаконичности пропущен.")
        return {"sent": True, "reason": "empty"}
    review_out = review(corpus, cfg, caller=caller)
    if review_out is None:
        notify("Гермес: аудит лаконичности пропущен: ревьюер недоступен.")
        return {"sent": True, "reason": "llm_skip"}
    notify(_format_report(corpus["stats"], review_out))
    return {"sent": True, "reason": "ok"}
