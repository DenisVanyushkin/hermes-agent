"""A0: the runtime must prove it can raise the source before the market is queried.

Two shadow runs on 2026-08-26 recorded LinkedIn as unusable — run 467 as
``blocked``, run 468 as ``error`` — for a reason belonging to the unit rather
than to the market: ``browser_worker`` raises the browser through ``sudo`` while
the unit carries ``NoNewPrivileges``. Both runs stored ``NULL`` in every error
field, so the reason was lost.

The contract is expressed through an explicit pre-dispatch seam
(``runtime_capability_checks``) rather than through the source callable itself.
Without such a seam a blocked family and a working one present *identical*
input before dispatch, and the only way to tell them apart would be to
introspect the test double — which would make the implementation pass by magic
instead of by contract.

Bootstrap traffic is counted separately and is expected to be non-zero: raising
a browser contacts the site before any search is issued, so a contract
demanding zero packets to the domain could never be satisfied honestly.
"""

from __future__ import annotations

from datetime import datetime, timezone
import inspect
import json
from pathlib import Path
import sqlite3
from typing import get_args

import pydantic
import pytest

from job_intel.product_search.acquisition_probe import (
    OBSERVED_SOURCE_STATES,
    ProbeQuery,
    RuntimeCapabilityResult,
    SourceState,
    SourceIsolation,
    UNOBSERVED_SOURCE_STATES,
    run_probe,
)

RUNTIME_FAILURE = (
    'sudo: The "no new privileges" flag is set, which prevents sudo from '
    "running as root."
)


class BlockedRuntime:
    """A family whose runtime cannot start.

    ``check()`` is the pre-dispatch seam and reports the failure structurally.
    ``__call__`` is the market transport and must never be reached.
    """

    def __init__(self) -> None:
        self.capability_check_calls = 0
        self.bootstrap_traffic_events = 0
        self.market_query_dispatch_count = 0

    def check(self) -> dict[str, object]:
        self.capability_check_calls += 1
        self.bootstrap_traffic_events += 1  # raising the browser touches the site
        return {
            "state": "runtime_capability_blocked",
            "error_class": "runtime_capability",
            "error_fingerprint": "no_new_privileges_sudo",
            "error_message_truncated": RUNTIME_FAILURE[:120],
            "bootstrap_traffic_events": self.bootstrap_traffic_events,
        }

    def __call__(self, query: str):
        self.market_query_dispatch_count += 1
        raise RuntimeError(RUNTIME_FAILURE)


class ReadyRuntime:
    """Control group: an identical shape whose runtime does start."""

    def __init__(self) -> None:
        self.capability_check_calls = 0
        self.bootstrap_traffic_events = 0
        self.market_query_dispatch_count = 0

    def check(self) -> dict[str, object]:
        self.capability_check_calls += 1
        self.bootstrap_traffic_events += 1
        return {"state": "ready", "bootstrap_traffic_events": self.bootstrap_traffic_events}

    def __call__(self, query: str):
        self.market_query_dispatch_count += 1
        return []


class PlainFailure:
    """A family that starts fine and then fails while extracting.

    Its message deliberately contains the same sudo text: classification must
    come from the seam, not from string-matching the exception afterwards.
    """

    def __init__(self) -> None:
        self.market_query_dispatch_count = 0

    def check(self) -> dict[str, object]:
        return {"state": "ready", "bootstrap_traffic_events": 1}

    def __call__(self, query: str):
        self.market_query_dispatch_count += 1
        raise RuntimeError(RUNTIME_FAILURE)


def _queries() -> list[ProbeQuery]:
    return [
        ProbeQuery(query_id="q-linkedin", cell_id="uk", source_family="linkedin",
                   query="Head of Product United Kingdom"),
    ]


def _isolation(tmp_path: Path) -> dict[str, SourceIsolation]:
    """Real isolation: without it the probe refuses the family before calling
    it, and every assertion here would hold for an unrelated reason."""
    profile = tmp_path / "profile-clone"
    profile.mkdir(exist_ok=True)
    return {
        "linkedin": SourceIsolation(
            mode="cloned_profile", path=profile, collection_method="browser"
        ),
        "greenhouse": SourceIsolation(
            mode="api", path=None, collection_method="api"
        ),
    }


def _require_seam() -> None:
    """Missing parameter is a different failure from a broken one.

    Catching every TypeError from inside run_probe would report an error raised
    by the capability checker or by serialisation as 'the seam is absent',
    hiding real defects behind a plumbing message.
    """
    if "runtime_capability_checks" not in inspect.signature(run_probe).parameters:
        pytest.fail(
            "run_probe has no pre-dispatch capability seam, so a blocked "
            "runtime cannot be distinguished from a working one"
        )


