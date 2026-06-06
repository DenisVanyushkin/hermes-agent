from __future__ import annotations

VALID_OPPORTUNITY_STATUSES = {
    "discovered",
    "notified",
    "watchlist",
    "evaluation_requested",
    "evaluated",
    "artifact_requested",
    "artifacts_ready",
    "application_planned",
    "applied",
    "application_confirmed",
    "outreach_planned",
    "outreach_sent",
    "recruiter_replied",
    "interviewing",
    "assessment_received",
    "offer_process",
    "rejected_by_company",
    "declined_by_me",
    "on_hold",
    "stale",
    "closed",
    "archived",
}

TERMINAL_GUARDED_STATUSES = {
    "rejected_by_company",
    "declined_by_me",
    "closed",
    "archived",
}

DELIVERY_NOTIFIED_ALLOWED_FROM = {"discovered"}
