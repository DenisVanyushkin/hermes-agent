from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse
import logging
import re
import sqlite3

from .dedup import canonical_vacancy_key
from .models import Evaluation, Vacancy
from .store import JobIntelStore

ROLE_BUCKETS = ("vp_product", "head_product", "director_product", "gm_product", "cpo", "other")
GEO_BUCKETS = (
    "USA",
    "UK",
    "Germany",
    "Netherlands",
    "Singapore",
    "UAE",
    "Australia",
    "Japan",
    "South Korea",
    "Hong Kong",
    "India",
    "Other",
)
INDUSTRY_BUCKETS = ("AI", "SaaS", "Fintech", "Marketplace", "Consumer", "Telecom", "Infrastructure", "Other")
REJECTION_REASONS = (
    "wrong_geography",
    "wrong_industry",
    "low_seniority",
    "missing_executive_responsibility",
    "pnl_unknown",
    "missing_product_ownership",
    "low_company_score",
    "low_hiring_likelihood",
    "low_confidence",
    "salary_unknown",
    "company_score_unknown",
    "hiring_likelihood_unknown",
    "location_unknown",
    "duplicate",
    "salary_too_low",
    "company_stage_mismatch",
    "role_too_execution_only",
    "role_too_internal_delivery",
    "blocked_geography",
    "sanctioned_or_high_risk_market",
    "language_requirement_mismatch",
    "onsite_requirement_mismatch",
    "weak_company_signal",
    "insufficient_data",
)

SOURCE_ALIASES = {
    "linkedin": "LinkedIn",
    "headhunter": "HeadHunter",
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "ashby": "Ashby",
    "teamtailor": "Teamtailor",
    "smartrecruiters": "SmartRecruiters",
    "personio": "Personio",
    "recruitee": "Recruitee",
}
_REASON_TYPES: dict[str, tuple[str, str]] = {
    # blocker — high
    "non_product_role":             ("blocker", "high"),
    "low_seniority":                ("blocker", "high"),
    "blocked_geography":            ("blocker", "high"),
    "onsite_requirement_mismatch":  ("blocker", "high"),
    "duplicate":                    ("blocker", "high"),
    "sales_role":                   ("blocker", "high"),
    # blocker — medium
    "marketing_role":               ("blocker", "medium"),
    "business_development_role":    ("blocker", "medium"),
    "analyst_role":                 ("blocker", "medium"),
    "low_company_tier":             ("blocker", "medium"),
    # unknown — low
    "salary_unknown":               ("unknown", "low"),
    "pnl_unknown":                  ("unknown", "low"),
    "company_score_unknown":        ("unknown", "low"),
    "hiring_likelihood_unknown":    ("unknown", "low"),
    "location_unknown":             ("unknown", "low"),
    # warning — low
    "weak_company_signal":          ("warning", "low"),
    "low_confidence":               ("warning", "low"),
    "unclear_scope":                ("warning", "low"),
    "missing_product_ownership_evidence": ("warning", "low"),
}


def classify_rejection_reason(reason: str) -> tuple[str, str]:
    """Returns (reason_type, severity). Unknown reasons default to ('unknown', 'low')."""
    return _REASON_TYPES.get(reason, ("unknown", "low"))

logger = logging.getLogger(__name__)
_COUNTRY_HINTS: list[tuple[str, str]] = [
    ("usa", "USA"),
    ("united states", "USA"),
    ("new york", "USA"),
    ("san francisco", "USA"),
    ("sf bay", "USA"),
    ("seattle", "USA"),
    ("london", "UK"),
    ("manchester", "UK"),
    ("edinburgh", "UK"),
    ("berlin", "Germany"),
    ("munich", "Germany"),
    ("hamburg", "Germany"),
    ("amsterdam", "Netherlands"),
    ("rotterdam", "Netherlands"),
    ("singapore", "Singapore"),
    ("dubai", "UAE"),
    ("abu dhabi", "UAE"),
    ("sydney", "Australia"),
    ("melbourne", "Australia"),
    ("tokyo", "Japan"),
    ("osaka", "Japan"),
    ("seoul", "South Korea"),
    ("busan", "South Korea"),
    ("hong kong", "Hong Kong"),
    ("bangalore", "India"),
    ("bengaluru", "India"),
    ("mumbai", "India"),
    ("delhi", "India"),
]

_APAC = {"Singapore", "Australia", "Japan", "South Korea", "Hong Kong", "India"}
_EUROPE = {"UK", "Germany", "Netherlands"}
_GCC = {"UAE"}


def canonical_source_name(source: str) -> str:
    key = (source or "unknown").strip().lower().replace("_", " ")
    return SOURCE_ALIASES.get(key, (source or "unknown").strip().title())