def _run(tmp_path: Path, source, *, queries=None, checks=None):
    _require_seam()
    return run_probe(
        run_id="a0-red",
        queries=queries if queries is not None else _queries(),
        sources={"linkedin": source} if not isinstance(source, dict) else source,
        runtime_capability_checks=(
            {"linkedin": source.check} if checks is None and not isinstance(source, dict)
            else checks
        ),
        output_dir=tmp_path,
        isolation=_isolation(tmp_path),
        max_attempts=1,
        now=lambda: datetime(2026, 8, 26, tzinfo=timezone.utc),
    )


def test_a_blocked_runtime_dispatches_no_market_query(tmp_path) -> None:
    source = BlockedRuntime()

    _run(tmp_path, source)

    assert source.capability_check_calls == 1, "the capability seam was not consulted"
    assert source.bootstrap_traffic_events == 1, "bootstrap traffic is expected, not forbidden"
    assert source.market_query_dispatch_count == 0, (
        "the market was queried before the runtime proved it could start"
    )


def test_a_ready_runtime_is_dispatched_exactly_once(tmp_path) -> None:
    """Control: the difference comes from the seam's answer, not from the
    identity of the double. Without this the gate could simply block everything."""
    source = ReadyRuntime()

    _run(tmp_path, source)

    assert source.capability_check_calls == 1
    assert source.market_query_dispatch_count == 1


def test_the_reason_survives_into_the_serialised_record(tmp_path) -> None:
    """The point of A0 is that the reason stops being lost. In runs 467 and 468
    error_class, error_fingerprint and error_message_truncated were all NULL."""
    source = BlockedRuntime()

    _run(tmp_path, source)

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    attempts = summary.get("family_attempts", [])
    linkedin = [a for a in attempts if a.get("source_family") == "linkedin"]
    assert linkedin, (
        "no family_attempts record in summary; keys present: "
        f"{sorted(summary)}"
    )
    record = linkedin[0]
    assert record["outcome"] == "runtime_capability_blocked"
    assert record["error_class"], "error_class is empty, the reason is lost again"
    assert record["error_fingerprint"], "error_fingerprint is empty"
    assert record["error_message_truncated"], "error_message_truncated is empty"
    assert record["market_query_dispatch_count"] == 0
    assert record["bootstrap_traffic_events"] >= 1


def test_the_reason_is_readable_from_the_experiment_database(tmp_path) -> None:
    source = BlockedRuntime()

    _run(tmp_path, source)

    with sqlite3.connect(tmp_path / "experiment.sqlite3") as conn:
        rows = list(
            conn.execute(
                "SELECT summary_json FROM probe_runs WHERE run_id = ?", ("a0-red",)
            )
        )
    assert rows, "the run was not recorded"
    stored = json.loads(rows[0][0])
    attempts = stored.get("family_attempts", [])
    assert any(
        a.get("source_family") == "linkedin"
        and a.get("outcome") == "runtime_capability_blocked"
        and a.get("error_fingerprint")
        for a in attempts
    ), "the stored run does not carry the machine-readable reason"


def test_an_ordinary_extraction_failure_keeps_its_own_name(tmp_path) -> None:
    """Boundary: once dispatch was allowed, a failure belongs to the source.
    Reclassifying it as runtime-blocked by string-matching would hide real
    source problems behind a unit-shaped label."""
    source = PlainFailure()

    result = _run(tmp_path, source)

    assert source.market_query_dispatch_count == 1
    assert result.source_states["linkedin"] == "blocked_extraction_failure"


def test_a_blocked_family_is_not_completed_for_the_cell(tmp_path) -> None:
    source = BlockedRuntime()

    result = _run(tmp_path, source)

    assert result.source_states["linkedin"] == "runtime_capability_blocked"
    assert result.acquisition_outcomes["uk"] == "blocked"


def test_a_ready_runtime_with_empty_results_is_an_honest_zero(tmp_path) -> None:
    source = ReadyRuntime()

    result = _run(tmp_path, source)

    assert result.acquisition_outcomes["uk"] == "no_candidate_records"


def test_the_fixture_actually_reaches_the_source(tmp_path) -> None:
    """Guard on the guard: with wrong isolation the probe refuses the family
    before calling it, and the assertions above would pass for a reason that
    has nothing to do with the contract under test."""
    source = ReadyRuntime()

    _run(tmp_path, source)

    assert source.market_query_dispatch_count > 0, (
        "the probe never called the source; the fixture is wrong"
    )


class NonBrowserSource:
    """An ATS family: no browser is involved, so the browser gate does not
    apply. It deliberately has no ``check`` attribute — requiring one would
    make the browser contract leak into families it has nothing to do with."""

    def __init__(self) -> None:
        self.market_query_dispatch_count = 0

    def __call__(self, query: str):
        self.market_query_dispatch_count += 1
        return []


