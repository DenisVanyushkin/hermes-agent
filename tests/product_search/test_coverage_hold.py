"""Coverage hold: the gate stays closed while coverage is unestablished.

The owner's C' decision says a completed execution and a productive pair do not
by themselves credit a cell, because sufficiency of coverage was never
established. These tests drive the real composition root -- run_probe, the real
aggregation and the real serialisation -- and read the outcome the probe
records, not a hand-built object.

The starting composition is deliberately one that PASSES the gate without the
hold. A composition that already fails for an unrelated reason would let an
implementation that ignores coverage evidence entirely look correct.
"""

import inspect
import json
import pathlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from typing import get_args

import job_intel.browser_sourcing as bs_module
import job_intel.product_search.acquisition_probe as acquisition_probe
from job_intel.product_search.acquisition_probe import SourceIsolation, run_probe

from test_acquisition_probe import _b1_record


def _linkedin_trace_without_progression() -> dict[str, object]:
    """A trace shaped like the one browser_sourcing writes for a planned run.

    Both requested offsets complete, and both return the same job ids: the
    execution axis is satisfied and the progression axis observed nothing. This
    is what the section B probe measured on 2026-09-02.
    """

    job_ids = ["4001", "4002"]
    pages = [
        {
            "requested_url": f"https://www.linkedin.com/jobs/search?start={offset}",
            "final_url": "https://www.linkedin.com/jobs/search?start=0&position=1&pageNum=0",
            "html_sha256": f"sha-{offset}",
            "page_classification": "usable_result_surface",
            "safety_reason": None,
            "dom_unique_job_ids": list(job_ids),
            "parsed_unique_job_ids_before_role_filter": list(job_ids),
            "returned_unique_job_ids": list(job_ids),
            "returned_outside_dom_job_ids": [],
            "returned_without_canonical_job_id_count": 0,
            "parsed_job_ids": list(job_ids),
            "duplicate_canonical_job_ids": [],
            "excluded_job_ids_by_reason": {},
            "unexplained_dom_job_ids": [],
            "job_id_outcomes": {},
            "extraction_counts": {},
            "planned_scroll_steps": 2,
            "completed_scroll_steps": 2,
            "scroll_trace": [],
            "artifact_ref": f"page-{offset}",
        }
        for offset in (0, 25)
    ]
    return {
        "pages": pages,
        "planned_page_offsets": [0, 25],
        "completed_page_offsets": [0, 25],
        "scroll_checkpoints": [],
        "extraction_counts": {"dom": 4, "returned": 4},
        "session_observation": "without_session",
        "stop_reason": "plan_complete",
        "failure_reason": "",
        "pages_fetched": 2,
    }


class _LinkedInSourceWithTrace:
    """Stands in for the browser-backed source, carrying a real-shaped trace."""

    def __init__(self, *, trace: dict[str, object] | None) -> None:
        self.last_trace = trace

    def __call__(self, _request: object) -> list[dict[str, str]]:
        return [_b1_record("linkedin-1")]


def _run_two_family_cell(
    tmp_path: Path,
    *,
    linkedin_source: object,
    linkedin_has_execution_plan: bool,
):
    """Two independent families, both productive, one credited record.

    Without a coverage hold this composition resolves to
    candidate_records_found -- asserted directly by the positive control below.
    """

    linkedin_query: dict[str, object] = {
        "query_id": "q-linkedin",
        "cell_id": "b1-cell",
        "source_family": "linkedin",
        "query": "Head of Product",
    }
    if linkedin_has_execution_plan:
        linkedin_query["execution_plan"] = {"page_offsets": [0, 25]}

    queries = (
        linkedin_query,
        {
            "query_id": "q-duckduckgo",
            "cell_id": "b1-cell",
            "source_family": "duckduckgo",
            "query": "Head of Product",
        },
    )
    sources = {
        "linkedin": linkedin_source,
        "duckduckgo": lambda _query: [_b1_record("duckduckgo-1")],
    }
    isolation = {
        family: SourceIsolation(
            mode="api", path=tmp_path / f"{family}.lock", collection_method="api"
        )
        for family in sources
    }
    return run_probe(
        run_id="coverage-hold-run",
        queries=queries,
        sources=sources,
        output_dir=tmp_path,
        isolation=isolation,
        max_attempts=1,
        minimum_independent_families_by_cell={"b1-cell": 2},
        credited_records_by_cell={"b1-cell": 1},
    )


