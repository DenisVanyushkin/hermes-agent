"""onsite_requirement_mismatch must reflect the work format, not the word.

Every string below is taken from a real vacancy description in the live
job-intel corpus. The reason is classified as a high-severity blocker, so a
false positive on it distorts the rejection dashboards and the morning digest —
and a false negative hides a genuine office requirement.

Sense counts measured over the 5779 descriptions in the corpus: `hybrid` next to
a work-format word 530 docs, `hybrid role` 91 docs, versus `hybrid <word> role`
3 docs and `hybrid cloud|network|pricing|...` 14 docs. Work format is the
dominant sense, so it is the default and only the countable noise senses are
excluded.
"""

from __future__ import annotations

import pytest

from job_intel.models import Evaluation, Vacancy
from job_intel.observability import rejection_reasons_for

REASON = "onsite_requirement_mismatch"


def _reasons(description: str, *, location: str = "Berlin, Germany") -> list[str]:
    vacancy = Vacancy(
        source="greenhouse",
        source_id="1",
        company="Example",
        title="Head of Product",
        location=location,
        url="https://example.com/1",
        description=description,
    )
    evaluation = Evaluation(score=30, tier="reject", recommendation="reject")
    return rejection_reasons_for(
        vacancy, evaluation, {"executive_detected": True}, duplicate=False
    )


# --- noise senses: the word appears, the requirement does not ---------------


@pytest.mark.parametrize(
    "label, description, location",
    [
        (
            "linkedin tracking tag",
            'company events<br><span style="color: rgb(255, 255, 255);">#li-hybrid</span>',
            "El Salvador (Remote)",
        ),
        (
            "linkedin tracking tags, onsite variant",
            '<div><span style="color: rgb(236, 240, 241);">#li-az1 #li-onsite</span></div>',
            "Singapore, Singapore",
        ),
        (
            "onsites as a plural noun for company events",
            "manage team meetings, onsites, and events by handling scheduling and vendor coordination",
            "Remote, Americas",
        ),
        (
            "onsite interview logistics",
            "our process is a screen, a take-home and a final on-site interview with the leadership team",
            "Remote, Europe",
        ),
        (
            "hybrid modifying the discipline, not the workplace",
            "6+ years in product management, management consulting, data science or a hybrid analytical role",
            "New York, NY, United States",
        ),
        (
            "hybrid as a technical architecture term",
            "you will take full ownership of our hybrid infrastructure, managing the lifecycle of our fleet",
            "Remote, Europe",
        ),
        (
            "hybrid as a commercial pricing term",
            "drive the transition across seat-based, usage-based, and hybrid pricing models",
            "Remote, US",
        ),
    ],
)
def test_word_without_work_format_meaning_is_not_an_onsite_requirement(
    label: str, description: str, location: str
) -> None:
    assert REASON not in _reasons(description, location=location), label


# --- genuine work-format statements -----------------------------------------


@pytest.mark.parametrize(
    "label, description, location",
    [
        (
            "hybrid environment anchored to an office",
            "this role will be based in our seattle office. we are a hybrid environment that combines "
            "the energy of being in the office with the benefits of flexibility",
            "Seattle, Washington, United States",
        ),
        (
            "explicit days in office",
            "ability to work hybrid, 3 days a week in office, in toronto, canada",
            "Toronto, ON",
        ),
        (
            "hybrid workplace",
            "we operate as a hybrid workplace to ensure our employees can create a work-life harmony",
            "Paris, France",
        ),
        (
            "hybrid role with in-office days",
            "based in berlin or barcelona - or willing to relocate. this is a hybrid role with 8 "
            "in-office days per month.",
            "Barcelona",
        ),
        (
            "bare hybrid in a location block",
            "<p><strong>location</strong></p><p>hybrid - herzliya, israel</p><p>full-time</p>",
            "Herzliya, Tel Aviv District",
        ),
        (
            "bare hybrid in a title line",
            "<p><strong>singapore | hybrid | full time</strong></p><p>what you will do</p>",
            "Singapore, Singapore",
        ),
        (
            "on-site stated in the location field",
            "we are looking for a product leader to own the roadmap.",
            "New York, NY (On-site)",
        ),
        (
            "hybrid approach of office and home working",
            "this role can be offered in a hybrid approach, working from our london office next to "
            "liverpool street station and from home",
            "London",
        ),
        (
            "remote and hybrid explicitly ruled out",
            "it is not a remote or hybrid-anywhere role. the startup motion is physical and the team "
            "sits together every day",
            "San Francisco, CA",
        ),
        (
            "german work-format wording",
            "flexibilitat und setup: hybrides arbeiten, flexible arbeitszeiten, moderne buros",
            "Munich, Germany",
        ),
    ],
)
def test_real_work_format_requirement_is_still_reported(
    label: str, description: str, location: str
) -> None:
    assert REASON in _reasons(description, location=location), label