def _capability_record(tmp_path: Path, family: str) -> dict:
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    records = [
        a for a in summary.get("family_attempts", []) if a.get("source_family") == family
    ]
    assert records, (
        f"no family_attempts record for {family}; keys present: {sorted(summary)}"
    )
    return records[0]


def test_a_non_browser_family_is_recorded_as_not_applicable(tmp_path) -> None:
    """Collection state and browser-capability state are different axes: an ATS
    family that collected fine is `observed` on one and `not_applicable` on the
    other, and the second must be recorded rather than inferred."""
    source = NonBrowserSource()
    queries = [
        ProbeQuery(query_id="q-gh", cell_id="ats_global_snapshot",
                   source_family="greenhouse", query="snapshot"),
    ]

    _run(tmp_path, {"greenhouse": source}, queries=queries, checks={})

    assert source.market_query_dispatch_count == 1, "the ATS family must still run"
    record = _capability_record(tmp_path, "greenhouse")
    assert record["capability_state"] == "not_applicable", (
        f"got {record.get('capability_state')!r}; a family outside the browser "
        "gate must be named, not silently treated as having passed it"
    )


def test_headhunter_api_does_not_require_a_browser_capability_check(tmp_path) -> None:
    source = NonBrowserSource()
    queries = [
        ProbeQuery(
            query_id="q-hh",
            cell_id="kazakhstan",
            source_family="headhunter",
            query="Head of Product Kazakhstan",
        )
    ]

    result = run_probe(
        run_id="headhunter-api",
        queries=queries,
        sources={"headhunter": source},
        runtime_capability_checks={},
        output_dir=tmp_path,
        isolation={
            "headhunter": SourceIsolation(mode="api", path=None)
        },
    )

    assert source.market_query_dispatch_count == 1
    assert result.source_states["headhunter"] == "observed"
    record = _capability_record(tmp_path, "headhunter")
    assert record["capability_state"] == "not_applicable"


def test_an_unclassified_collection_method_fails_closed(tmp_path) -> None:
    source = NonBrowserSource()
    query = ProbeQuery(
        query_id="q-unknown",
        cell_id="unknown",
        source_family="unknown-family",
        query="Head of Product",
    )

    result = run_probe(
        run_id="unclassified-family",
        queries=[query],
        sources={"unknown-family": source},
        runtime_capability_checks={},
        output_dir=tmp_path,
        isolation={
            "unknown-family": SourceIsolation(
                mode="exclusive_lock", path=tmp_path / "unknown.lock"
            )
        },
    )

    assert source.market_query_dispatch_count == 0
    assert result.source_states["unknown-family"] == "runtime_capability_blocked"


def test_a_browser_family_marked_not_applicable_still_cannot_dispatch(tmp_path) -> None:
    """`not_applicable` is not evidence of capability. For a browser family it
    is a contradiction, and the safe reading of a contradiction is refusal."""
    class NotApplicableBrowser:
        def __init__(self) -> None:
            self.market_query_dispatch_count = 0

        def check(self) -> RuntimeCapabilityResult:
            return RuntimeCapabilityResult(state="not_applicable")

        def __call__(self, query: str):
            self.market_query_dispatch_count += 1
            return []

    source = NotApplicableBrowser()

    result = _run(tmp_path, source)

    assert source.market_query_dispatch_count == 0
    assert result.source_states["linkedin"] == "runtime_capability_blocked"


def test_a_browser_family_without_a_checker_fails_closed(tmp_path) -> None:
    """Absence of a capability check is not evidence of capability."""
    source = ReadyRuntime()

    result = _run(tmp_path, source, checks={})

    assert source.market_query_dispatch_count == 0, (
        "a browser family with no capability check reached the market anyway"
    )
    assert result.source_states["linkedin"] == "runtime_capability_blocked"


def test_an_unknown_capability_state_is_not_treated_as_ready(tmp_path) -> None:
    """The closed vocabulary must be a property of the contract, not an
    agreement between test doubles."""
    class UnknownState:
        def __init__(self) -> None:
            self.market_query_dispatch_count = 0

        def check(self):
            return {"state": "probably_fine"}

        def __call__(self, query: str):
            self.market_query_dispatch_count += 1
            return []

    source = UnknownState()

    result = _run(tmp_path, source)

    assert source.market_query_dispatch_count == 0, (
        "an unrecognised capability answer was treated as ready"
    )
    assert result.source_states["linkedin"] == "runtime_capability_blocked"


def _two_queries() -> list[ProbeQuery]:
    return [
        ProbeQuery(query_id="q-uk", cell_id="uk", source_family="linkedin",
                   query="Head of Product United Kingdom"),
        ProbeQuery(query_id="q-sg", cell_id="singapore", source_family="linkedin",
                   query="Head of Product Singapore"),
    ]