def _run_audited(tmp_path: Path):
    """The composition under the hold: identical trace, execution plan present."""

    return _run_two_family_cell(
        tmp_path,
        linkedin_source=_LinkedInSourceWithTrace(
            trace=_linkedin_trace_without_progression()
        ),
        linkedin_has_execution_plan=True,
    )


def _run_unaudited(tmp_path: Path):
    """The released composition: identical trace, execution plan absent.

    Exactly one input differs from _run_audited -- whether the query declares
    an execution plan. The trace is the same object shape with the same
    contents, so a difference in outcome cannot be attributed to the evidence
    appearing or disappearing.
    """

    return _run_two_family_cell(
        tmp_path,
        linkedin_source=_LinkedInSourceWithTrace(
            trace=_linkedin_trace_without_progression()
        ),
        linkedin_has_execution_plan=False,
    )


def _persisted_linkedin_pair(summary: dict) -> dict:
    return next(
        pair
        for pair in summary["cell_family_attempts"]
        if pair["source_family"] == "linkedin"
    )


def _linkedin_pair(result) -> dict[str, object]:
    return next(
        pair
        for pair in result.cell_family_attempts
        if pair["source_family"] == "linkedin"
    )


def test_positive_control_composition_credits_the_cell_without_a_hold(
    tmp_path: Path,
) -> None:
    """Anti-vacuum guard: the same composition passes when nothing is audited.

    A pair whose queries declare no execution plan is not subject to the audit,
    so it behaves exactly as it did before. If this ever fails, every hold test
    below has stopped proving anything.
    """

    result = _run_unaudited(tmp_path)

    assert result.acquisition_outcomes["b1-cell"] == "candidate_records_found"


def test_hold_when_evidence_present_and_readable(tmp_path: Path) -> None:
    """Execution completed, records received, coverage never established.

    The only input that differs from the positive control is the declared
    execution plan. Coverage is unestablished, so the gate credit must not
    arise.
    """

    result = _run_audited(tmp_path)

    assert result.acquisition_outcomes["b1-cell"] != "candidate_records_found"


def test_composition_writes_honest_label(tmp_path: Path) -> None:
    """The withheld outcome reaches the artifact, not only the return value.

    Nothing in production reads the returned ProbeResult -- the sole call site
    discards it -- so the recorded label is the only thing anyone will ever
    see. This test therefore reads summary.json rather than the object.
    """

    _run_audited(tmp_path)

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    outcome = summary["acquisition_outcomes"]["b1-cell"]
    annotation = summary["acquisition_outcome_annotations"]["b1-cell"]

    assert outcome != "candidate_records_found"
    # The artifact carries the cell's label twice. Checking only the top-level
    # map let a run ship two contradicting labels for one cell, so both are
    # asserted, and asserted to agree.
    assert annotation["acquisition_outcome"] == outcome
    assert annotation["coverage_hold_reasons"] == ["pagination_progression_not_observed"]


def test_withheld_cell_does_not_claim_a_false_cause(tmp_path: Path) -> None:
    """The withheld cell names its own cause instead of borrowing a false one.

    Withholding credit by deducting the pair from the family counts would have
    produced insufficient_breadth: a claim about the number of sources, which
    is not what went wrong. Every legacy name would have misdescribed it.
    """

    result = _run_audited(tmp_path)

    assert result.acquisition_outcomes["b1-cell"] not in {
        "insufficient_breadth",
        "insufficient_corroboration",
        "no_candidate_records",
        "degraded",
        "blocked",
    }


