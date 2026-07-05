"""Negative feedback loop: Slack thread prompt, reply parsing, classification.

Implements the collection half of the negative-feedback PRD:

    negative reaction -> feedback thread -> reason codes + free text
    -> attribution classifier -> CRM feedback event

Scoring calibration lives in :mod:`job_intel.calibration`; this module never
mutates scoring config (PRD core principle: a negative reaction is a
calibration signal, not a scoring update).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .feedback_taxonomy import (
    ALL_DETAIL_CODES,
    CATEGORY_BY_DETAIL_CODE,
    CATEGORY_COMPANY_QUALITY,
    CATEGORY_DATA_QUALITY,
    CATEGORY_LOCATION_WORK_FORMAT,
    CATEGORY_OTHER,
    CLASSIFIER_VERSION,
    DEFAULT_HARD_BLOCKER_CODES,
    KEYWORD_DETAIL_RULES,
    NO_PREFERENCE_PENALTY_CODES,
    NUMERIC_CATEGORY_MAP,
    REASON_CATEGORIES,
    SOFT_PREFERENCE_CODES,
    attribution_for,
    scoring_features_for,
)

logger = logging.getLogger(__name__)

NEGATIVE_REACTIONS = {"-1", "thumbsdown", "thumbs_down", "x"}

FEEDBACK_PROMPT_TEXT = (
    "Понял, вакансия не подходит.\n"
    "\n"
    "Что именно не понравилось?\n"
    "Можно выбрать несколько причин:\n"
    "\n"
    "1 — не та функция\n"
    "2 — недостаточно seniority / scope\n"
    "3 — нет remote / плохая локация\n"
    "4 — компания неинтересна или red flag\n"
    "5 — индустрия/рынок неинтересны\n"
    "6 — деньги/уровень, вероятно, не те\n"
    "7 — дубль / плохой парсинг / мусор\n"
    "8 — другое\n"
    "\n"
    "Можно просто написать текстом:\n"
    "«слишком junior, нет P&L, onsite Dubai».\n"
    "\n"
    "Ответ можно пропустить — он только улучшает будущий скоринг."
)

CLARIFICATION_TEXT = (
    "Не смог разобрать ответ. Напиши номера причин (1-8) или текстом, "
    "например: «2 3, нет P&L и onsite only»."
)


@dataclass
class ParsedReply:
    category_codes: list[str] = field(default_factory=list)
    detail_codes: list[str] = field(default_factory=list)
    free_text: str = ""
    recognized: bool = False


def parse_feedback_reply(text: str) -> ParsedReply:
    """Parse a thread reply: numbers, reason codes, free text — possibly mixed."""
    raw = (text or "").strip()
    parsed = ParsedReply(free_text=raw)
    if not raw:
        return parsed
    lowered = raw.casefold()

    categories: list[str] = []
    details: list[str] = []

    def add_category(category: str) -> None:
        if category not in categories:
            categories.append(category)

    def add_detail(detail: str) -> None:
        if detail not in details:
            details.append(detail)
            category = CATEGORY_BY_DETAIL_CODE.get(detail)
            if category:
                add_category(category)

    # 1) standalone numbers 1..8 (avoid matching digits inside words/numbers)
    for match in re.finditer(r"(?<![\w.])([1-8])(?![\w.])", raw):
        category = NUMERIC_CATEGORY_MAP.get(int(match.group(1)))
        if category:
            add_category(category)

    # 2) verbatim taxonomy codes (categories or detail codes)
    for token in re.findall(r"[a-z][a-z0-9_]{2,}", lowered):
        if token in ALL_DETAIL_CODES:
            add_detail(token)
        elif token in REASON_CATEGORIES:
            add_category(token)

    # 3) RU/EN keyword rules
    for needle, detail in KEYWORD_DETAIL_RULES:
        if needle in lowered:
            add_detail(detail)

    parsed.category_codes = categories
    parsed.detail_codes = details
    parsed.recognized = bool(categories or details)
    return parsed


def classify_feedback(parsed: ParsedReply) -> dict[str, Any]:
    """Rule-based classifier producing the PRD 13.2 output contract."""
    categories = list(parsed.category_codes)
    details = list(parsed.detail_codes)
    if not categories:
        categories = [CATEGORY_OTHER]
        if parsed.free_text:
            details = details or ["user_free_text_only"]

    attribution = attribution_for(categories, details)
    hard_blocker = any(code in DEFAULT_HARD_BLOCKER_CODES for code in details)
    soft_preference = bool(details) and all(
        code in SOFT_PREFERENCE_CODES for code in details
    )
    data_quality_only = bool(details) and all(
        code in NO_PREFERENCE_PENALTY_CODES for code in details
    )
    if not details:
        data_quality_only = categories == [CATEGORY_DATA_QUALITY]

    applies_to_company = CATEGORY_COMPANY_QUALITY in categories
    applies_to_parser_quality = (
        CATEGORY_DATA_QUALITY in categories
        or any(code in NO_PREFERENCE_PENALTY_CODES for code in details)
    )
    applies_to_location = CATEGORY_LOCATION_WORK_FORMAT in categories
    applies_to_industry = "industry" in attribution
    applies_to_role = any(
        target in attribution for target in ("role_function", "seniority_scope", "role_title")
    )

    if details:
        confidence = 0.9 if parsed.recognized else 0.3
    elif parsed.category_codes:
        confidence = 0.7
    else:
        confidence = 0.2

    needs_followup = not parsed.recognized
    scoring_features = [] if data_quality_only else scoring_features_for(details)

    return {
        "reason_category_codes": categories,
        "reason_detail_codes": details,
        "attribution_targets": attribution,
        "hard_blocker": hard_blocker,
        "soft_preference": soft_preference,
        "data_quality_only": data_quality_only,
        "applies_to_company": applies_to_company,
        "applies_to_role": applies_to_role,
        "applies_to_location": applies_to_location,
        "applies_to_industry": applies_to_industry,
        "applies_to_parser_quality": applies_to_parser_quality,
        "scoring_features_impacted": scoring_features,
        "confidence": confidence,
        "needs_followup": needs_followup,
        "classifier_version": CLASSIFIER_VERSION,
    }


def build_confirmation_text(classification: dict[str, Any]) -> str:
    details = classification["reason_detail_codes"] or classification["reason_category_codes"]
    lines = ["Записал negative feedback:", ""]
    lines.extend(f"- {code}" for code in details)
    lines.append("")
    lines.append("Attribution:")
    lines.extend(f"- {target}" for target in classification["attribution_targets"])
    guards = []
    if not classification["applies_to_company"]:
        guards.append("компанию не понижаю")
    if not classification["applies_to_industry"]:
        guards.append("индустрию не понижаю")
    if classification["data_quality_only"]:
        guards.append("это data-quality сигнал, не преференция")
    if guards:
        lines.append("")
        lines.append("Гарантии: " + ", ".join(guards) + ".")
    lines.append("")
    lines.append("Попадет в weekly calibration review.")
    return "\n".join(lines)


@dataclass
class FeedbackLoopService:
    """Orchestrates prompt creation, reply handling and CRM state updates.

    ``deliver`` posts a Slack message and must accept
    ``(message, channel, thread_ts)`` and return the posted message ts (or
    None on failure) — the CLI wires this to ``_deliver_to_slack``.
    """

    store: Any
    crm: Any | None = None
    deliver: Callable[[str, str, str], str | None] | None = None

    # --- Slice 3: prompt --------------------------------------------------

    def handle_negative_reaction(
        self,
        *,
        slack_channel_id: str,
        slack_message_ts: str,
        user_id: str,
        reaction: str,
        vacancy_id: int | None = None,
        company: str | None = None,
        raw_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = self.store.find_feedback_event_by_message(
            slack_channel_id=slack_channel_id,
            slack_message_ts=slack_message_ts,
            user_id=user_id,
        )
        if existing:
            return {"status": "reused", "feedback_event_id": existing["id"]}

        opportunity = None
        if self.crm is not None:
            try:
                opportunity = self.crm.find_opportunity_by_slack_message(
                    slack_channel_id, slack_message_ts
                )
            except Exception:
                logger.exception("feedback_loop: opportunity resolution failed")
        opportunity_id = int(opportunity["id"]) if opportunity else None
        if opportunity and not company:
            company = opportunity.get("company")

        event_id = self.store.create_feedback_event(
            slack_channel_id=slack_channel_id,
            slack_message_ts=slack_message_ts,
            user_id=user_id,
            reaction_type=reaction,
            opportunity_id=opportunity_id,
            vacancy_id=vacancy_id,
            company=company,
            slack_thread_ts=slack_message_ts,
            raw_payload_json=raw_payload or {},
        )

        prompt_ts: str | None = None
        if self.deliver is not None:
            try:
                prompt_ts = self.deliver(FEEDBACK_PROMPT_TEXT, slack_channel_id, slack_message_ts)
            except Exception:
                logger.exception("feedback_loop: prompt delivery failed")
        if prompt_ts:
            self.store.update_feedback_event(event_id, prompt_message_ts=prompt_ts)

        return {
            "status": "prompted" if prompt_ts else "created_without_prompt",
            "feedback_event_id": event_id,
            "opportunity_id": opportunity_id,
        }

    # --- Slices 4-6: reply handling ---------------------------------------

    def handle_thread_reply(
        self,
        *,
        slack_channel_id: str,
        slack_thread_ts: str,
        user_id: str,
        text: str,
    ) -> dict[str, Any]:
        event = self.store.find_feedback_event_awaiting_reply(
            slack_channel_id=slack_channel_id, slack_thread_ts=slack_thread_ts
        )
        if not event:
            return {"status": "not_feedback_thread"}

        parsed = parse_feedback_reply(text)

        if not parsed.recognized and not (parsed.free_text or "").strip():
            return {"status": "ignored_empty", "feedback_event_id": event["id"]}

        if not parsed.recognized and event["status"] == "awaiting_reply":
            # One clarification round max (PRD 12.5): keep raw text, ask once.
            self.store.update_feedback_event(
                event["id"],
                status="awaiting_followup",
                free_text=parsed.free_text,
                needs_followup=True,
                followup_question_sent_at=_utcnow(),
            )
            self._post_thread(slack_channel_id, slack_thread_ts, CLARIFICATION_TEXT)
            return {"status": "clarification_requested", "feedback_event_id": event["id"]}

        try:
            classification = classify_feedback(parsed)
        except Exception:
            logger.exception("feedback_loop: classifier failed, failing closed")
            self.store.update_feedback_event(
                event["id"],
                status="classified",
                free_text=parsed.free_text,
                classifier_confidence=None,
                needs_manual_review=True,
                followup_answer_received_at=_utcnow(),
            )
            return {"status": "stored_unclassified", "feedback_event_id": event["id"]}

        merged_free_text = parsed.free_text
        if event.get("free_text") and event["status"] == "awaiting_followup":
            merged_free_text = f"{event['free_text']}\n{parsed.free_text}".strip()

        needs_manual_review = classification["confidence"] < 0.5
        self.store.update_feedback_event(
            event["id"],
            status="classified",
            free_text=merged_free_text,
            reason_category_codes_json=classification["reason_category_codes"],
            reason_detail_codes_json=classification["reason_detail_codes"],
            attribution_targets_json=classification["attribution_targets"],
            classifier_version=classification["classifier_version"],
            classifier_confidence=classification["confidence"],
            hard_blocker=classification["hard_blocker"],
            soft_preference=classification["soft_preference"],
            applies_to_company=classification["applies_to_company"],
            applies_to_role=classification["applies_to_role"],
            applies_to_location=classification["applies_to_location"],
            applies_to_industry=classification["applies_to_industry"],
            applies_to_parser_quality=classification["applies_to_parser_quality"],
            scoring_features_impacted_json=classification["scoring_features_impacted"],
            needs_followup=False,
            needs_manual_review=needs_manual_review,
            followup_answer_received_at=_utcnow(),
        )

        self._update_opportunity_state(event, classification)

        confirmation = build_confirmation_text(classification)
        self._post_thread(slack_channel_id, slack_thread_ts, confirmation)
        return {
            "status": "classified",
            "feedback_event_id": event["id"],
            "classification": classification,
            "confirmation": confirmation,
        }

    # --- Slice 6: CRM opportunity state ------------------------------------

    def _update_opportunity_state(self, event: dict[str, Any], classification: dict[str, Any]) -> None:
        if self.crm is None or not event.get("opportunity_id"):
            return
        opportunity_id = int(event["opportunity_id"])
        details = set(classification["reason_detail_codes"])
        payload = {
            "feedback_event_id": event["id"],
            "reason_detail_codes": classification["reason_detail_codes"],
            "attribution_targets": classification["attribution_targets"],
        }
        try:
            if classification["data_quality_only"]:
                # Data-quality issue: no preference penalty, flag re-enrichment.
                self.crm.repo.add_event(
                    opportunity_id=opportunity_id,
                    event_type="needs_reenrichment",
                    event_source="feedback_loop",
                    actor=event.get("user_id"),
                    payload_json=payload,
                )
                self.crm.create_or_update_open_task(
                    opportunity_id=opportunity_id, task_type="reenrich_opportunity"
                )
            elif classification["soft_preference"] or details & SOFT_PREFERENCE_CODES:
                self.crm.transition_opportunity(
                    opportunity_id, "on_hold", "feedback_loop", event.get("user_id"), payload=payload
                )
            else:
                self.crm.transition_opportunity(
                    opportunity_id, "declined_by_me", "feedback_loop", event.get("user_id"), payload=payload
                )
                self.crm.repo.add_event(
                    opportunity_id=opportunity_id,
                    event_type="rejected_with_reasons",
                    event_source="feedback_loop",
                    actor=event.get("user_id"),
                    payload_json={**payload, "applies_to_company": classification["applies_to_company"]},
                )
        except Exception:
            # PRD 20.2: a feedback event can exist even if the opportunity
            # status update fails.
            logger.exception("feedback_loop: opportunity state update failed")

    def _post_thread(self, channel: str, thread_ts: str, message: str) -> None:
        if self.deliver is None:
            return
        try:
            self.deliver(message, channel, thread_ts)
        except Exception:
            logger.exception("feedback_loop: thread reply delivery failed")


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