def _text(vacancy: Vacancy) -> str:
    return " ".join(
        [
            vacancy.company or "",
            vacancy.title or "",
            vacancy.location or "",
            vacancy.description or "",
            str(vacancy.metadata or {}),
        ]
    ).lower()


def score_band_for(score: int) -> str:
    if score <= 39:
        return "0_39"
    if score <= 59:
        return "40_59"
    if score <= 74:
        return "60_74"
    if score <= 89:
        return "75_89"
    return "90_100"


def role_bucket_for(vacancy: Vacancy, classification: dict[str, Any] | None = None) -> str:
    classification = classification or {}
    cls = str(classification.get("classification") or "").lower()
    title = (vacancy.title or "").lower()
    if cls == "chief_product_officer" or "chief product officer" in title or " cpo" in title:
        return "cpo"
    if cls == "vp_product" or "vp" in title or "vice president" in title:
        return "vp_product"
    if cls == "director_product" or "director" in title:
        return "director_product"
    if cls == "head_product" or "head of" in title:
        return "head_product"
    if "general manager" in title or re.search(r"\bgm\b", title):
        return "gm_product"
    if cls in {"product_strategy_lead", "growth_product_lead", "monetization_product_lead", "platform_ecosystem_product_lead", "consumer_product_lead", "product_lead", "product_strategy_and_growth"}:
        return "other"
    return "other"


def geo_bucket_for(location: str) -> str:
    text = (location or "").strip().lower()
    if not text:
        return "Other"
    if text == "remote":
        return "Remote"
    for needle, bucket in _COUNTRY_HINTS:
        if needle in text:
            return bucket
    return "Other"


def industry_bucket_for(vacancy: Vacancy) -> str:
    text = _text(vacancy)
    keyword_groups: list[tuple[str, tuple[str, ...]]] = [
        ("AI", (" ai ", "artificial intelligence", "machine learning", "llm", "genai", "generative ai")),
        ("SaaS", ("saas", "software as a service", "b2b software", "subscription software")),
        ("Fintech", ("fintech", "payments", "banking", "lending", "wallet", "wealthtech")),
        ("Marketplace", ("marketplace", "two-sided", "platform marketplace")),
        ("Consumer", ("consumer", "b2c", "direct-to-consumer", "d2c", "mobile app")),
        ("Telecom", ("telecom", "telco", "network", "carrier", "5g")),
        ("Infrastructure", ("infrastructure", "platform engineering", "cloud", "devops", "platform")),
    ]
    for bucket, keywords in keyword_groups:
        if any(keyword in text for keyword in keywords):
            return bucket
    return "Other"


def _clean_reason_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip().lower()


def rejection_reasons_for(
    vacancy: Vacancy,
    evaluation: Evaluation,
    classification: dict[str, Any],
    *,
    duplicate: bool,
) -> list[str]:
    if evaluation.recommendation not in {"near_miss", "reject"} and not duplicate:
        return []

    text = _text(vacancy)
    title = (vacancy.title or "").lower()
    reasons: list[str] = []

    if duplicate:
        reasons.append("duplicate")

    blocked_geo_terms = ["russia", "belarus", "iran", "north korea", "syria", "crimea", "donetsk", "luhansk"]
    sanctioned_terms = ["russia", "belarus", "iran", "north korea", "syria"]
    if any(term in text for term in blocked_geo_terms):
        reasons.append("blocked_geography")
    if any(term in text for term in sanctioned_terms):
        reasons.append("sanctioned_or_high_risk_market")
    if any(term in text for term in ["russia", "belarus", "iran", "north korea", "syria", "remote only in"]):
        reasons.append("wrong_geography")

    negative_role_terms = ["delivery", "project manager", "scrum master", "implementation", "support operations", "internal tooling", "internal tools"]
    if any(term in text for term in negative_role_terms):
        reasons.append("role_too_internal_delivery")
        reasons.append("role_too_execution_only")

    if "product owner" in title or "product owner" in text:
        reasons.append("missing_product_ownership")
    if not classification.get("executive_detected"):
        reasons.append("missing_executive_responsibility")
        reasons.append("low_seniority")

    if "p&l" not in text and "profit and loss" not in text and evaluation.score < 75:
        reasons.append("pnl_unknown")
    if not any(term in text for term in ["product strategy", "product ownership", "growth", "monetization", "roadmap"]):
        reasons.append("missing_product_ownership")

    salary_text = (vacancy.salary or "").lower()
    if salary_text and any(term in salary_text for term in ["low", "below", "junior", "entry", "competitive"]):
        reasons.append("salary_too_low")
    if not salary_text:
        reasons.append("salary_unknown")

    if "series a" in text or "series b" in text or "seed" in text or "startup" in text:
        if evaluation.score < 60:
            reasons.append("company_stage_mismatch")

    if evaluation.score < 55:
        reasons.append("company_score_unknown")
    if evaluation.score < 45:
        reasons.append("hiring_likelihood_unknown")
    if evaluation.score < 35:
        reasons.append("low_confidence")

    if not vacancy.description or len(vacancy.description.strip()) < 120:
        reasons.append("insufficient_data")
    if "onsite" in text or "on-site" in text or "hybrid" in text:
        reasons.append("onsite_requirement_mismatch")
    if any(term in text for term in ["english required", "native", "language requirement", "fluent in"]) and evaluation.score < 70:
        reasons.append("language_requirement_mismatch")
    if not any(term in text for term in ["product", "roadmap", "p&l", "monetization", "growth"]):
        reasons.append("weak_company_signal")
    if not vacancy.location or (vacancy.location or "").strip().lower() in {"unknown", ""}:
        reasons.append("location_unknown")

    # Preserve normalized order, dedupe while keeping first occurrence.
    ordered: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        if reason in REJECTION_REASONS and reason not in seen:
            ordered.append(reason)
            seen.add(reason)
    return ordered