def test_credited_and_received_are_identical_with_and_without_the_hold(
    tmp_path: Path,
) -> None:
    """The hold withholds credit; it does not rewrite what was counted.

    Compared as exact equality between the held and the released run of the
    same composition, not against constants: a constant would still pass an
    implementation that changed both runs in the same wrong direction.
    """

    held = _run_audited(tmp_path / "held")
    released = _run_unaudited(tmp_path / "released")

    assert (
        held.acquisition_outcome_annotations["b1-cell"]["credited_records"]
        == released.acquisition_outcome_annotations["b1-cell"]["credited_records"]
    )
    assert (
        held.credited_records_provenance["b1-cell"]
        == released.credited_records_provenance["b1-cell"]
    )
    assert (
        _linkedin_pair(held)["received_records"]
        == _linkedin_pair(released)["received_records"]
    )


# ---------------------------------------------------------------------------
# Question 0 -- the answer "no new top-level contract" holds only while nothing
# reads the document. That condition is pinned, not assumed.
# ---------------------------------------------------------------------------


def _production_sources() -> list[pathlib.Path]:
    root = pathlib.Path(acquisition_probe.__file__).resolve().parents[2]
    return [
        path
        for path in (root / "job_intel").rglob("*.py")
        if "tests" not in path.parts
    ]


def test_probe_result_has_no_production_consumer() -> None:
    """If this fails, the answer to question 0 has stopped being true.

    The plan declines to version the serialised result because nothing in
    production reads it back. That is a measurement, not a principle, so it is
    re-measured here rather than trusted.
    """

    readers = [
        path
        for path in _production_sources()
        if "ProbeResult.model_validate" in path.read_text(encoding="utf-8")
        or "SELECT summary_json" in path.read_text(encoding="utf-8")
    ]

    assert readers == []


def test_probe_result_still_forbids_extra_fields() -> None:
    """The nested envelope rides inside a pair record, not as a new field."""

    with pytest.raises(ValidationError):
        acquisition_probe.ProbeResult(
            run_id="x",
            stage_counts={},
            provisional_labels={},
            source_states={},
            acquisition_outcomes={},
            product_observability_state={},
            product_observability_reason={},
            credited_records_provenance={},
            degraded_families={},
            blocked_families={},
            duplicates=0,
            evidence=(),
            cost={},
            latency_seconds=0.0,
            coverage_decision={"held": True},
        )


# ---------------------------------------------------------------------------
# Question 6 -- the branch is chosen by an input that exists before any evidence
# ---------------------------------------------------------------------------


def _probe_query(query_id: str, *, with_plan: bool):
    payload: dict[str, object] = {
        "query_id": query_id,
        "cell_id": "b1-cell",
        "source_family": "linkedin",
        "query": "Head of Product",
    }
    if with_plan:
        payload["execution_plan"] = {"page_offsets": [0, 25]}
    return acquisition_probe.ProbeQuery.model_validate(payload)


def test_finalize_has_no_audited_query_ids_parameter() -> None:
    """A parameter that does not exist cannot be supplied by a caller.

    The scope is not protected by a rule saying callers must not pass their own
    audited set; it is protected by there being nowhere to pass it.
    """

    parameters = inspect.signature(acquisition_probe.finalize_pair_coverage).parameters

    assert "audited_query_ids" not in parameters
    assert set(parameters) == {"pair", "pair_queries", "query_audits"}


def test_scope_is_built_from_execution_plan_of_each_query() -> None:
    scope = acquisition_probe.build_coverage_scope(
        (_probe_query("q-a", with_plan=True), _probe_query("q-b", with_plan=False))
    )

    assert scope.query_plan_presence == (("q-a", True), ("q-b", False))
    assert scope.audited_query_ids == ("q-a",)
    assert scope.all_query_ids == ("q-a", "q-b")


def test_pair_with_mixed_plan_presence_is_audit_subject() -> None:
    """`any`, not `all`: one unplanned query must not release the whole pair.

    Under `all`, an incomplete declaration would grant the credit -- which is
    the counterexample of question 6 reappearing one level up.
    """

    scope = acquisition_probe.build_coverage_scope(
        (_probe_query("q-a", with_plan=True), _probe_query("q-b", with_plan=False))
    )

    assert scope.audit_subject is True


