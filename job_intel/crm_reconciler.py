from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .crm_constants import TERMINAL_GUARDED_STATUSES
from .crm_service import CRMService
from .models import Vacancy
from .store import JobIntelStore, canonical_company_key


def _normalize_title(value: str | None) -> str | None:
    if not value:
        return None
    return " ".join(value.casefold().split())


def _feedback_to_reaction(feedback_type: str) -> str | None:
    return {
        "save_for_later": "eyes",
        "interesting": "+1",
        "not_interesting": "-1",
        "exceptional": "star",
        "applied": "rocket",
        "question": "question",
        "mailbox_with_mail": "mailbox_with_mail",
    }.get((feedback_type or "").strip().lower())


def _payload_as_dict(raw_payload: Any) -> dict[str, Any]:
    if isinstance(raw_payload, dict):
        return dict(raw_payload)
    if not raw_payload:
        return {}
    try:
        parsed = json.loads(raw_payload)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _looks_like_smoke_test(*values: Any) -> bool:
    needles = ("smoke", "manual-smoke-test", "manual smoke", "audit fixture")
    for value in values:
        text = str(value or "").strip().casefold()
        if not text:
            continue
        if any(needle in text for needle in needles):
            return True
    return False


@dataclass
class CRMReconciler:
    store: JobIntelStore

    def __post_init__(self) -> None:
        self.store.bootstrap()
        self.crm = CRMService.from_store(self.store)
        self._planned_opportunities: dict[int, dict[str, Any]] = {}
        self._mapped_messages: dict[tuple[str, str], int] = {}

    def run(
        self,
        *,
        days: int = 14,
        dry_run: bool = True,
        apply: bool = False,
        vacancy_id: int | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        if dry_run == apply:
            raise ValueError("Choose exactly one of --dry-run or --apply.")

        summary: dict[str, Any] = {
            "mode": "dry_run" if dry_run else "apply",
            "days": days,
            "vacancy_id": vacancy_id,
            "limit": limit,
            "counts": {
                "delivered_messages_without_mapping": 0,
                "opportunities_to_create": 0,
                "mappings_to_create": 0,
                "reaction_events_to_add": 0,
                "statuses_to_change": 0,
                "tasks_to_create": 0,
                "artifact_placeholders_to_create": 0,
            },
            "actions": [],
            "affected_vacancies": [],
            "excluded_records": [],
        }

        delivered_rows = self._iter_delivered_rows(days=days, vacancy_id=vacancy_id, limit=limit)
        for row in delivered_rows:
            plan = self._plan_mapping(row)
            if plan.get("excluded"):
                summary["excluded_records"].append(plan)
                continue
            summary["counts"]["delivered_messages_without_mapping"] += 1
            if plan["would_create_opportunity"]:
                summary["counts"]["opportunities_to_create"] += 1
            if plan["would_create_slack_message_map"]:
                summary["counts"]["mappings_to_create"] += 1
            if plan.get("would_set_status"):
                summary["counts"]["statuses_to_change"] += 1
            summary["actions"].append(plan)
            if not dry_run:
                self._apply_mapping(plan)

        feedback_rows = self._iter_feedback_rows(days=days, vacancy_id=vacancy_id, limit=limit)
        for row in feedback_rows:
            history_plan = self._plan_feedback_history(row)
            if history_plan.get("excluded"):
                summary["excluded_records"].append(history_plan)
                continue
            if history_plan.get("would_add_reaction_event"):
                summary["counts"]["reaction_events_to_add"] += 1
                summary["actions"].append(history_plan)
                if not dry_run:
                    self._apply_feedback_history(history_plan)

        state_rows = self._iter_active_state_rows(days=days, vacancy_id=vacancy_id, limit=limit)
        for row in state_rows:
            state_plan = self._plan_active_state(row)
            if state_plan.get("excluded"):
                summary["excluded_records"].append(state_plan)
                continue
            if state_plan.get("would_set_status"):
                summary["counts"]["statuses_to_change"] += 1
            if state_plan.get("would_create_task"):
                summary["counts"]["tasks_to_create"] += 1
            if state_plan.get("would_create_artifact_placeholder"):
                summary["counts"]["artifact_placeholders_to_create"] += 1
            summary["actions"].append(state_plan)
            if not dry_run:
                self._apply_active_state(state_plan)

        summary["affected_vacancies"] = self._affected_vacancies(summary["actions"])
        return summary

    def _iter_delivered_rows(self, *, days: int, vacancy_id: int | None, limit: int | None) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ["vsm.created_at >= datetime('now', ?)"]
        params.append(f"-{days} days")
        if vacancy_id is not None:
            where.append("vsm.vacancy_id = ?")
            params.append(vacancy_id)
        sql = f"""
            SELECT
                vsm.*,
                v.source,
                v.source_id,
                v.location,
                v.description,
                v.metadata_json,
                sm.id AS crm_map_id,
                sm.opportunity_id,
                o.status AS crm_status
            FROM vacancy_slack_messages vsm
            LEFT JOIN vacancies v ON v.id = vsm.vacancy_id
            LEFT JOIN slack_message_map sm
              ON sm.slack_channel_id = vsm.slack_channel
             AND sm.slack_message_ts = vsm.slack_message_ts
            LEFT JOIN opportunities o ON o.id = sm.opportunity_id
            WHERE {' AND '.join(where)}
            ORDER BY vsm.created_at DESC, vsm.id DESC
        """
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        with self.store.connect(read_only=True) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows if row["opportunity_id"] is None]

    def _iter_feedback_rows(self, *, days: int, vacancy_id: int | None, limit: int | None) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ["vf.created_at >= datetime('now', ?)"]
        params.append(f"-{days} days")
        if vacancy_id is not None:
            where.append("vf.vacancy_id = ?")
            params.append(vacancy_id)
        sql = f"""
            SELECT
                vf.*,
                v.company,
                v.title,
                v.url,
                v.source,
                sm.id AS crm_map_id,
                sm.opportunity_id
            FROM vacancy_feedback vf
            LEFT JOIN vacancies v ON v.id = vf.vacancy_id
            LEFT JOIN slack_message_map sm
              ON sm.slack_channel_id = vf.slack_channel
             AND sm.slack_message_ts = vf.slack_message_ts
            WHERE {' AND '.join(where)}
            ORDER BY vf.created_at ASC, vf.id ASC
        """
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        with self.store.connect(read_only=True) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def _iter_active_state_rows(self, *, days: int, vacancy_id: int | None, limit: int | None) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ["vfs.active = 1", "vfs.updated_at >= datetime('now', ?)"]
        params.append(f"-{days} days")
        if vacancy_id is not None:
            where.append("vfs.vacancy_id = ?")
            params.append(vacancy_id)
        sql = f"""
            SELECT
                vfs.*,
                v.company,
                v.title,
                v.url,
                v.source,
                sm.id AS crm_map_id,
                sm.opportunity_id
            FROM vacancy_feedback_state vfs
            LEFT JOIN vacancies v ON v.id = vfs.vacancy_id
            LEFT JOIN slack_message_map sm
              ON sm.slack_channel_id = vfs.slack_channel
             AND sm.slack_message_ts = vfs.slack_message_ts
            WHERE {' AND '.join(where)}
            ORDER BY vfs.updated_at DESC, vfs.id DESC
        """
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        with self.store.connect(read_only=True) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def _plan_mapping(self, row: dict[str, Any]) -> dict[str, Any]:
        plan: dict[str, Any] = {
            "action": "backfill_mapping",
            "vacancy_id": row["vacancy_id"],
            "company": row.get("company"),
            "title": row.get("title"),
            "slack_channel": row.get("slack_channel"),
            "slack_message_ts": row.get("slack_message_ts"),
            "legacy_message_id": row.get("id"),
        }
        if not row.get("slack_channel") or not row.get("slack_message_ts") or not row.get("message_type"):
            plan["excluded"] = True
            plan["reason"] = "missing_real_delivery_metadata"
            return plan
        if _looks_like_smoke_test(row.get("company"), row.get("title"), row.get("url"), row.get("canonical_url")):
            plan["excluded"] = True
            plan["reason"] = "smoke_or_manual_fixture"
            return plan
        opportunity = self._find_or_plan_opportunity(row["vacancy_id"])
        plan["opportunity_id"] = opportunity["id"]
        plan["would_create_opportunity"] = bool(opportunity["planned_create"] and not opportunity.get("reported_create"))
        if plan["would_create_opportunity"]:
            opportunity["reported_create"] = True
        plan["would_create_slack_message_map"] = True
        current_status = opportunity.get("status") or "discovered"
        plan["current_status"] = current_status
        plan["would_set_status"] = "notified" if current_status == "discovered" else None
        self._mapped_messages[(str(row["slack_channel"]), str(row["slack_message_ts"]))] = int(opportunity["id"])
        return plan

    def _apply_mapping(self, plan: dict[str, Any]) -> None:
        message = self.store.find_vacancy_message(
            slack_channel=str(plan["slack_channel"]),
            slack_message_ts=str(plan["slack_message_ts"]),
        )
        if not message:
            return
        vacancy_row = self.store.get_vacancy_by_id(int(plan["vacancy_id"]))
        if not vacancy_row:
            return
        vacancy = self._vacancy_from_row(vacancy_row)
        opportunity = self.crm.ensure_opportunity_for_vacancy(vacancy=vacancy, vacancy_id=int(plan["vacancy_id"]))
        self.crm.link_slack_message_to_opportunity(
            opportunity_id=int(opportunity["id"]),
            slack_channel_id=str(plan["slack_channel"]),
            slack_message_ts=str(plan["slack_message_ts"]),
            slack_thread_ts=str(plan["slack_message_ts"]),
        )
        self.crm.transition_for_delivery(int(opportunity["id"]))
        self._mapped_messages[(str(plan["slack_channel"]), str(plan["slack_message_ts"]))] = int(opportunity["id"])

    def _plan_feedback_history(self, row: dict[str, Any]) -> dict[str, Any]:
        reaction = _feedback_to_reaction(str(row.get("feedback_type") or ""))
        plan: dict[str, Any] = {
            "action": "preserve_feedback_history",
            "legacy_feedback_id": row["id"],
            "vacancy_id": row["vacancy_id"],
            "company": row.get("company"),
            "title": row.get("title"),
            "event_type": row.get("event_type"),
            "feedback_type": row.get("feedback_type"),
            "slack_channel": row.get("slack_channel"),
            "slack_message_ts": row.get("slack_message_ts"),
            "actor": row.get("user_id"),
            "reaction": reaction,
        }
        if not row.get("slack_channel") or not row.get("slack_message_ts") or not reaction:
            plan["excluded"] = True
            plan["reason"] = "missing_message_reference_or_reaction"
            return plan
        if _looks_like_smoke_test(row.get("company"), row.get("title"), row.get("url"), row.get("user_id")):
            plan["excluded"] = True
            plan["reason"] = "smoke_or_manual_fixture"
            return plan
        opportunity_id = self._resolve_opportunity_id(row)
        if opportunity_id is None:
            plan["excluded"] = True
            plan["reason"] = "missing_mapping_after_planning"
            return plan
        plan["opportunity_id"] = opportunity_id
        plan["would_add_reaction_event"] = not self._event_exists(opportunity_id, legacy_feedback_id=int(row["id"]))
        return plan

    def _apply_feedback_history(self, plan: dict[str, Any]) -> None:
        if not plan.get("would_add_reaction_event"):
            return
        self.crm.repo.add_event(
            opportunity_id=int(plan["opportunity_id"]),
            event_type=str(plan["event_type"]),
            event_source="crm_reconciler",
            actor=plan.get("actor"),
            payload_json={
                "reaction": plan.get("reaction"),
                "feedback_type": plan.get("feedback_type"),
                "legacy_feedback_id": plan.get("legacy_feedback_id"),
                "reconciled": True,
            },
            created_at=self._feedback_created_at(int(plan["legacy_feedback_id"])),
        )

    def _plan_active_state(self, row: dict[str, Any]) -> dict[str, Any]:
        reaction = _feedback_to_reaction(str(row.get("feedback_type") or ""))
        plan: dict[str, Any] = {
            "action": "apply_active_feedback_state",
            "legacy_feedback_state_id": row["id"],
            "vacancy_id": row["vacancy_id"],
            "company": row.get("company"),
            "title": row.get("title"),
            "feedback_type": row.get("feedback_type"),
            "actor": row.get("user_id"),
            "reaction": reaction,
            "slack_channel": row.get("slack_channel"),
            "slack_message_ts": row.get("slack_message_ts"),
        }
        if not reaction:
            plan["excluded"] = True
            plan["reason"] = "unsupported_feedback_type"
            return plan
        if _looks_like_smoke_test(row.get("company"), row.get("title"), row.get("url"), row.get("user_id")):
            plan["excluded"] = True
            plan["reason"] = "smoke_or_manual_fixture"
            return plan
        opportunity_id = self._resolve_opportunity_id(row)
        if opportunity_id is None:
            plan["excluded"] = True
            plan["reason"] = "missing_mapping_after_planning"
            return plan
        opportunity = self.crm.get_opportunity(opportunity_id) if self._opportunity_exists(opportunity_id) else self._find_or_plan_opportunity(int(row["vacancy_id"]))
        current_status = str(opportunity.get("status") or "discovered")
        plan["opportunity_id"] = int(opportunity_id)
        plan["current_status"] = current_status
        if current_status in TERMINAL_GUARDED_STATUSES:
            plan["excluded"] = True
            plan["reason"] = "terminal_status_guard"
            return plan
        next_status, task_type, create_placeholder = self._state_semantics(reaction=reaction, current_status=current_status)
        plan["would_set_status"] = next_status if next_status and next_status != current_status else None
        plan["would_create_task"] = bool(task_type and not self.crm.repo.find_open_task_by_type(opportunity_id, task_type))
        plan["task_type"] = task_type
        plan["would_create_artifact_placeholder"] = bool(
            create_placeholder and self.crm.repo.find_artifact(opportunity_id, "application_bundle_placeholder") is None
        )
        return plan

    def _apply_active_state(self, plan: dict[str, Any]) -> None:
        opportunity_id = int(plan["opportunity_id"])
        current_status = str(self.crm.get_opportunity(opportunity_id)["status"])
        reaction = str(plan["reaction"])
        if current_status in TERMINAL_GUARDED_STATUSES:
            return
        next_status, task_type, create_placeholder = self._state_semantics(reaction=reaction, current_status=current_status)
        payload = {
            "reaction": reaction,
            "legacy_feedback_state_id": plan["legacy_feedback_state_id"],
            "feedback_type": plan["feedback_type"],
            "reconciled": True,
        }
        if next_status:
            self.crm.transition_opportunity(
                opportunity_id=opportunity_id,
                new_status=next_status,
                source="crm_reconciler",
                actor=plan.get("actor"),
                reason="legacy_feedback_state",
                payload=payload,
            )
        if task_type:
            self.crm.create_or_update_open_task(opportunity_id=opportunity_id, task_type=task_type)
        if reaction == "+1" and not self._event_exists(opportunity_id, legacy_feedback_state_id=int(plan["legacy_feedback_state_id"]), event_type="evaluation_requested"):
            self.crm.repo.add_event(
                opportunity_id=opportunity_id,
                event_type="evaluation_requested",
                event_source="crm_reconciler",
                actor=plan.get("actor"),
                payload_json={**payload, "mode": "reconciled"},
            )
        if reaction == "star" and not self._event_exists(opportunity_id, legacy_feedback_state_id=int(plan["legacy_feedback_state_id"]), event_type="artifact_requested"):
            self.crm.repo.add_event(
                opportunity_id=opportunity_id,
                event_type="artifact_requested",
                event_source="crm_reconciler",
                actor=plan.get("actor"),
                payload_json={**payload, "mode": "reconciled"},
            )
        if reaction == "rocket" and current_status != "artifacts_ready":
            if not self._event_exists(opportunity_id, legacy_feedback_state_id=int(plan["legacy_feedback_state_id"]), event_type="priority_signal"):
                self.crm.repo.add_event(
                    opportunity_id=opportunity_id,
                    event_type="priority_signal",
                    event_source="crm_reconciler",
                    actor=plan.get("actor"),
                    payload_json=payload,
                )
        if create_placeholder:
            self.crm.ensure_placeholder_artifact(opportunity_id=opportunity_id)

    def _state_semantics(self, *, reaction: str, current_status: str) -> tuple[str | None, str | None, bool]:
        if reaction in {"eyes", "bookmark"}:
            return "watchlist", "review_opportunity", False
        if reaction in {"+1", "thumbsup", "thumbs_up"}:
            return "evaluation_requested", "review_opportunity", False
        if reaction in {"-1", "thumbsdown", "thumbs_down", "x"}:
            return "declined_by_me", None, False
        if reaction in {"star", "fire"}:
            return "artifact_requested", "generate_artifacts", True
        if reaction == "question":
            next_status = "evaluation_requested" if current_status in {"discovered", "notified", "watchlist", "evaluation_requested"} else "on_hold"
            return next_status, "review_opportunity", False
        if reaction == "mailbox_with_mail":
            return "outreach_planned", "send_outreach", False
        if reaction == "rocket":
            if current_status == "artifacts_ready":
                return "application_planned", None, False
            return None, "review_opportunity", False
        return None, None, False

    def _resolve_opportunity_id(self, row: dict[str, Any]) -> int | None:
        if row.get("opportunity_id") is not None:
            return int(row["opportunity_id"])
        slack_channel = row.get("slack_channel")
        slack_message_ts = row.get("slack_message_ts")
        if slack_channel and slack_message_ts:
            planned = self._mapped_messages.get((str(slack_channel), str(slack_message_ts)))
            if planned is not None:
                return int(planned)
            existing = self.crm.find_opportunity_by_slack_message(str(slack_channel), str(slack_message_ts))
            if existing:
                return int(existing["id"])
        planned_opportunity = self._planned_opportunities.get(int(row["vacancy_id"]))
        if planned_opportunity:
            return int(planned_opportunity["id"])
        with self.store.connect(read_only=True) as conn:
            found = conn.execute(
                "SELECT id FROM opportunities WHERE vacancy_id=? ORDER BY id LIMIT 1",
                (row["vacancy_id"],),
            ).fetchone()
        return int(found[0]) if found else None

    def _find_or_plan_opportunity(self, vacancy_id: int) -> dict[str, Any]:
        cached = self._planned_opportunities.get(vacancy_id)
        if cached:
            return cached
        vacancy_row = self.store.get_vacancy_by_id(vacancy_id)
        if not vacancy_row:
            planned = {"id": -vacancy_id, "status": "discovered", "planned_create": False}
            self._planned_opportunities[vacancy_id] = planned
            return planned
        vacancy = self._vacancy_from_row(vacancy_row)
        canonical_url = vacancy.url
        existing = self.crm.repo.find_opportunity_by_canonical_url(canonical_url)
        ats = str(vacancy.metadata.get("ats") or "").strip() or None
        ats_job_id = str(vacancy.metadata.get("ats_job_id") or "").strip() or None
        if not existing and ats and ats_job_id:
            existing = self.crm.repo.find_opportunity_by_ats_job(ats, ats_job_id)
        if not existing:
            matches = self.crm.repo.list_opportunities_by_signature(
                company_normalized=canonical_company_key(vacancy.company),
                title_normalized=_normalize_title(vacancy.title),
                location=vacancy.location,
            )
            existing = matches[0] if matches else None
        if existing:
            planned = {"id": int(existing["id"]), "status": existing["status"], "planned_create": False}
            self._planned_opportunities[vacancy_id] = planned
            return planned
        planned = {"id": -(100000 + vacancy_id), "status": "discovered", "planned_create": True}
        self._planned_opportunities[vacancy_id] = planned
        return planned

    def _vacancy_from_row(self, row: dict[str, Any]) -> Vacancy:
        metadata = _payload_as_dict(row.get("metadata_json"))
        metadata.setdefault("ats", row.get("source"))
        metadata.setdefault("ats_job_id", row.get("source_id"))
        return Vacancy(
            source=str(row["source"]),
            source_id=str(row["source_id"]),
            company=str(row["company"]),
            title=str(row["title"]),
            location=str(row["location"]),
            url=str(row["url"]),
            description=str(row.get("description") or ""),
            posted_at=row.get("posted_at"),
            scraped_at=row.get("scraped_at"),
            salary=row.get("salary"),
            company_url=row.get("company_url"),
            metadata=metadata,
        )

    def _event_exists(
        self,
        opportunity_id: int,
        *,
        legacy_feedback_id: int | None = None,
        legacy_feedback_state_id: int | None = None,
        event_type: str | None = None,
    ) -> bool:
        for event in self.crm.repo.list_events(opportunity_id):
            if event_type and event["event_type"] != event_type:
                continue
            payload = _payload_as_dict(event.get("payload_json"))
            if legacy_feedback_id is not None and payload.get("legacy_feedback_id") == legacy_feedback_id:
                return True
            if legacy_feedback_state_id is not None and payload.get("legacy_feedback_state_id") == legacy_feedback_state_id:
                return True
        return False

    def _opportunity_exists(self, opportunity_id: int) -> bool:
        if opportunity_id <= 0:
            return False
        return self.crm.repo.find_opportunity_by_id(opportunity_id) is not None

    def _feedback_created_at(self, legacy_feedback_id: int) -> str | None:
        with self.store.connect(read_only=True) as conn:
            row = conn.execute("SELECT created_at FROM vacancy_feedback WHERE id=?", (legacy_feedback_id,)).fetchone()
        return str(row[0]) if row else None

    def _affected_vacancies(self, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: dict[int, dict[str, Any]] = {}
        for action in actions:
            vacancy_id = action.get("vacancy_id")
            if vacancy_id is None or vacancy_id in seen:
                continue
            seen[int(vacancy_id)] = {
                "vacancy_id": int(vacancy_id),
                "company": action.get("company"),
                "title": action.get("title"),
            }
        return list(seen.values())
