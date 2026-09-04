"""The LinkedIn plan must not ask for a term measured to empty its results.

On 2026-09-04 the daily LinkedIn query returned the source's own
`No matching jobs found.` in fifteen of eighteen geographies, and the cause was
one term: `AI products`. In the shape the plan actually asks -- a role group
AND a context group -- adding it does not widen the result set, it destroys it,
and that survives quoting.

The measurement was made against LinkedIn, so the remedy is scoped to LinkedIn.
`CONTEXT_FAMILIES` also feeds `rotating_source_queries` for every other source
and `discovery_queries`; changing it in place would have changed what HeadHunter
and company discovery search for on the strength of an experiment run against a
different source. That restraint is asserted here rather than assumed, because
it is invisible in the LinkedIn tests that motivated the change.

These tests cannot ask the source anything. They pin what was measured against
it, so that putting the term back is a failure here rather than a silent month
of empty runs.
"""

from __future__ import annotations

import pytest

from job_intel import sources


def test_the_linkedin_plan_asks_for_no_measured_term() -> None:
    """Through the builder: the plan is what gets asked."""

    plan = sources.rotating_linkedin_queries(limit=18)

    assert plan
    for item in plan:
        offender = sources.linkedin_query_carries_measured_empty_term(item.query)
        assert offender is None, (offender, item.query)


def test_the_linkedin_plan_asks_for_the_replacement_instead() -> None:
    """The subject survived; only the wording that emptied the search changed.

    Dropping the term would have removed a subject from the search along with
    the defect.
    """

    queries = " ".join(item.query for item in sources.rotating_linkedin_queries(limit=18))

    assert "artificial intelligence products" in queries


def test_the_shared_vocabulary_is_left_alone() -> None:
    """The restraint, asserted rather than assumed.

    The families are shared. A remedy measured against one source must not
    silently redefine what the others look for.
    """

    context_terms = {term for _name, terms in sources.CONTEXT_FAMILIES for term in terms}

    assert "AI products" in context_terms
    assert "artificial intelligence products" not in context_terms


def test_other_sources_still_ask_what_they_asked_before() -> None:
    """Not the vocabulary but a builder that reads it, since that is the path
    the other sources actually take."""

    joined = " ".join(sources.rotating_source_queries("headhunter", limit=60))

    assert "AI products" in joined


def test_the_guard_is_not_fooled_by_case() -> None:
    """`ai products` empties a search exactly as `AI products` does.

    An equality check against the recorded spelling would let the lowercase
    form back into the vocabulary unnoticed, which is the whole failure this
    guard exists to prevent.
    """

    assert sources.linkedin_query_carries_measured_empty_term(
        "(head of product) (digital products OR ai products)"
    ) == "AI products"
    assert sources.linkedin_query_carries_measured_empty_term(
        "(head of product) (DIGITAL PRODUCTS OR Ml Products)"
    ) == "ML products"
    assert sources.linkedin_query_carries_measured_empty_term(
        "(head of product) (digital products)"
    ) is None


def test_the_measured_set_still_names_what_was_measured() -> None:
    """Without this, the guard disarms itself quietly.

    The tests above read `LINKEDIN_TERMS_MEASURED_TO_EMPTY_RESULTS` through the
    helper, so emptying it makes them pass vacuously and the plan is free
    again. The set records a measurement against the live source, not a
    tunable; adding a newly measured term is a deliberate edit here.
    """

    assert sources.LINKEDIN_TERMS_MEASURED_TO_EMPTY_RESULTS == frozenset(
        {"AI products", "ML products"}
    )