def test_audited_query_ids_exclude_queries_without_plan() -> None:
    """A query that produces no audit must not be missed from one either."""

    scope = acquisition_probe.build_coverage_scope(
        (_probe_query("q-a", with_plan=True), _probe_query("q-b", with_plan=False))
    )
    decision = acquisition_probe.resolve_coverage_hold(
        acquisition_probe.CoverageAudit(
            evidence_state="parsed",
            queries=(_audit_for("q-a", [("0", ["1"]), ("25", ["1"])]),),
            pagination_outcome="pagination_not_observed",
        ),
        scope=scope,
    )

    assert decision.reason == "pagination_progression_not_observed"


def test_total_evidence_loss_still_holds() -> None:
    """The worst case: the quantity the branch guards is gone entirely."""

    scope = acquisition_probe.build_coverage_scope((_probe_query("q-a", with_plan=True),))

    decision = acquisition_probe.resolve_coverage_hold(None, scope=scope)

    assert decision.held is True
    assert decision.reason == "coverage_not_evaluated"


# ---------------------------------------------------------------------------
# Questions 2 and 3 -- the reason mapping is total, and absence is not
# unverifiability
# ---------------------------------------------------------------------------


def _page(offset: int, job_ids: list[str], *, seen: set[str]) -> dict[str, object]:
    fresh = len([job_id for job_id in job_ids if job_id not in seen])
    seen.update(job_ids)
    return {
        "requested_offset": offset,
        "final_url": f"https://www.linkedin.com/jobs/search?start={offset}",
        "job_ids": tuple(job_ids),
        "page_classification": "usable_result_surface",
        "safety_reason": None,
        "final_url_start": offset,
        "final_url_start_matches_requested": True,
        "new_ids_vs_prior_offsets_count": fresh,
    }


def _audit_for(query_id: str, pages: list[tuple[str, list[str]]]):
    seen: set[str] = set()
    observations = tuple(
        acquisition_probe.PageProgressionObservation(
            **_page(int(offset), job_ids, seen=seen)
        )
        for offset, job_ids in pages
    )
    novel = [page.new_ids_vs_prior_offsets_count for page in observations[1:]]
    return acquisition_probe.QueryCoverageAudit(
        query_id=query_id,
        pages=observations,
        pagination_outcome=acquisition_probe._fold_pagination_outcome(novel),
    )


def _scope_for(*query_ids: str):
    return acquisition_probe.build_coverage_scope(
        tuple(_probe_query(query_id, with_plan=True) for query_id in query_ids)
    )


def test_pair_outcome_is_single_query_outcome_when_all_agree() -> None:
    audit = acquisition_probe.CoverageAudit(
        evidence_state="parsed",
        queries=(
            _audit_for("q-a", [("0", ["1"]), ("25", ["1"])]),
            _audit_for("q-b", [("0", ["2"]), ("25", ["2"])]),
        ),
        pagination_outcome="pagination_not_observed",
    )

    decision = acquisition_probe.resolve_coverage_hold(audit, scope=_scope_for("q-a", "q-b"))

    assert decision.reason == "pagination_progression_not_observed"


def test_pair_outcome_is_mixed_when_queries_disagree() -> None:
    """A pair aggregate is never evidence about a single query's progression."""

    audit = acquisition_probe.CoverageAudit(
        evidence_state="parsed",
        queries=(
            _audit_for("q-a", [("0", ["1"]), ("25", ["1"])]),
            _audit_for("q-b", [("0", ["2"]), ("25", ["3"])]),
        ),
        pagination_outcome=None,
    )

    decision = acquisition_probe.resolve_coverage_hold(audit, scope=_scope_for("q-a", "q-b"))

    assert decision.reason == "pagination_evidence_mixed_across_queries"


def test_first_zero_novelty_is_not_a_stopping_rule() -> None:
    """A, B, B, C: stopping at the first repeat would have lost C.

    The sequence is ambiguous, and ambiguity is recorded as ambiguity rather
    than resolved into either claim.
    """

    audit = _audit_for("q-a", [("0", ["A"]), ("25", ["B"]), ("50", ["B"]), ("75", ["C"])])

    assert audit.pagination_outcome == "pagination_ambiguous"


