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
    if not feas:
        return False
    return bool(feas.get("blockers") or feas.get("unknowns")
                or feas.get("verdict") in ("infeasible", "uncertain"))


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


def post_advisory(message: str, *, dry_run: bool = True,
                  channel: str | None = None) -> dict[str, Any]:
    """Post the advisory via the same webhook the pipeline uses. dry_run
    (default) returns the rendered message and posts nothing."""
    if dry_run:
        return {"posted": False, "dry_run": True, "message": message}
    webhook = os.getenv("JOB_INTEL_SLACK_WEBHOOK_URL", "").strip()
    if not webhook:
        return {"posted": False, "error": "no JOB_INTEL_SLACK_WEBHOOK_URL"}
    import requests

    payload: dict[str, str] = {"text": message}
    ch = channel or os.getenv("SEMANTIC_SHADOW_ADVISORY_CHANNEL", "").strip()
    if ch:
        payload["channel"] = ch
    resp = requests.post(webhook, json=payload, timeout=20)
    resp.raise_for_status()
    return {"posted": True, "dry_run": False}
