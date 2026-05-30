from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class Vacancy(BaseModel):
    source: str
    source_id: str
    company: str
    title: str
    location: str
    url: str
    description: str
    posted_at: str | None = None
    scraped_at: str | None = None
    salary: str | None = None
    company_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {
        "extra": "allow",
    }


class Evaluation(BaseModel):
    score: int
    tier: Literal["exceptional_fit", "strong_fit", "possible_fit", "weak_fit", "reject"]
    recommendation: Literal["exceptional_fit", "strong_fit", "possible_fit", "potential_fit", "near_miss", "reject"]
    salary_tier: str | None = None
    matched_signals: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    raw_breakdown: dict[str, int] = Field(default_factory=dict)


class VacancyResult(BaseModel):
    vacancy: Vacancy
    evaluation: Evaluation
    duplicate_of: str | None = None
    dedup_reason: str | None = None
    first_seen_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