def test_progression_observed_still_holds_the_gate() -> None:
    """Even observed progression does not establish sufficiency.

    The owner set no sufficiency policy, so the outcome changes the reason and
    never the status or the hold.
    """

    audit = acquisition_probe.CoverageAudit(
        evidence_state="parsed",
        queries=(_audit_for("q-a", [("0", ["1"]), ("25", ["2"])]),),
        pagination_outcome="pagination_progression_observed_through",
    )

    decision = acquisition_probe.resolve_coverage_hold(audit, scope=_scope_for("q-a"))

    assert decision.held is True
    assert decision.coverage_status == "unestablished"
    assert decision.reason == "progression_observed_sufficiency_policy_absent"


def test_pair_outcome_holds_when_declared_query_missing_from_audit() -> None:
    audit = acquisition_probe.CoverageAudit(
        evidence_state="parsed",
        queries=(_audit_for("q-a", [("0", ["1"])]),),
        pagination_outcome="pagination_not_observed",
    )

    decision = acquisition_probe.resolve_coverage_hold(audit, scope=_scope_for("q-a", "q-b"))

    assert decision.reason == "coverage_audit_incomplete_for_declared_queries"


def test_pair_outcome_holds_when_audit_has_unknown_query() -> None:
    """An audit record with no parent pair is unrepresentable; a stray query id
    inside a real pair is not, so that one is detected."""

    audit = acquisition_probe.CoverageAudit(
        evidence_state="parsed",
        queries=(_audit_for("q-ghost", [("0", ["1"])]),),
        pagination_outcome="pagination_not_observed",
    )

    decision = acquisition_probe.resolve_coverage_hold(audit, scope=_scope_for("q-a"))

    assert decision.reason == "coverage_audit_references_unknown_query"


def test_reasons_distinguish_absent_from_unverifiable() -> None:
    """Scenario 2 and scenario 3 hold alike and must not be told alike."""

    scope = _scope_for("q-a")
    absent = acquisition_probe.resolve_coverage_hold(None, scope=scope)
    unverifiable = acquisition_probe.resolve_coverage_hold(
        acquisition_probe.CoverageAudit(
            evidence_state="unverifiable", queries=(), pagination_outcome=None
        ),
        scope=scope,
    )

    assert absent.held is True and unverifiable.held is True
    assert absent.reason == "coverage_not_evaluated"
    assert unverifiable.reason == "pagination_evidence_unverifiable"
    assert absent.reason != unverifiable.reason


def test_pairs_without_audit_are_not_held() -> None:
    scope = acquisition_probe.build_coverage_scope((_probe_query("q-a", with_plan=False),))

    decision = acquisition_probe.resolve_coverage_hold(None, scope=scope)

    assert decision.held is False
    assert decision.reason is None


# ---------------------------------------------------------------------------
# Questions 1 and 5 -- the persisted document is strict about itself
# ---------------------------------------------------------------------------


def test_coverage_audit_model_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        acquisition_probe.CoverageAudit(
            evidence_state="parsed",
            queries=(),
            pagination_outcome=None,
            surprise=1,
        )


def test_coverage_audit_model_is_frozen() -> None:
    audit = acquisition_probe.CoverageAudit(
        evidence_state="parsed", queries=(), pagination_outcome=None
    )

    with pytest.raises(ValidationError):
        audit.evidence_state = "unverifiable"


def test_persisted_decision_recomputes_from_persisted_document(tmp_path: Path) -> None:
    """The document reconstructs its own verdict, with no trusted outside input.

    Scope is read from the artifact rather than handed to the resolver, so this
    proves the document stands on its own rather than proving it stands when
    someone tells it what it is.
    """

    _run_audited(tmp_path)
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    pair = _persisted_linkedin_pair(summary)

    scope = acquisition_probe.CoverageScope.model_validate(pair["coverage_scope"])
    audit = acquisition_probe.CoverageAudit.model_validate(pair["coverage_audit"])
    recomputed = acquisition_probe.resolve_coverage_hold(audit, scope=scope)

    assert recomputed.model_dump() == pair["coverage_decision"]