def test_the_capability_check_runs_once_per_family_not_once_per_query(tmp_path) -> None:
    """The live contract issues 112 LinkedIn queries. A cold bootstrap per query
    would be 112 browser starts, which is not a contract anyone would run."""
    source = ReadyRuntime()

    _run(tmp_path, source, queries=_two_queries())

    assert source.capability_check_calls == 1, (
        f"capability was checked {source.capability_check_calls} times for one family"
    )
    assert source.market_query_dispatch_count == 2, "both queries must still be issued"


def test_one_blocked_check_stops_every_query_of_that_family(tmp_path) -> None:
    source = BlockedRuntime()

    _run(tmp_path, source, queries=_two_queries())

    assert source.capability_check_calls == 1
    assert source.market_query_dispatch_count == 0


def test_a_failing_capability_check_fails_closed(tmp_path) -> None:
    """The preflight itself can throw. That must neither abort the run nor fall
    through into the generic extraction path, which would blame the source."""
    class ExplodingCheck:
        def __init__(self) -> None:
            self.market_query_dispatch_count = 0

        def check(self):
            raise TimeoutError("capability probe timed out waiting for CDP")

        def __call__(self, query: str):
            self.market_query_dispatch_count += 1
            return []

    source = ExplodingCheck()

    result = _run(tmp_path, source)

    assert source.market_query_dispatch_count == 0
    assert result.source_states["linkedin"] == "runtime_capability_blocked"
    record = _capability_record(tmp_path, "linkedin")
    assert record["error_class"], "an exploding check lost its reason"
    assert record["error_fingerprint"]
    assert record["error_message_truncated"]


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"error_class": "runtime_capability"},
        {"error_class": "runtime_capability", "error_fingerprint": "fp"},
        # whitespace is not a reason: "non-empty" must mean strip-non-empty
        {"error_class": "  ", "error_fingerprint": "fp", "error_message_truncated": "d"},
        {"error_class": "c", "error_fingerprint": "\t\n", "error_message_truncated": "d"},
        {"error_class": "c", "error_fingerprint": "fp", "error_message_truncated": "   "},
    ],
)
def test_blocked_without_a_reason_is_not_a_valid_result(fields) -> None:
    """A closed vocabulary is not enough: `blocked` with empty or whitespace
    fields reproduces exactly the loss A0 exists to stop."""
    with pytest.raises(pydantic.ValidationError):
        RuntimeCapabilityResult(state="runtime_capability_blocked", **fields)


def test_a_negative_bootstrap_count_is_rejected() -> None:
    with pytest.raises(pydantic.ValidationError):
        RuntimeCapabilityResult(state="ready", bootstrap_traffic_events=-5)


def _blocked(message: str) -> RuntimeCapabilityResult:
    return RuntimeCapabilityResult(
        state="runtime_capability_blocked",
        error_class="runtime_capability",
        error_fingerprint="fp",
        error_message_truncated=message,
    )


def test_the_message_boundary_is_exact() -> None:
    """A stated limit, not a vague `too long`: 512 accepted, 513 refused."""
    assert _blocked("x" * 512).error_message_truncated == "x" * 512

    with pytest.raises(pydantic.ValidationError):
        _blocked("x" * 513)


def test_source_state_groups_partition_the_closed_source_state_vocabulary() -> None:
    declared = set(get_args(SourceState))

    assert OBSERVED_SOURCE_STATES | UNOBSERVED_SOURCE_STATES == declared
    assert OBSERVED_SOURCE_STATES.isdisjoint(UNOBSERVED_SOURCE_STATES)

def test_bootstrap_unit_is_clone_and_namespace_parameterized() -> None:
    root = Path(__file__).resolve().parents[2]
    unit_path = (
        root / "deploy/systemd/experiments/job-intel-browser-bootstrap.service"
    )
    unit = unit_path.read_text(encoding="utf-8")
    assert "EnvironmentFile=/etc/job-intel/product-search-probe-experiment.env" in unit
    assert "Type=notify" in unit
    assert "KillMode=control-group" in unit
    assert "RemainAfterExit=yes" not in unit
    assert "ExecStop=" in unit
    assert "--profile ${PRODUCT_SEARCH_BROWSER_PROFILE_PATH}" in unit
    assert "--network-namespace ${PRODUCT_SEARCH_BROWSER_NETWORK_NAMESPACE}" in unit
    assert "/var/lib/browser-desktop/profiles/linkedin" not in unit

    acquisition_unit = (
        root / "deploy/systemd/experiments/job-intel-product-search-probe-experiment.service"
    ).read_text(encoding="utf-8")
    assert "BindsTo=job-intel-browser-bootstrap.service" in acquisition_unit
    assert "After=job-intel-browser-bootstrap.service" in acquisition_unit
    assert "sudo" not in acquisition_unit
