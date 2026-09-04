"""The search vocabulary must not carry a term measured to empty the results.

On 2026-09-04 the daily LinkedIn query returned the source's own
`No matching jobs found.` in fifteen of eighteen geographies. The cause was one
term: `AI products`. Adding it as an OR alternative does not widen the result
set, it destroys it, and that behaviour survives quoting.

These tests cannot ask the source anything. They pin what was measured against
it, so that putting the term back is a failure here rather than a silent month
of empty runs.
"""

from __future__ import annotations

from job_intel import sources


def _every_term() -> list[tuple[str, str, str]]:
    out = []
    for label, families in (
        ("ROLE_FAMILIES", sources.ROLE_FAMILIES),
        ("CONTEXT_FAMILIES", sources.CONTEXT_FAMILIES),
        ("GEO_FAMILIES", sources.GEO_FAMILIES),
    ):
        for name, terms in families:
            for term in terms:
                out.append((label, name, term))
    return out


def test_no_family_carries_a_term_measured_to_empty_the_results() -> None:
    offenders = [
        (label, name, term)
        for label, name, term in _every_term()
        if term in sources.LINKEDIN_TERMS_MEASURED_TO_EMPTY_RESULTS
    ]

    assert offenders == []


def test_the_intent_survived_the_replacement() -> None:
    """The term was replaced, not dropped.

    Deleting it would have removed a subject from the search as well as the
    defect; `artificial intelligence products` was measured to return results
    with the rest of the production group intact.
    """

    context_terms = {term for _name, terms in sources.CONTEXT_FAMILIES for term in terms}

    assert "artificial intelligence products" in context_terms
    assert "AI products" not in context_terms


def test_the_daily_plan_carries_no_such_term() -> None:
    """Through the builder, not the vocabulary: the plan is what gets asked."""

    plan = sources.rotating_linkedin_queries(limit=18)

    for item in plan:
        for term in sources.LINKEDIN_TERMS_MEASURED_TO_EMPTY_RESULTS:
            assert term not in item.query, (term, item.query)
def test_the_measured_set_still_names_what_was_measured() -> None:
    """Without this, the guard disarms itself quietly.

    Two of the tests above iterate over
    `LINKEDIN_TERMS_MEASURED_TO_EMPTY_RESULTS`, so emptying it makes them pass
    vacuously and the vocabulary is free again. The set is a record of a
    measurement against the live source, not a tunable, so it is pinned.
    Adding a newly measured term is a deliberate edit here.
    """

    assert sources.LINKEDIN_TERMS_MEASURED_TO_EMPTY_RESULTS == frozenset(
        {"AI products", "ML products"}
    )