def test_persisted_audit_round_trips_exactly(tmp_path: Path) -> None:
    _run_audited(tmp_path)
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    pair = _persisted_linkedin_pair(summary)

    audit = acquisition_probe.CoverageAudit.model_validate(pair["coverage_audit"])

    # Compared through JSON, because JSON is what the artifact is. Comparing
    # model_dump() against it would only be measuring that pydantic keeps
    # tuples where the file keeps arrays.
    assert json.loads(audit.model_dump_json()) == pair["coverage_audit"]


def test_scope_is_written_even_when_audit_absent() -> None:
    """"No payload" and "not subject to the audit" stay different states."""

    pair: dict[str, object] = {}
    acquisition_probe.finalize_pair_coverage(
        pair, pair_queries=(_probe_query("q-a", with_plan=True),), query_audits={}
    )

    assert pair["coverage_scope"]["audit_subject"] is True
    assert pair["coverage_audit"] is None
    assert pair["coverage_decision"]["reason"] == "coverage_not_evaluated"


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("queries", 0, "pages", 1, "new_ids_vs_prior_offsets_count"), 99),
        (("queries", 0, "pages", 1, "final_url_start"), 25),
        (("queries", 0, "pagination_outcome"), "pagination_progression_observed_through"),
        (("pagination_outcome"), "pagination_progression_observed_through"),
    ),
)
def test_forged_derivation_in_persisted_document_is_rejected(
    tmp_path: Path, path, value
) -> None:
    """A forged self-description is a parse error, never a returned model.

    The reader does not accept the stored value, recompute a different one and
    hand back an object as though both were true.
    """

    _run_audited(tmp_path)
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    document = _persisted_linkedin_pair(summary)["coverage_audit"]

    target = document
    keys = path if isinstance(path, tuple) else (path,)
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value

    with pytest.raises(ValidationError):
        acquisition_probe.CoverageAudit.model_validate(document)


def test_forged_audit_subject_in_persisted_scope_is_rejected(tmp_path: Path) -> None:
    _run_audited(tmp_path)
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    scope = _persisted_linkedin_pair(summary)["coverage_scope"]
    scope["audit_subject"] = False

    with pytest.raises(ValidationError):
        acquisition_probe.CoverageScope.model_validate(scope)


def test_forged_audited_query_ids_in_persisted_scope_is_rejected(tmp_path: Path) -> None:
    _run_audited(tmp_path)
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    scope = _persisted_linkedin_pair(summary)["coverage_scope"]
    scope["audited_query_ids"] = []

    with pytest.raises(ValidationError):
        acquisition_probe.CoverageScope.model_validate(scope)


def test_scope_fidelity_to_execution_plan_is_not_claimed(tmp_path: Path) -> None:
    """Named limitation, asserted so it cannot quietly become a promise.

    query_plan_presence is the one field the document cannot check against
    anything: the execution plans are not in it. A forged presence list is
    internally valid, and the acceptance criteria never claimed otherwise.
    """

    _run_audited(tmp_path)
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    scope = _persisted_linkedin_pair(summary)["coverage_scope"]
    scope["query_plan_presence"] = [["q-linkedin", False]]
    scope["audited_query_ids"] = []
    scope["audit_subject"] = False

    forged = acquisition_probe.CoverageScope.model_validate(scope)

    assert forged.audit_subject is False


# ---------------------------------------------------------------------------
# The two stages are separate, and the second one is where the pair is decided
# ---------------------------------------------------------------------------


def test_capture_stage_writes_no_pair_verdict(tmp_path: Path) -> None:
    """Capture runs per query -- four times, counting the failure branches.

    At that moment the other queries of the pair have not run, so a pair
    verdict written there could not see a mixed outcome or a missing query.
    """

    audit = acquisition_probe.build_query_coverage_audit(
        "q-a", _linkedin_trace_without_progression()
    )

    assert isinstance(audit, acquisition_probe.QueryCoverageAudit)
    assert not hasattr(audit, "held")


