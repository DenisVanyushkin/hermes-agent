from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from .crm_constants import DELIVERY_NOTIFIED_ALLOWED_FROM, TERMINAL_GUARDED_STATUSES, VALID_OPPORTUNITY_STATUSES
from .crm_repository import OpportunityRepository
from .dedup import canonical_job_url
from .models import Vacancy
from .store import JobIntelStore, canonical_company_key


def _normalize_title(value: str | None) -> str | None:
    if not value:
        return None
    return " ".join(value.casefold().split())


def _normalize_reaction(raw_reaction: str) -> str:
    return (raw_reaction or "").strip().lower()


def _looks_like_url(value: str) -> bool:
    parsed = urlparse((value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


@dataclass
class CRMService:
    repo: OpportunityRepository

    @classmethod
    def from_store(cls, store: JobIntelStore) -> "CRMService":
        return cls(repo=OpportunityRepository(store))

    def get_opportunity(self, opportunity_id: int) -> dict[str, Any]:
        row = self.repo.find_opportunity_by_id(opportunity_id)
        if not row:
            raise LookupError(f"Opportunity {opportunity_id} not found")
        return row

    def find_opportunity_by_slack_message(self, slack_channel_id: str, slack_message_ts: str) -> dict[str, Any] | None:
        return self.repo.find_opportunity_by_slack_message(slack_channel_id, slack_message_ts)

    def ensure_opportunity_for_vacancy(self, *, vacancy: Vacancy, vacancy_id: int) -> dict[str, Any]:
        canonical_url = canonical_job_url(vacancy.url, vacancy.source) or vacancy.url
        existing = self.repo.find_opportunity_by_canonical_url(canonical_url) if canonical_url else None
        ats = str(vacancy.metadata.get("ats") or "").strip() or None
        ats_job_id = str(vacancy.metadata.get("ats_job_id") or "").strip() or None
        if not existing and ats and ats_job_id:
            existing = self.repo.find_opportunity_by_ats_job(ats, ats_job_id)
        if not existing:
            existing_matches = self.repo.list_opportunities_by_signature(
                company_normalized=canonical_company_key(vacancy.company),
                title_normalized=_normalize_title(vacancy.title),
                location=vacancy.location,
            )
            existing = existing_matches[0] if existing_matches else None
        if existing:
            self.repo.update_opportunity(
                existing["id"],
                vacancy_id=vacancy_id,
                source_url=vacancy.url,
                canonical_url=canonical_url,
                last_seen_at=existing.get("last_seen_at"),
            )
            return self.get_opportunity(existing["id"])
        opportunity_id = self.repo.create_opportunity(
            vacancy_id=vacancy_id,
            company=vacancy.company,
            company_normalized=canonical_company_key(vacancy.company),
            title=vacancy.title,
            title_normalized=_normalize_title(vacancy.title),
            location=vacancy.location,
            source=vacancy.source,
            source_url=vacancy.url,
            canonical_url=canonical_url,
            ats=ats,
            ats_job_id=ats_job_id,
            description=vacancy.description,
            status="discovered",
        )
        self.repo.add_event(
            opportunity_id=opportunity_id,
            event_type="vacancy_discovered",
            event_source="job_intel",
            actor=None,
            payload_json={"vacancy_id": vacancy_id, "canonical_url": canonical_url},
        )
        return self.get_opportunity(opportunity_id)

    def link_slack_message_to_opportunity(
        self,
        *,
        opportunity_id: int,
        slack_channel_id: str,
        slack_message_ts: str,
        slack_thread_ts: str | None = None,
    ) -> None:
        self.repo.link_slack_message_to_opportunity(
            opportunity_id=opportunity_id,
            slack_channel_id=slack_channel_id,
            slack_message_ts=slack_message_ts,
            slack_thread_ts=slack_thread_ts,
        )
        self.repo.update_opportunity(
            opportunity_id,
            slack_channel_id=slack_channel_id,
            slack_message_ts=slack_message_ts,
            slack_thread_ts=slack_thread_ts,
        )

    def transition_opportunity(
        self,
        opportunity_id: int,
        new_status: str,
        source: str,
        actor: str | None,
        reason: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        current = self.get_opportunity(opportunity_id)
        if new_status not in VALID_OPPORTUNITY_STATUSES:
            raise ValueError(f"Unsupported opportunity status: {new_status}")
        if current["status"] == new_status:
            return False
        if current["status"] in TERMINAL_GUARDED_STATUSES and not self._manual_override_allowed(actor, reason, payload):
            return False
        self.repo.update_opportunity(opportunity_id, status=new_status)
        self.repo.add_event(
            opportunity_id=opportunity_id,
            event_type="manual_status_changed" if self._is_manual(source, actor, payload) else "status_changed",
            event_source=source,
            actor=actor,
            payload_json={
                "from_status": current["status"],
                "to_status": new_status,
                "reason": reason,
                **(payload or {}),
            },
        )
        return True

    def transition_for_delivery(self, opportunity_id: int) -> bool:
        current = self.get_opportunity(opportunity_id)
        if current["status"] not in DELIVERY_NOTIFIED_ALLOWED_FROM:
            return False
        return self.transition_opportunity(
            opportunity_id=opportunity_id,
            new_status="notified",
            source="slack_delivery",
            actor=None,
            reason="vacancy_card_sent",
            payload={"delivery": "slack"},
        )

    def handle_slack_reaction_event(
        self,
        *,
        slack_channel_id: str,
        slack_message_ts: str,
        reaction: str,
        event_type: str,
        actor: str | None,
        payload: dict[str, Any] | None = None,
    ) -> str:
        opportunity = self.find_opportunity_by_slack_message(slack_channel_id, slack_message_ts)
        if not opportunity:
            return "missing_mapping"
        opportunity_id = int(opportunity["id"])
        reaction_key = _normalize_reaction(reaction)
        self.repo.add_event(
            opportunity_id=opportunity_id,
            event_type=event_type,
            event_source="slack_reaction",
            actor=actor,
            payload_json={"reaction": reaction_key, **(payload or {})},
        )
        if event_type == "reaction_removed":
            return "ok"
        return self._apply_reaction_added(
            opportunity_id=opportunity_id,
            current_status=str(opportunity["status"]),
            reaction_key=reaction_key,
            actor=actor,
            payload=payload or {},
        )

    def create_or_update_open_task(
        self,
        *,
        opportunity_id: int,
        task_type: str,
        note: str | None = None,
        due_at: str | None = None,
    ) -> int:
        existing = self.repo.find_open_task_by_type(opportunity_id, task_type)
        if existing:
            self.repo.update_task(int(existing["id"]), note=note, due_at=due_at)
            return int(existing["id"])
        task_id = self.repo.create_task(
            opportunity_id=opportunity_id,
            task_type=task_type,
            note=note,
            due_at=due_at,
        )
        self.repo.add_event(
            opportunity_id=opportunity_id,
            event_type="task_created",
            event_source="crm_service",
            actor=None,
            payload_json={"task_type": task_type},
        )
        return task_id

    def ensure_placeholder_artifact(self, *, opportunity_id: int) -> int:
        existing = self.repo.find_artifact(opportunity_id, "application_bundle_placeholder")
        if existing:
            return int(existing["id"])
        return self.repo.create_artifact(
            opportunity_id=opportunity_id,
            artifact_type="application_bundle_placeholder",
            qa_status="stub",
        )

    def _apply_reaction_added(
        self,
        *,
        opportunity_id: int,
        current_status: str,
        reaction_key: str,
        actor: str | None,
        payload: dict[str, Any],
    ) -> str:
        if reaction_key in {"eyes", "bookmark"}:
            self.transition_opportunity(opportunity_id, "watchlist", "slack_reaction", actor, payload=payload)
            self.create_or_update_open_task(opportunity_id=opportunity_id, task_type="review_opportunity")
            return "ok"
        if reaction_key in {"+1", "thumbsup", "thumbs_up"}:
            if payload.get("evaluation_result"):
                self.transition_opportunity(opportunity_id, "evaluated", "slack_reaction", actor, payload=payload)
                self.repo.add_event(
                    opportunity_id=opportunity_id,
                    event_type="evaluation_completed",
                    event_source="slack_reaction",
                    actor=actor,
                    payload_json={"mode": "real", **payload},
                )
            else:
                self.transition_opportunity(opportunity_id, "evaluation_requested", "slack_reaction", actor, payload=payload)
                self.repo.add_event(
                    opportunity_id=opportunity_id,
                    event_type="evaluation_requested",
                    event_source="slack_reaction",
                    actor=actor,
                    payload_json={"mode": "stub", **payload},
                )
                self.create_or_update_open_task(opportunity_id=opportunity_id, task_type="review_opportunity")
            return "ok"
        if reaction_key in {"fire", "star"}:
            self.transition_opportunity(opportunity_id, "artifact_requested", "slack_reaction", actor, payload=payload)
            self.repo.add_event(
                opportunity_id=opportunity_id,
                event_type="artifact_requested",
                event_source="slack_reaction",
                actor=actor,
                payload_json={"mode": "stub", **payload},
            )
            self.create_or_update_open_task(opportunity_id=opportunity_id, task_type="generate_artifacts")
            self.ensure_placeholder_artifact(opportunity_id=opportunity_id)
            return "ok"
        if reaction_key == "rocket":
            if current_status == "artifacts_ready":
                self.transition_opportunity(opportunity_id, "application_planned", "slack_reaction", actor, payload=payload)
            else:
                self.repo.add_event(
                    opportunity_id=opportunity_id,
                    event_type="priority_signal",
                    event_source="slack_reaction",
                    actor=actor,
                    payload_json=payload,
                )
                self.create_or_update_open_task(opportunity_id=opportunity_id, task_type="review_opportunity")
            return "ok"
        if reaction_key in {"-1", "thumbsdown", "thumbs_down", "x"}:
            self.transition_opportunity(opportunity_id, "declined_by_me", "slack_reaction", actor, payload=payload)
            return "ok"
        if reaction_key == "question":
            next_status = "evaluation_requested" if current_status in {"discovered", "notified", "watchlist", "evaluation_requested"} else "on_hold"
            self.transition_opportunity(opportunity_id, next_status, "slack_reaction", actor, payload=payload)
            self.create_or_update_open_task(opportunity_id=opportunity_id, task_type="review_opportunity")
            return "ok"
        if reaction_key == "mailbox_with_mail":
            self.transition_opportunity(opportunity_id, "outreach_planned", "slack_reaction", actor, payload=payload)
            self.create_or_update_open_task(opportunity_id=opportunity_id, task_type="send_outreach")
            return "ok"
        return "ignored"

    def resolve_command_opportunity(
        self,
        *,
        slack_channel_id: str | None = None,
        slack_message_ts: str | None = None,
        search_text: str | None = None,
    ) -> dict[str, Any]:
        if slack_channel_id and slack_message_ts:
            opportunity = self.find_opportunity_by_slack_message(slack_channel_id, slack_message_ts)
            if opportunity:
                return {"status": "ok", "opportunity": opportunity}
        matches = self.repo.search_opportunities(search_text)
        if not matches:
            return {"status": "not_found"}
        if len(matches) > 1:
            return {"status": "needs_disambiguation", "matches": matches}
        return {"status": "ok", "opportunity": matches[0]}

    def add_note(self, *, opportunity_id: int, actor: str, text: str) -> None:
        self.repo.add_event(
            opportunity_id=opportunity_id,
            event_type="note_added",
            event_source="manual_command",
            actor=actor,
            payload_json={"text": text},
        )

    def list_due(self) -> list[dict[str, Any]]:
        return self.repo.list_due_tasks(datetime.now(timezone.utc).isoformat())

    def list_active(self) -> list[dict[str, Any]]:
        return self.repo.search_opportunities(None)

    def pipeline_summary(self) -> list[dict[str, Any]]:
        return self.repo.pipeline_counts()

    def create_followup_task(self, *, opportunity_id: int, spec: str) -> int:
        days = 0
        value = (spec or "").strip().lower()
        if value.endswith("d"):
            days = int(value[:-1] or "0")
        due_at = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        return self.create_or_update_open_task(
            opportunity_id=opportunity_id,
            task_type="follow_up_recruiter",
            due_at=due_at,
        )

    def ingest_manual_job_url(
        self,
        *,
        url: str,
        actor: str = "Denis",
        source: str = "manual_command",
        force_re_evaluate: bool = False,
    ) -> dict[str, Any]:
        raw_url = (url or "").strip()
        if not _looks_like_url(raw_url):
            return {
                "status": "invalid_url",
                "opportunity_id": None,
                "created": False,
                "deduped": False,
                "canonical_url": None,
                "ats": None,
                "ats_job_id": None,
                "crm_status": None,
                "extraction_status": "not_attempted",
                "message": "URL is invalid.",
            }

        canonical_url = canonical_job_url(raw_url, "manual_url") or raw_url.rstrip("/")
        ats, ats_job_id = self._infer_ats_and_job_id(raw_url)
        existing = self.repo.find_opportunity_by_canonical_url(canonical_url)
        if not existing and ats and ats_job_id:
            existing = self.repo.find_opportunity_by_ats_job(ats, ats_job_id)

        created = False
        deduped = False
        if existing:
            opportunity_id = int(existing["id"])
            deduped = True
            self.repo.update_opportunity(
                opportunity_id,
                source_url=raw_url,
                canonical_url=canonical_url,
                ats=ats,
                ats_job_id=ats_job_id,
                last_seen_at=datetime.now(timezone.utc).isoformat(),
            )
        else:
            opportunity_id = self.repo.create_opportunity(
                source="manual_url",
                source_url=raw_url,
                canonical_url=canonical_url,
                ats=ats,
                ats_job_id=ats_job_id,
                status="discovered",
            )
            created = True

        self.repo.add_event(
            opportunity_id=opportunity_id,
            event_type="manual_url_submitted",
            event_source=source,
            actor=actor,
            payload_json={"url": raw_url, "canonical_url": canonical_url, "ats": ats, "ats_job_id": ats_job_id},
        )

        current = self.get_opportunity(opportunity_id)
        if created:
            self.transition_opportunity(
                opportunity_id=opportunity_id,
                new_status="evaluation_requested",
                source=source,
                actor=actor,
                reason="manual_url_submission",
                payload={"command": "eval", "url": raw_url},
            )
        elif force_re_evaluate or current["status"] in {"discovered", "notified", "watchlist", "evaluation_requested"}:
            self.transition_opportunity(
                opportunity_id=opportunity_id,
                new_status="evaluation_requested",
                source=source,
                actor=actor,
                reason="manual_url_submission",
                payload={"command": "eval", "url": raw_url},
            )

        self.create_or_update_open_task(opportunity_id=opportunity_id, task_type="review_opportunity")

        return {
            "status": "ok",
            "opportunity_id": opportunity_id,
            "created": created,
            "deduped": deduped,
            "canonical_url": canonical_url,
            "ats": ats,
            "ats_job_id": ats_job_id,
            "crm_status": self.get_opportunity(opportunity_id)["status"],
            "extraction_status": "not_attempted",
            "message": "URL saved. Paste vacancy text later for better evaluation.",
        }

    def _is_manual(self, source: str, actor: str | None, payload: dict[str, Any] | None) -> bool:
        if source == "manual_command":
            return True
        if actor == "Denis":
            return True
        return str((payload or {}).get("command") or "").strip().lower() == "reopen"

    def _manual_override_allowed(self, actor: str | None, reason: str | None, payload: dict[str, Any] | None) -> bool:
        if actor == "Denis":
            return True
        if (reason or "").strip().lower() == "reopen":
            return True
        return str((payload or {}).get("command") or "").strip().lower() == "reopen"

    def _infer_ats_and_job_id(self, url: str) -> tuple[str | None, str | None]:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        path = (parsed.path or "").strip("/")
        parts = [part for part in path.split("/") if part]
        if "ashbyhq.com" in host:
            return "ashby", parts[-1] if parts else None
        if "greenhouse.io" in host:
            return "greenhouse", parts[-1] if parts else None
        if "lever.co" in host:
            return "lever", parts[-1] if parts else None
        if "smartrecruiters.com" in host:
            return "smartrecruiters", parts[-1] if parts else None
        if "workdayjobs.com" in host or "myworkdayjobs.com" in host:
            return "workday", parts[-1] if parts else None
        return "generic", None
