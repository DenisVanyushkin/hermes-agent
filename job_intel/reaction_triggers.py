"""Reaction-triggered vacancy tasks.

Evaluations and packages are triggered by Slack reactions on job-intel vacancy cards.
This module holds platform-independent pieces: reaction classification, prompt building,
and redelivery dedup.

State file layout mirrors ``idea_reaction_capture``: a JSON object with a
capped ``seen`` list of ``channel:ts:reaction`` keys.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

EVALUATION_REACTIONS = {"mag"}
PACKAGE_REACTIONS = {"+1", "thumbsup", "thumbs_up"}

_MAX_SEEN = 500


def classify_trigger(reaction: str) -> str | None:
    normalized = (reaction or "").strip().lower()
    if normalized in EVALUATION_REACTIONS:
        return "evaluation"
    if normalized in PACKAGE_REACTIONS:
        return "package"
    return None


def _vacancy_line(message: Mapping[str, Any]) -> str:
    title = str(message.get("title") or "").strip() or "(no title)"
    company = str(message.get("company") or "").strip() or "(unknown company)"
    url = str(message.get("canonical_url") or message.get("url") or "").strip()
    return f"{title} — {company}\nURL: {url}"


def build_trigger_prompt(kind: str, message: Mapping[str, Any]) -> str:
    vacancy = _vacancy_line(message)
    if kind == "evaluation":
        return (
            "Оцени вакансию и компанию, используя скиллы vacancy-evaluation и "
            "company-assessment из recruiter-пакета.\n"
            f"Вакансия: {vacancy}\n"
            "Дай оценку соответствия вакансии моему профилю и оценку компании "
            "(сигналы, риски, привлекательность). Ответь в этом треде. "
            "Весь ответ — полностью на русском языке."
        )
    if kind == "package":
        return (
            "Prepare the full application document package for this vacancy "
            "using the application-package-orchestrator skill from the "
            "recruiter package.\n"
            f"Vacancy: {vacancy}\n"
            "Deliver the resulting documents in this thread. "
            "All documents must be written in English."
        )
    raise ValueError(f"unknown trigger kind: {kind!r}")


def _state_file() -> Path:
    override = os.getenv("VACANCY_REACTION_TRIGGER_STATE_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    raw = os.getenv("HERMES_HOME", "").strip()
    home = Path(raw).expanduser() if raw else Path.home() / ".hermes"
    return home / "state" / "vacancy_reaction_triggers.json"


def _load_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("seen"), list):
            return data
    except Exception:
        pass
    return {"seen": []}


def should_process(*, channel: str, message_ts: str, reaction: str) -> bool:
    """Return True exactly once per (channel, ts, reaction); record it."""
    key = f"{channel}:{message_ts}:{(reaction or '').strip().lower()}"
    path = _state_file()
    state = _load_state(path)
    seen: list[str] = state["seen"]
    if key in seen:
        return False
    seen.append(key)
    if len(seen) > _MAX_SEEN:
        del seen[: len(seen) - _MAX_SEEN]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return True