@dataclass(frozen=True)
class ObservabilityRow:
    run_id: int
    vacancy_key: str
    source: str
    role_bucket: str
    geo_bucket: str
    industry_bucket: str
    executive_detected: bool
    accepted: bool
    notified: bool
    score: int
    score_band: str
    confidence: float | None
    is_duplicate: bool
    created_at: str


class JobIntelObservabilityExporter:
    def __init__(self, store: JobIntelStore):
        self.store = store

    def _fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.store.connect(read_only=True) as conn:
            try:
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "no such table" in message or "no such column" in message:
                    logger.warning("job-intel exporter skipping schema-mismatched query: %s", message)
                    return []
                raise
        return [dict(row) for row in rows]

    def _fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.store.connect(read_only=True) as conn:
            try:
                row = conn.execute(sql, params).fetchone()
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "no such table" in message or "no such column" in message:
                    logger.warning("job-intel exporter skipping schema-mismatched query: %s", message)
                    return None
                raise
        return dict(row) if row else None

    def _window_bounds(self, days: int) -> tuple[str, str]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        return start.isoformat(), end.isoformat()

    def _count(self, table: str, days: int, *, where: str = "1=1", params: tuple[Any, ...] = ()) -> int:
        start, end = self._window_bounds(days)
        sql = f"""
            SELECT COUNT(*) AS c
            FROM {table} t
            JOIN runs r ON r.id = t.run_id
            WHERE r.run_type = 'production'
              AND datetime(t.created_at) >= datetime(?)
              AND datetime(t.created_at) < datetime(?)
              AND {where}
        """
        row = self._fetchone(sql, (start, end, *params))
        return int((row or {}).get("c") or 0)

    def _window_scores(self, days: int, *, where: str = "1=1") -> list[int]:
        start, end = self._window_bounds(days)
        sql = f"""
            SELECT t.score AS score
            FROM vacancy_observability t
            JOIN runs r ON r.id = t.run_id
            WHERE r.run_type = 'production'
              AND datetime(t.created_at) >= datetime(?)
              AND datetime(t.created_at) < datetime(?)
              AND {where}
            ORDER BY t.score ASC
        """
        return [int(row["score"] or 0) for row in self._fetchall(sql, (start, end))]

    @staticmethod
    def _pctl(values: list[int], pct: float) -> int | None:
        if not values:
            return None
        if len(values) == 1:
            return int(values[0])
        xs = sorted(values)
        idx = int(round((pct / 100.0) * (len(xs) - 1)))
        idx = max(0, min(len(xs) - 1, idx))
        return int(xs[idx])

    @staticmethod
    def _rate(numerator: int, denominator: int) -> float | None:
        return (numerator / denominator) if denominator else None

    def latest_run_health(self) -> dict[str, float]:
        result: dict[str, float] = {}
        for mode in ("daily", "weekly_kpi"):
            row = self._fetchone(
                """
                SELECT status
                FROM runs
                WHERE mode = ?
                  AND run_type = 'production'
                ORDER BY id DESC
                LIMIT 1
                """,
                (mode,),
            ) or {}
            result[mode] = 1.0 if row.get("status") == "ok" else 0.0
        return result

    def latest_successful_daily_run_timestamp(self) -> int | None:
        row = self._fetchone(
            """
            SELECT started_at, finished_at
            FROM runs
            WHERE mode = 'daily'
              AND run_type = 'production'
              AND status = 'ok'
            ORDER BY id DESC
            LIMIT 1
            """
        ) or {}
        stamp = row.get("finished_at") or row.get("started_at")
        if not stamp:
            return None
        try:
            return int(datetime.fromisoformat(str(stamp).replace('Z', '+00:00')).timestamp())
        except ValueError:
            return None

    def run_success_rate(self, days: int, mode: str = 'daily') -> float | None:
        start, end = self._window_bounds(days)
        row = self._fetchone(
            """
            SELECT
                COUNT(*) AS total_runs,
                SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_runs
            FROM runs
            WHERE run_type = 'production'
              AND mode = ?
              AND datetime(started_at) >= datetime(?)
              AND datetime(started_at) < datetime(?)
            """,
            (mode, start, end),
        ) or {}
        return self._rate(int(row.get('ok_runs') or 0), int(row.get('total_runs') or 0))

    def latest_source_statuses(self) -> list[dict[str, Any]]:
        sql = """
            SELECT k1.source, k1.source_status, k1.run_id, r.started_at
            FROM source_kpi_run k1
            JOIN runs r ON r.id = k1.run_id
            JOIN (
                SELECT source, MAX(run_id) AS max_run_id
                FROM source_kpi_run
                GROUP BY source
            ) latest ON latest.source = k1.source AND latest.max_run_id = k1.run_id
            ORDER BY k1.source ASC
        """
        return self._fetchall(sql)

    def source_windows(self, days: int) -> list[dict[str, Any]]:
        start, end = self._window_bounds(days)
        sql = """
            SELECT
                t.source AS source,
                SUM(CASE WHEN t.executive_detected = 1 THEN 1 ELSE 0 END) AS executive_detected,
                SUM(CASE WHEN t.accepted = 1 THEN 1 ELSE 0 END) AS accepted,
                SUM(CASE WHEN t.notified = 1 THEN 1 ELSE 0 END) AS notified,
                COUNT(*) AS found
            FROM vacancy_observability t
            JOIN runs r ON r.id = t.run_id
            WHERE r.run_type = 'production'
              AND datetime(t.created_at) >= datetime(?)
              AND datetime(t.created_at) < datetime(?)
            GROUP BY t.source
            ORDER BY found DESC, t.source ASC
        """
        return self._fetchall(sql, (start, end))

    def source_issue_windows(self, days: int) -> list[dict[str, Any]]:
        start, end = self._window_bounds(days)
        sql = """
            SELECT
                k.source AS source,
                SUM(COALESCE(k.login_walls, 0)) AS login_walls,
                SUM(COALESCE(k.auth_redirects, 0)) AS auth_redirects,
                SUM(COALESCE(k.anti_bot_events, 0)) AS anti_bot_events,
                SUM(COALESCE(k.extraction_failures, 0)) AS extraction_failures
            FROM source_kpi_run k
            JOIN runs r ON r.id = k.run_id
            WHERE r.run_type = 'production'
              AND datetime(r.started_at) >= datetime(?)
              AND datetime(r.started_at) < datetime(?)
            GROUP BY k.source
            ORDER BY k.source ASC
        """
        return self._fetchall(sql, (start, end))

    def rejections_by_reason(self, days: int) -> list[dict[str, Any]]:
        start, end = self._window_bounds(days)
        sql = """
            SELECT rejection_reason AS reason, COUNT(*) AS count
            FROM vacancy_rejection_events t
            JOIN runs r ON r.id = t.run_id
            WHERE r.run_type = 'production'
              AND datetime(t.created_at) >= datetime(?)
              AND datetime(t.created_at) < datetime(?)
            GROUP BY rejection_reason
            ORDER BY count DESC, reason ASC
        """
        return self._fetchall(sql, (start, end))

    def rejections_by_source_reason(self, days: int) -> list[dict[str, Any]]:
        start, end = self._window_bounds(days)
        sql = """
            SELECT source, rejection_reason AS reason, COUNT(*) AS count
            FROM vacancy_rejection_events t
            JOIN runs r ON r.id = t.run_id
            WHERE r.run_type = 'production'
              AND datetime(t.created_at) >= datetime(?)
              AND datetime(t.created_at) < datetime(?)
            GROUP BY source, rejection_reason
            ORDER BY source ASC, count DESC
        """
        return self._fetchall(sql, (start, end))

    def band_counts(self, days: int, *, accepted: int | None = None, rejected: int | None = None) -> dict[str, int]:
        start, end = self._window_bounds(days)
        clauses = ["r.run_type = 'production'", "datetime(t.created_at) >= datetime(?)", "datetime(t.created_at) < datetime(?)"]
        params: list[Any] = [start, end]
        if accepted is not None:
            clauses.append("t.accepted = ?")
            params.append(1 if accepted else 0)
        if rejected is not None:
            clauses.append("t.accepted = ?")
            params.append(0 if rejected else 1)
        sql = f"""
            SELECT t.score_band AS score_band, COUNT(*) AS count
            FROM vacancy_observability t
            JOIN runs r ON r.id = t.run_id
            WHERE {' AND '.join(clauses)}
            GROUP BY t.score_band
        """
        rows = self._fetchall(sql, tuple(params))
        return {str(row["score_band"]): int(row["count"] or 0) for row in rows}

    def geography_counts(self, days: int) -> dict[str, int]:
        start, end = self._window_bounds(days)
        sql = """
            SELECT geo_bucket, COUNT(*) AS count
            FROM vacancy_observability t
            JOIN runs r ON r.id = t.run_id
            WHERE r.run_type = 'production'
              AND datetime(t.created_at) >= datetime(?)
              AND datetime(t.created_at) < datetime(?)
            GROUP BY geo_bucket
        """
        rows = self._fetchall(sql, (start, end))
        return {str(row["geo_bucket"]): int(row["count"] or 0) for row in rows}

    def industry_counts(self, days: int) -> dict[str, int]:
        start, end = self._window_bounds(days)
        sql = """
            SELECT industry_bucket, COUNT(*) AS count
            FROM vacancy_observability t
            JOIN runs r ON r.id = t.run_id
            WHERE r.run_type = 'production'
              AND datetime(t.created_at) >= datetime(?)
              AND datetime(t.created_at) < datetime(?)
            GROUP BY industry_bucket
        """
        rows = self._fetchall(sql, (start, end))
        return {str(row["industry_bucket"]): int(row["count"] or 0) for row in rows}

    def role_counts(self, days: int) -> dict[str, int]:
        start, end = self._window_bounds(days)
        sql = """
            SELECT role_bucket, COUNT(*) AS count
            FROM vacancy_observability t
            JOIN runs r ON r.id = t.run_id
            WHERE r.run_type = 'production'
              AND datetime(t.created_at) >= datetime(?)
              AND datetime(t.created_at) < datetime(?)
            GROUP BY role_bucket
        """
        rows = self._fetchall(sql, (start, end))
        return {str(row["role_bucket"]): int(row["count"] or 0) for row in rows}

    def company_counts(self, days: int) -> dict[str, int]:
        start, end = self._window_bounds(days)
        sql = """
            SELECT
                COUNT(*) AS discovered,
                SUM(CASE WHEN COALESCE(target_category, '') IN ('tier1', 'tier2') THEN 1 ELSE 0 END) AS prioritized,
                SUM(CASE WHEN COALESCE(target_category, '') = 'tier1' THEN 1 ELSE 0 END) AS tier1,
                SUM(CASE WHEN COALESCE(target_category, '') = 'tier2' THEN 1 ELSE 0 END) AS tier2
            FROM company_intelligence
            WHERE datetime(updated_at) >= datetime(?)
              AND datetime(updated_at) < datetime(?)
        """
        row = self._fetchone(sql, (start, end)) or {}
        return {k: int(row.get(k) or 0) for k in ["discovered", "prioritized", "tier1", "tier2"]}

    def source_health_metrics(self) -> list[dict[str, Any]]:
        rows = []
        for row in self.latest_source_statuses():
            status = str(row.get("source_status") or "unknown")
            healthy = 1 if status in {"ok", "empty", "skipped"} else 0
            rows.append({"source": row.get("source") or "unknown", "status": status, "healthy": healthy})
        return rows

    def render_text(self) -> str:
        lines: list[str] = []

        def emit(name: str, value: float | int | None, labels: dict[str, str] | None = None, help_text: str | None = None, metric_type: str = "gauge") -> None:
            if help_text:
                lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {metric_type}")
            if value is None:
                return
            if labels:
                rendered = ",".join(f'{k}="{_escape_label(v)}"' for k, v in labels.items())
                lines.append(f"{name}{{{rendered}}} {value}")
            else:
                lines.append(f"{name} {value}")

        # Run health
        latest = self.latest_run_health()
        for mode in ("daily", "weekly_kpi"):
            emit("job_intel_run_success", latest.get(mode, 0.0), {"mode": mode}, "Latest run success flag", "gauge")
        latest_run = self._fetchone(
            """
            SELECT started_at, finished_at, status
            FROM runs
            WHERE mode = 'daily'
              AND run_type = 'production'
            ORDER BY id DESC
            LIMIT 1
            """
        ) or {}
        if latest_run.get("started_at"):
            emit(
                "job_intel_run_last_success_timestamp",
                self.latest_successful_daily_run_timestamp(),
                {"mode": "daily"},
                "Timestamp of the latest successful daily run",
                "gauge",
            )
        for suffix, days in (("24h", 1), ("7d", 7), ("30d", 30)):
            emit(
                f"job_intel_daily_run_success_rate_{suffix}",
                self.run_success_rate(days, mode="daily"),
                help_text=f"Daily run success rate over the last {suffix}",
            )
        for days, suffix in ((1, "24h"), (7, "7d"), (30, "30d")):
            emit(f"job_intel_vacancies_found_{suffix}", self._count("vacancy_observability", days), help_text=f"Vacancies found in the last {suffix}")
            emit(f"job_intel_executive_detected_{suffix}", self._count("vacancy_observability", days, where="t.executive_detected = 1"), help_text=f"Executive candidates detected in the last {suffix}")
            emit(f"job_intel_vacancies_accepted_{suffix}", self._count("vacancy_observability", days, where="t.accepted = 1"), help_text=f"Accepted vacancies in the last {suffix}")
            emit(f"job_intel_notifications_sent_{suffix}", self._count("vacancy_observability", days, where="t.notified = 1"), help_text=f"Notifications sent in the last {suffix}")
            company_window = self.company_counts(days)
            emit(f"job_intel_companies_discovered_{suffix}", company_window["discovered"], help_text=f"Companies discovered in the last {suffix}")
            emit(f"job_intel_companies_prioritized_{suffix}", company_window["prioritized"], help_text=f"Companies prioritized in the last {suffix}")

        # Source effectiveness and source health.
        for row in self.source_health_metrics():
            source = canonical_source_name(str(row["source"]))
            emit("job_intel_source_healthy", int(row["healthy"]), {"source": source}, "Whether the source is healthy", "gauge")
            emit("job_intel_source_last_run_status_info", 1, {"source": source, "status": str(row["status"])}, "Latest source status info", "gauge")

        for row in self.source_windows(7):
            source = canonical_source_name(str(row["source"]))
            emit("job_intel_source_found_7d", int(row["found"] or 0), {"source": source}, "Found opportunities by source in the last 7d")
            emit("job_intel_source_executive_7d", int(row["executive_detected"] or 0), {"source": source}, "Executive candidates by source in the last 7d")
            emit("job_intel_source_accepted_7d", int(row["accepted"] or 0), {"source": source}, "Accepted opportunities by source in the last 7d")
            emit("job_intel_source_notified_7d", int(row["notified"] or 0), {"source": source}, "Notified opportunities by source in the last 7d")
        for row in self.source_issue_windows(7):
            source = canonical_source_name(str(row["source"]))
            emit("job_intel_source_login_walls_7d", int(row["login_walls"] or 0), {"source": source}, "Login walls by source in the last 7d")
            emit("job_intel_source_auth_redirects_7d", int(row["auth_redirects"] or 0), {"source": source}, "Auth redirects by source in the last 7d")
            emit("job_intel_source_anti_bot_events_7d", int(row["anti_bot_events"] or 0), {"source": source}, "Anti-bot events by source in the last 7d")
            emit("job_intel_source_extraction_failures_7d", int(row["extraction_failures"] or 0), {"source": source}, "Extraction failures by source in the last 7d")

        # Score quality.
        for days, suffix in ((1, "24h"), (7, "7d"), (30, "30d")):
            for scope, where in (("all", "1=1"), ("accepted", "t.accepted = 1"), ("rejected", "t.accepted = 0")):
                scores = self._window_scores(days, where=where)
                avg = (sum(scores) / len(scores)) if scores else None
                p50 = self._pctl(scores, 50)
                p90 = self._pctl(scores, 90)
                top = max(scores) if scores else None
                emit(f"job_intel_vacancy_score_avg_{suffix}", avg, {"scope": scope}, f"Average vacancy score over the last {suffix}")
                emit(f"job_intel_vacancy_score_p50_{suffix}", p50, {"scope": scope}, f"P50 vacancy score over the last {suffix}")
                emit(f"job_intel_vacancy_score_p90_{suffix}", p90, {"scope": scope}, f"P90 vacancy score over the last {suffix}")
                emit(f"job_intel_vacancy_score_top_{suffix}", top, {"scope": scope}, f"Top vacancy score over the last {suffix}")
            band_counts = Counter()
            for row in self._fetchall(
                """
                SELECT score_band, COUNT(*) AS count
                FROM vacancy_rejection_summary t
                JOIN runs r ON r.id = t.run_id
                WHERE r.run_type = 'production'
                  AND datetime(t.created_at) >= datetime(?)
                  AND datetime(t.created_at) < datetime(?)
                GROUP BY score_band
                """,
                self._window_bounds(days),
            ):
                band_counts[str(row["score_band"])] = int(row["count"] or 0)
            for band, count in band_counts.items():
                emit(f"job_intel_vacancy_score_band_total_{suffix}", count, {"score_band": band}, f"Vacancy score band counts over the last {suffix}")
                if band in {"60_74", "75_89", "90_100"}:
                    emit(f"job_intel_near_miss_count_{suffix}", count, {"score_band": band}, f"Near miss counts over the last {suffix}")

        # Rejections.
        for days, suffix in ((1, "24h"), (7, "7d"), (30, "30d")):
            for row in self.rejections_by_reason(days):
                emit(f"job_intel_rejections_{suffix}", int(row["count"] or 0), {"reason": str(row["reason"])}, f"Rejections by reason over the last {suffix}")
            if days == 7:
                for row in self.rejections_by_source_reason(days):
                    emit("job_intel_rejections_by_source_7d", int(row["count"] or 0), {"source": str(row["source"]), "reason": str(row["reason"])}, "Rejections by source and reason over the last 7d")

        # Geography / industry / role trends.
        geo_counts = self.geography_counts(7)
        industry_counts = self.industry_counts(7)
        role_counts = self.role_counts(7)
        for geo, count in geo_counts.items():
            emit("job_intel_opportunities_by_geography_total", count, {"geo": geo}, "Opportunity counts by geography")
        for industry, count in industry_counts.items():
            emit("job_intel_opportunities_by_industry_total", count, {"industry": industry}, "Opportunity counts by industry")
        for role, count in role_counts.items():
            emit("job_intel_opportunities_by_role_total", count, {"role": role}, "Opportunity counts by role bucket")

        # Company intelligence.
        company = self.company_counts(7)
        emit("job_intel_companies_discovered_total", company["discovered"], {"window": "7d"}, "Companies discovered over 7d")
        emit("job_intel_companies_prioritized_total", company["prioritized"], {"window": "7d"}, "Companies prioritized over 7d")
        emit("job_intel_companies_tier_total", company["tier1"], {"tier": "tier1"}, "Tier-1 companies")
        emit("job_intel_companies_tier_total", company["tier2"], {"tier": "tier2"}, "Tier-2 companies")

        # System health from source KPI rows.
        for days in (1, 7, 30):
            start, end = self._window_bounds(days)
            row = self._fetchone(
                """
                SELECT
                    COUNT(*) AS runs,
                    SUM(CASE WHEN r.status = 'ok' THEN 1 ELSE 0 END) AS ok_runs,
                    SUM(COALESCE(k.login_walls, 0)) AS login_walls,
                    SUM(COALESCE(k.auth_redirects, 0)) AS auth_redirects,
                    SUM(COALESCE(k.anti_bot_events, 0)) AS anti_bot_events,
                    SUM(COALESCE(k.extraction_failures, 0)) AS extraction_failures
                FROM source_kpi_run k
                JOIN runs r ON r.id = k.run_id
                WHERE r.run_type = 'production'
                  AND datetime(r.started_at) >= datetime(?)
                  AND datetime(r.started_at) < datetime(?)
                """,
                (start, end),
            ) or {}
            emit(f"job_intel_daily_run_success_rate_{days}d", self._rate(int(row.get("ok_runs") or 0), int(row.get("runs") or 0)), help_text=f"Daily run success rate over the last {days}d")
            emit(f"job_intel_login_walls_{days}d", int(row.get("login_walls") or 0), help_text=f"Login walls over the last {days}d")
            emit(f"job_intel_auth_redirects_{days}d", int(row.get("auth_redirects") or 0), help_text=f"Auth redirects over the last {days}d")
            emit(f"job_intel_anti_bot_events_{days}d", int(row.get("anti_bot_events") or 0), help_text=f"Anti-bot events over the last {days}d")
            emit(f"job_intel_extraction_failures_{days}d", int(row.get("extraction_failures") or 0), help_text=f"Extraction failures over the last {days}d")

        return "\n".join(lines) + "\n"

    def render_http_payload(self) -> str:
        try:
            return self.render_text()
        except Exception:
            logger.exception("job-intel exporter failed while rendering metrics for %s", self.store.db_path)
            return (
                '# HELP job_intel_exporter_up Whether the exporter rendered metrics successfully\n'
                '# TYPE job_intel_exporter_up gauge\n'
                'job_intel_exporter_up 0\n'
                '# HELP job_intel_exporter_render_errors_total Total metric rendering failures\n'
                '# TYPE job_intel_exporter_render_errors_total counter\n'
                'job_intel_exporter_render_errors_total 1\n'
            )

    def serve(self, host: str = "0.0.0.0", port: int = 9899) -> None:
        exporter = self

        logger.info(
            "job-intel exporter listening on %s:%s using db_path=%s",
            host,
            port,
            exporter.store.db_path,
        )

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path not in {"/", "/metrics"}:
                    self.send_response(404)
                    self.end_headers()
                    return
                payload = exporter.render_http_payload().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

        server = HTTPServer((host, port), Handler)
        try:
            server.serve_forever()
        finally:
            server.server_close()