def test_finalize_folds_across_queries_not_within_one(tmp_path: Path) -> None:
    pair: dict[str, object] = {}
    acquisition_probe.finalize_pair_coverage(
        pair,
        pair_queries=(
            _probe_query("q-a", with_plan=True),
            _probe_query("q-b", with_plan=True),
        ),
        query_audits={
            "q-a": _audit_for("q-a", [("0", ["1"]), ("25", ["1"])]),
            "q-b": _audit_for("q-b", [("0", ["2"]), ("25", ["3"])]),
        },
    )

    assert pair["coverage_decision"]["reason"] == "pagination_evidence_mixed_across_queries"


def test_admission_is_not_derivable_from_the_legacy_outcome(tmp_path: Path) -> None:
    """Two runs whose legacy pair outcomes are identical, gates differing.

    This is the observable form of the D0 requirement: admission cannot be read
    off the legacy outcome field, because the same field yields two gates.
    """

    held = _run_audited(tmp_path / "held")
    released = _run_unaudited(tmp_path / "released")

    assert _linkedin_pair(held)["outcome"] == _linkedin_pair(released)["outcome"]
    assert (
        held.acquisition_outcomes["b1-cell"] != released.acquisition_outcomes["b1-cell"]
    )


def test_both_axes_reach_the_persisted_envelope(tmp_path: Path) -> None:
    """The page label and the safety reason are in the artifact, per page.

    Functions that classify correctly and are never called prove nothing about
    what anyone will read. This asserts the recorded document, which is the
    only place either value is observable.
    """

    _run_audited(tmp_path)
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    pages = _persisted_linkedin_pair(summary)["coverage_audit"]["queries"][0]["pages"]

    assert [page["page_classification"] for page in pages] == [
        "usable_result_surface",
        "usable_result_surface",
    ]
    assert [page["safety_reason"] for page in pages] == [None, None]


def test_a_page_recorded_without_its_classification_is_unreadable_evidence(
    tmp_path: Path,
) -> None:
    """Absent, not defaulted.

    Reading a missing label as "unknown" would invent a classification nobody
    measured, and the invented value would then be indistinguishable from a
    measured one. The pair is held as unverifiable instead.
    """

    trace = _linkedin_trace_without_progression()
    for page in trace["pages"]:
        del page["page_classification"]

    with pytest.raises(ValueError):
        acquisition_probe.build_query_coverage_audit("q-a", trace)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("page_classification", "not_a_declared_class"),
        ("page_classification", "usable"),
        ("safety_reason", "not_a_declared_reason"),
    ),
)
def test_a_persisted_page_outside_the_declared_sets_is_rejected(
    tmp_path: Path, field: str, value: str
) -> None:
    """The declared sets are the contract of the field, not a comment near it.

    Both fields were plain str first, so the envelope accepted and stored
    anything -- an abbreviated form like "usable", a value from a future
    revision, a tampered one -- and the tuples of names beside them meant
    nothing to the reader. An unknown value now makes the document unreadable
    rather than becoming a new value nobody declared.
    """

    _run_audited(tmp_path)
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    document = _persisted_linkedin_pair(summary)["coverage_audit"]
    document["queries"][0]["pages"][0][field] = value

    with pytest.raises(ValidationError):
        acquisition_probe.CoverageAudit.model_validate(document)


def test_the_declared_sets_are_read_out_of_the_types_they_declare() -> None:
    """One definition, not a tuple maintained beside a type.

    A tuple written twice drifts the moment one copy is edited, and nothing
    fails when it does. These are the type's own arguments, so they cannot
    disagree with the type.
    """

    assert bs_module.LINKEDIN_PAGE_CLASSIFICATIONS == get_args(
        bs_module.LinkedInPageClassification
    )
    assert bs_module.LINKEDIN_SAFETY_REASONS == get_args(
        bs_module.LinkedInSafetyReason
    )
    assert len(bs_module.LINKEDIN_PAGE_CLASSIFICATIONS) == 5
    assert len(bs_module.LINKEDIN_SAFETY_REASONS) == 7
