"""Phase III Stage 1 — soft feasibility advisory (observe→advisory).

Owner gate C2: surface the semantic shadow's feasibility flags on roles the
user was ALREADY shown (prod recommendation != reject), as a SEPARATE
advisory Slack message. It does not touch the cards (webhook messages can't
be edited), does not filter or rerank anything, and changes no production
decision — it only adds caveats. Off by default (SEMANTIC_SHADOW_ADVISORY_
ENABLED); a dry-run renders the message without posting.

Pure production module: store + stdlib only, no semantic imports.
"""
from __future__ import annotations

import os
from typing import Any

ADVISORY_ENV = "SEMANTIC_SHADOW_ADVISORY_ENABLED"


def advisory_enabled() -> bool:
    """User-facing, so OFF by default (unlike the observe-only shadow which
    is ON): nothing posts to Slack until the owner explicitly enables it."""
    return (os.getenv(ADVISORY_ENV, "0") or "0").strip().lower() in {"1", "true", "yes"}


def _has_concern(feas: dict | None) -> bool:
    """Only a CONCRETE statement qualifies as an advisory.

    A bare `uncertain` verdict carries no text to show — on live data 38 of
    39 candidate roles were exactly that, which would have produced 39 bullet
    lines with nothing under them. An advisory that cannot say what the
    concern is, is noise; the verdict alone is not advisory-worthy.
    """
    if not feas:
        return False
    return bool(feas.get("blockers") or feas.get("unknowns"))


def build_feasibility_advisory(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """rows: shown vacancies (prod != reject) with a shadow `feasibility`.
    Keep only those with a feasibility concern; a feasible/absent one adds no
    caveat and is dropped."""
    out = []
    for r in rows:
        feas = r.get("feasibility")
        if not _has_concern(feas):
            continue
        out.append({
            "company": r.get("company"),
            "title": r.get("title"),
            "url": r.get("url"),
            "prod_recommendation": r.get("prod_recommendation"),
            "verdict": feas.get("verdict"),
            "blockers": feas.get("blockers") or [],
            "unknowns": feas.get("unknowns") or [],
        })
    return out


def format_advisory(items: list[dict[str, Any]], *, run_label: str) -> str | None:
    """Slack text. Returns None when there is nothing to advise."""
    if not items:
        return None
    lines = [
        "⚠️ *Feasibility advisory* (observe-only — does not change any "
        "recommendation, ranking, or which roles you were shown)",
        f"_{run_label}: {len(items)} shown role(s) carry a feasibility caveat "
        "from the semantic shadow._",
        "",
    ]
    for it in items:
        head = f"• *{it['company']} — {it['title']}*"
        if it.get("url"):
            head = f"• *<{it['url']}|{it['company']} — {it['title']}>*"
        lines.append(head)
        for b in it["blockers"]:
            lines.append(f"    🚫 {b}")
        for u in it["unknowns"]:
            lines.append(f"    ❔ {u}")
    return "\n".join(lines)


# Job-intel cards are delivered through the hermes gateway adapter, not a
# webhook (JOB_INTEL_SLACK_WEBHOOK_URL is empty in production, so cli.py's
# _deliver_to_slack always takes the gateway branch). The advisory uses the
# SAME path so it lands in the same place, with the same auth.
DEFAULT_ADVISORY_CHANNEL = "C0B4MM6D52A"  # executive_search_report


def _gateway_send(payload: dict[str, str]) -> str:
    from tools.send_message_tool import send_message_tool

    return send_message_tool(payload)


def post_advisory(message: str, *, dry_run: bool = True,
                  channel: str | None = None) -> dict[str, Any]:
    """Deliver the advisory through the hermes gateway. dry_run (default)
    renders and posts nothing. Never raises: returns posted=False + error."""
    from job_intel.runtime import delivery_disabled

    if delivery_disabled():
        return {
            "posted": False,
            "dry_run": dry_run,
            "error": "outbound delivery is disabled by JOB_INTEL_DELIVERY_DISABLED",
        }
    if dry_run:
        return {"posted": False, "dry_run": True, "message": message}
    ch = (channel or os.getenv("SEMANTIC_SHADOW_ADVISORY_CHANNEL", "").strip()
          or DEFAULT_ADVISORY_CHANNEL)
    try:
        import json as _json

        raw = _gateway_send({"target": f"slack:{ch}", "message": message})
        resp = _json.loads(raw) if raw else {}
    except Exception as exc:
        return {"posted": False, "error": f"gateway delivery error: {exc}"}
    if resp.get("error"):
        return {"posted": False, "error": str(resp["error"])}
    if resp.get("success"):
        return {"posted": True, "dry_run": False, "ts": resp.get("ts")}
    return {"posted": False, "error": f"unexpected gateway response: {resp}"}


def describe_post_result(result: dict[str, Any], *, run_id: Any, count: int) -> str:
    """Single source of truth for the operator-facing line. Reports what
    ACTUALLY happened — a delivery failure must never read as a success."""
    if result.get("posted"):
        return f"[advisory] run {run_id}: posted {count} caveat(s)"
    if result.get("dry_run"):
        return f"[advisory] run {run_id}: DRY-RUN — {count} caveat(s), nothing sent"
    return (f"[advisory] run {run_id}: NOT POSTED (delivery failed) — "
            f"{count} caveat(s); error: {result.get('error')}")
