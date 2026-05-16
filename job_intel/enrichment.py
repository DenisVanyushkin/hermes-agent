from __future__ import annotations

from dataclasses import dataclass

from .models import Vacancy

DEFAULT_GAPS = [
    ("preferred_company_stage", "What company stage do you want most right now: startup, scale-up, or mature platform?"),
    ("travel_tolerance", "What is your comfortable travel cadence for a role like this: none, occasional, monthly, or frequent?"),
    ("preferred_compensation_mix", "Do you prefer salary-heavy, equity-heavy, or balanced compensation for your next move?"),
    ("willingness_for_APAC_relocation", "Are you open to relocation to APAC if the opportunity is exceptional?"),
]


def detect_high_value_questions(memory: dict[str, str], vacancies: list[Vacancy] | None = None) -> list[str]:
    questions: list[str] = []
    for key, question in DEFAULT_GAPS:
        if key not in memory or not memory[key].strip():
            questions.append(question)
    return questions[:3]