def record_daily_observability(
    store: JobIntelStore,
    run_id: int,
    scored_rows: Iterable[tuple[Vacancy, Evaluation, dict[str, Any], int, bool]],
    *,
    accepted_vacancy_ids: set[int] | None = None,
    notified_vacancy_ids: set[int] | None = None,
    dual_scores_by_url: dict[str, dict[str, Any]] | None = None,
) -> None:
    accepted_vacancy_ids = accepted_vacancy_ids or set()
    notified_vacancy_ids = notified_vacancy_ids or set()
    dual_scores_by_url = dual_scores_by_url or {}
    rejection_events: list[dict[str, Any]] = []
    for vacancy, evaluation, classification, vacancy_id, duplicate in scored_rows:
        score = int(getattr(evaluation, "score", 0) or 0)
        score_band = score_band_for(score)
        source = canonical_source_name(str(getattr(vacancy, "source", "unknown") or "unknown"))
        role_bucket = role_bucket_for(vacancy, classification)
        geo_bucket = geo_bucket_for(getattr(vacancy, "location", "") or "")
        industry_bucket = industry_bucket_for(vacancy)
        executive_detected = bool(classification.get("executive_detected"))
        accepted = int(vacancy_id in accepted_vacancy_ids or (getattr(evaluation, 'recommendation', None) in {'strong_fit','potential_fit'} and not duplicate))
        notified = int(vacancy_id in notified_vacancy_ids)
        confidence = float(score / 100.0) if score is not None else None
        created_at = getattr(vacancy, "scraped_at", None) or datetime.now(timezone.utc).isoformat()
        vacancy_key = canonical_vacancy_key(vacancy)
        recommendation = str(getattr(evaluation, "recommendation", None) or "") or None
        # Dual-score fields: look up by vacancy URL if dual scoring was active
        dual = dual_scores_by_url.get(getattr(vacancy, "url", None) or "")
        score_v1: int | None = int(dual["score_v1"]) if dual and dual.get("score_v1") is not None else None
        score_v2: int | None = int(dual["score_v2"]) if dual and dual.get("score_v2") is not None else None
        store.upsert_vacancy_observability(
            run_id=run_id,
            vacancy_key=vacancy_key,
            source=source,
            role_bucket=role_bucket,
            geo_bucket=geo_bucket,
            industry_bucket=industry_bucket,
            executive_detected=executive_detected,
            accepted=bool(accepted),
            notified=bool(notified),
            score=score,
            score_band=score_band,
            confidence=confidence,
            is_duplicate=bool(duplicate),
            created_at=created_at,
            company=str(getattr(vacancy, "company", None) or "") or None,
            title=str(getattr(vacancy, "title", None) or "") or None,
            location=str(getattr(vacancy, "location", None) or "") or None,
            url=str(getattr(vacancy, "url", None) or "") or None,
            score_v1=score_v1,
            score_v2=score_v2,
            active_score=score,
            recommendation=recommendation,
        )
        reasons = rejection_reasons_for(vacancy, evaluation, classification, duplicate=duplicate)
        top_reason = reasons[0] if reasons else None
        # Classify each rejection reason for summary counts
        classified = [classify_rejection_reason(r) for r in reasons]
        real_blockers = [r for r, (rt, _) in zip(reasons, classified) if rt == "blocker"]
        unknowns = [r for r, (rt, _) in zip(reasons, classified) if rt == "unknown"]
        warnings = [r for r, (rt, _) in zip(reasons, classified) if rt == "warning"]
        top_real_blocker = max(set(real_blockers), key=real_blockers.count) if real_blockers else None
        top_unknown_reason = max(set(unknowns), key=unknowns.count) if unknowns else None
        top_warning = max(set(warnings), key=warnings.count) if warnings else None
        store.upsert_vacancy_rejection_summary(
            run_id=run_id,
            vacancy_key=vacancy_key,
            source=source,
            score=score,
            score_band=score_band,
            accepted=bool(accepted),
            is_duplicate=bool(duplicate),
            rejection_reason_count=len(reasons),
            top_rejection_reason=top_reason,
            created_at=created_at,
            recommendation=recommendation,
            real_blocker_count=len(real_blockers),
            unknown_count=len(unknowns),
            warning_count=len(warnings),
            top_real_blocker=top_real_blocker,
            top_unknown_reason=top_unknown_reason,
            top_warning=top_warning,
        )
        if reasons:
            for reason, (reason_type, severity) in zip(reasons, classified):
                rejection_events.append(
                    {
                        "run_id": run_id,
                        "vacancy_key": vacancy_key,
                        "source": source,
                        "role_bucket": role_bucket,
                        "geo_bucket": geo_bucket,
                        "industry_bucket": industry_bucket,
                        "score": score,
                        "score_band": score_band,
                        "confidence": confidence,
                        "rejection_reason": reason,
                        "created_at": created_at,
                        "reason_type": reason_type,
                        "severity": severity,
                    }
                )
    store.insert_vacancy_rejection_events(rejection_events)


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _iso_to_epoch(value: str | None) -> int | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(dt.timestamp())
