from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from job_intel.product_search.contracts import ImmutableArtifactRef


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/product_search/fixtures/company_evidence"
BUNDLE_PATH = FIXTURES / "company-evidence-bundle.v1.yaml"
THESIS_PATH = FIXTURES / "company-thesis-input.v1.yaml"
CONTRACT_PATH = ROOT / "config/product_search/company_evidence_contract.v1.yaml"


def _company_evidence() -> Any:
    spec = importlib.util.find_spec("job_intel.product_search.company_evidence")
    assert spec is not None, "Task 9 company_evidence domain module is missing"
    return importlib.import_module("job_intel.product_search.company_evidence")


def _symbol(name: str) -> Any:
    module = _company_evidence()
    assert hasattr(module, name), f"Task 9 contract symbol is missing: {name}"
    return getattr(module, name)


def _bundle_payload() -> dict[str, Any]:
    return yaml.safe_load(BUNDLE_PATH.read_text(encoding="utf-8"))


def _thesis_payload() -> dict[str, Any]:
    return yaml.safe_load(THESIS_PATH.read_text(encoding="utf-8"))


def _canonical_sha256(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=lambda item: item.isoformat().replace("+00:00", "Z"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _rehash_evidence(record: dict[str, Any]) -> None:
    record["content_sha256"] = _canonical_sha256(
        {key: value for key, value in record.items() if key != "content_sha256"}
    )


def _rehash_bundle(payload: dict[str, Any]) -> None:
    payload["content_sha256"] = _canonical_sha256(
        {key: value for key, value in payload.items() if key != "content_sha256"}
    )


def test_company_identity_resolution_is_deterministic_and_closed() -> None:
    """Mutation caught: fuzzy aliases silently merge or invent a company identity."""
    CompanyIdentityV1 = _symbol("CompanyIdentityV1")
    resolve_company_identity = _symbol("resolve_company_identity")

    identities = (
        CompanyIdentityV1(
            company_id="company:northstar-commerce",
            canonical_name="Northstar Commerce",
            aliases=("Northstar",),
            domains=("northstar.example",),
        ),
        CompanyIdentityV1(
            company_id="company:northstar-labs",
            canonical_name="Northstar Labs",
            aliases=("Northstar",),
            domains=("labs.northstar.example",),
        ),
        CompanyIdentityV1(
            company_id="company:clearwater",
            canonical_name="Clearwater Systems",
            aliases=("Clearwater",),
            domains=("clearwater.example",),
        ),
    )

    by_domain = resolve_company_identity(identities, domain="NORTHSTAR.EXAMPLE")
    assert by_domain.model_dump(mode="json") == {
        "state": "resolved",
        "company_id": "company:northstar-commerce",
        "candidate_company_ids": ["company:northstar-commerce"],
    }

    ambiguous = resolve_company_identity(identities, name="Northstar")
    assert ambiguous.model_dump(mode="json") == {
        "state": "ambiguous",
        "company_id": None,
        "candidate_company_ids": [
            "company:northstar-commerce",
            "company:northstar-labs",
        ],
    }

    unresolved = resolve_company_identity(identities, name="North Star")
    assert unresolved.model_dump(mode="json") == {
        "state": "unresolved",
        "company_id": None,
        "candidate_company_ids": [],
    }


def test_ambiguous_or_unresolved_identity_cannot_satisfy_evidence() -> None:
    """Mutation caught: ambiguity is treated as a resolved company and merged."""
    CompanyEvidenceBundleV1 = _symbol("CompanyEvidenceBundleV1")
    payload = _bundle_payload()
    payload["identity_resolution"] = {
        "state": "ambiguous",
        "company_id": None,
        "candidate_company_ids": [
            "company:northstar-commerce",
            "company:northstar-labs",
        ],
    }
    _rehash_bundle(payload)

    with pytest.raises(ValidationError, match="ambiguous.*cannot satisfy evidence"):
        CompanyEvidenceBundleV1.model_validate(payload)


def test_source_timestamps_are_aware_ordered_and_public_redacted() -> None:
    """Mutation caught: future-at-capture or private source material enters a bundle."""
    CompanyEvidenceSourceV1 = _symbol("CompanyEvidenceSourceV1")
    source = _bundle_payload()["sources"][0]
    parsed = CompanyEvidenceSourceV1.model_validate(source)
    assert parsed.published_at < parsed.captured_at
    assert parsed.sensitivity.value == "public"
    assert parsed.redaction_state.value == "shareable_redacted"

    for field, value, message in (
        ("captured_at", "2026-08-02T09:00:00", "timezone"),
        ("published_at", "2026-08-03T09:00:00Z", "published_at"),
        ("source_uri", "hermes-private://career/user-notes.json", "public https"),
    ):
        invalid = dict(source)
        invalid[field] = value
        with pytest.raises(ValidationError, match=message):
            CompanyEvidenceSourceV1.model_validate(invalid)


def test_source_artifacts_are_content_addressed_and_tamper_evident(
    tmp_path: Path,
) -> None:
    """Mutation caught: source bytes change while their immutable hash stays pinned."""
    load_company_evidence_bundle = _symbol("load_company_evidence_bundle")
    bundle = load_company_evidence_bundle(BUNDLE_PATH)
    assert bundle.sources[0].artifact_ref.sha256 == (
        "bb04bf37de87fb7d3a005d3e10e8866456786169f01c69663c80bd6f1c9b1da1"
    )

    copied = tmp_path / "company_evidence"
    shutil.copytree(FIXTURES, copied)
    source_path = copied / "sources" / f"{bundle.sources[0].artifact_ref.sha256}.json"
    source_path.write_text('{"tampered":true}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="source artifact sha256"):
        load_company_evidence_bundle(copied / BUNDLE_PATH.name)


def test_fact_and_inference_are_distinct_closed_evidence_kinds() -> None:
    """Mutation caught: an inference is silently promoted to a sourced fact."""
    CompanyEvidenceKind = _symbol("CompanyEvidenceKind")
    CompanyEvidenceRecordV1 = _symbol("CompanyEvidenceRecordV1")
    payload = _bundle_payload()
    kinds = {record["evidence_kind"] for record in payload["evidence"]}
    assert kinds == {"fact", "inference"}
    assert {item.value for item in CompanyEvidenceKind} == {"fact", "inference"}

    invalid = dict(payload["evidence"][0])
    invalid["evidence_kind"] = "signal"
    _rehash_evidence(invalid)
    with pytest.raises(ValidationError, match="evidence_kind"):
        CompanyEvidenceRecordV1.model_validate(invalid)


def test_freshness_state_must_match_bundle_as_of_time() -> None:
    """Mutation caught: expired evidence remains current and satisfies Gate B."""
    CompanyEvidenceBundleV1 = _symbol("CompanyEvidenceBundleV1")
    payload = _bundle_payload()
    payload["evidence"][0]["fresh_until"] = "2026-08-09T00:00:00Z"
    _rehash_evidence(payload["evidence"][0])
    _rehash_bundle(payload)

    with pytest.raises(ValidationError, match="freshness_state"):
        CompanyEvidenceBundleV1.model_validate(payload)


def test_corrections_append_with_valid_supersession_links() -> None:
    """Mutation caught: a correction overwrites evidence or links another company."""
    CompanyEvidenceBundleV1 = _symbol("CompanyEvidenceBundleV1")
    load_company_evidence_bundle = _symbol("load_company_evidence_bundle")
    bundle = load_company_evidence_bundle(BUNDLE_PATH)
    replacement = next(
        item for item in bundle.evidence if item.evidence_id == "evidence:northstar:risk:2"
    )
    assert replacement.supersedes_evidence_id == "evidence:northstar:risk:1"
    assert next(
        item for item in bundle.evidence if item.evidence_id == "evidence:northstar:risk:1"
    ).contradiction_state.value == "contradicted"
    with pytest.raises(ValidationError, match="frozen"):
        replacement.statement = "mutated in place"

    payload = _bundle_payload()
    payload["evidence"][6]["supersedes_evidence_id"] = "evidence:missing"
    _rehash_evidence(payload["evidence"][6])
    _rehash_bundle(payload)
    with pytest.raises(ValidationError, match="supersedes_evidence_id"):
        CompanyEvidenceBundleV1.model_validate(payload)


def test_weekly_thesis_cannot_cite_a_superseded_evidence_revision() -> None:
    """Mutation caught: a corrected evidence revision remains consumable."""
    load_company_evidence_bundle = _symbol("load_company_evidence_bundle")
    load_company_thesis_input = _symbol("load_company_thesis_input")
    payload = _bundle_payload()
    payload["evidence"][5]["contradiction_state"] = "unopposed"
    _rehash_evidence(payload["evidence"][5])
    _rehash_bundle(payload)
    bundle = load_company_evidence_bundle(payload)

    thesis_payload = _thesis_payload()
    thesis_payload["evidence_bundle_ref"]["sha256"] = bundle.content_sha256
    thesis_payload["supporting_evidence_ids"] = [
        "evidence:northstar:scale:1",
        "evidence:northstar:risk:1",
    ]
    with pytest.raises(ValueError, match="superseded evidence"):
        load_company_thesis_input(thesis_payload, evidence_bundle=bundle)


def test_sufficiency_requires_each_company_dimension_not_a_signal() -> None:
    """Mutation caught: a partial bundle or company event is labeled sufficient."""
    CompanyEvidenceBundleV1 = _symbol("CompanyEvidenceBundleV1")
    payload = _bundle_payload()
    payload["evidence"] = [
        record for record in payload["evidence"] if record["dimension"] != "credible_need"
    ]
    _rehash_bundle(payload)
    with pytest.raises(ValidationError, match="credible_need"):
        CompanyEvidenceBundleV1.model_validate(payload)

    payload = _bundle_payload()
    payload["evidence"] = [
        record for record in payload["evidence"] if record["dimension"] == "signal_event"
    ]
    payload["sufficiency_state"] = "sufficient"
    _rehash_bundle(payload)
    with pytest.raises(ValidationError, match="signal.*insufficient"):
        CompanyEvidenceBundleV1.model_validate(payload)


def test_company_dimensions_cover_gate_b_inputs_without_creating_opportunities() -> None:
    """Mutation caught: scale, risk, or need disappears, or an event becomes a vacancy."""
    CompanyEvidenceDimension = _symbol("CompanyEvidenceDimension")
    CompanyEvidenceRecordV1 = _symbol("CompanyEvidenceRecordV1")
    assert {item.value for item in CompanyEvidenceDimension} == {
        "scale_stage",
        "trajectory",
        "business_model",
        "employer_risk",
        "geographic_context",
        "credible_need",
        "signal_event",
    }

    event = deepcopy(_bundle_payload()["evidence"][-1])
    event["vacancy_url"] = "https://jobs.example/role"
    _rehash_evidence(event)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CompanyEvidenceRecordV1.model_validate(event)


def test_weekly_thesis_requires_evidence_fit_thesis_and_proposed_action() -> None:
    """Mutation caught: weekly intelligence is emitted from a signal alone."""
    CompanyThesisInputV1 = _symbol("CompanyThesisInputV1")
    load_company_evidence_bundle = _symbol("load_company_evidence_bundle")
    load_company_thesis_input = _symbol("load_company_thesis_input")
    bundle = load_company_evidence_bundle(BUNDLE_PATH)
    thesis = load_company_thesis_input(THESIS_PATH, evidence_bundle=bundle)
    assert thesis.proposed_action.value == "research"
    assert thesis.evidence_bundle_ref.sha256 == bundle.content_sha256

    for missing in ("supporting_evidence_ids", "fit_thesis", "proposed_action"):
        payload = _thesis_payload()
        del payload[missing]
        with pytest.raises(ValidationError):
            CompanyThesisInputV1.model_validate(payload)

    signal_only = _thesis_payload()
    signal_only["supporting_evidence_ids"] = ["evidence:northstar:signal:1"]
    with pytest.raises(ValueError, match="signal alone"):
        load_company_thesis_input(signal_only, evidence_bundle=bundle)


def test_company_and_vacancy_evidence_stay_separate_but_correlated() -> None:
    """Mutation caught: vacancy data is embedded into company evidence records."""
    load_company_evidence_bundle = _symbol("load_company_evidence_bundle")
    bundle = load_company_evidence_bundle(BUNDLE_PATH)
    vacancy_ref = bundle.vacancy_evidence_refs[0]
    assert isinstance(vacancy_ref, ImmutableArtifactRef)
    assert vacancy_ref.artifact_id == "vacancy-evidence:redacted-001"
    assert all(not hasattr(item, "vacancy") for item in bundle.evidence)
    assert all(item.company_id == bundle.company_identity.company_id for item in bundle.evidence)


def test_private_candidate_facts_and_user_notes_are_rejected() -> None:
    """Mutation caught: private profile/user-note fields leak into shareable evidence."""
    CompanyEvidenceBundleV1 = _symbol("CompanyEvidenceBundleV1")
    CompanyThesisInputV1 = _symbol("CompanyThesisInputV1")
    bundle_payload = _bundle_payload()
    bundle_payload["candidate_facts"] = {"leadership_scope": "private"}
    _rehash_bundle(bundle_payload)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CompanyEvidenceBundleV1.model_validate(bundle_payload)

    thesis_payload = _thesis_payload()
    thesis_payload["user_notes"] = "private note"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CompanyThesisInputV1.model_validate(thesis_payload)


def test_content_hash_is_literal_tamper_evident_and_replay_is_deterministic() -> None:
    """Mutation caught: canonical bytes or evidence content change without a new hash."""
    CompanyEvidenceBundleV1 = _symbol("CompanyEvidenceBundleV1")
    load_company_evidence_bundle = _symbol("load_company_evidence_bundle")
    first = load_company_evidence_bundle(BUNDLE_PATH)
    second = load_company_evidence_bundle(BUNDLE_PATH)
    assert first.content_sha256 == (
        "c0c7415fc0e10d799c21698579d144f8432bf648bb402aef49049b6f7816b6f7"
    )
    assert first.model_dump_json() == second.model_dump_json()

    tampered = _bundle_payload()
    tampered["evidence"][0]["statement"] = "Changed without a correction."
    with pytest.raises(ValidationError, match="content_sha256"):
        CompanyEvidenceBundleV1.model_validate(tampered)


def test_contract_file_is_versioned_closed_and_persistence_free() -> None:
    """Mutation caught: the YAML authority permits private or persisted inputs."""
    load_company_evidence_contract = _symbol("load_company_evidence_contract")
    contract = load_company_evidence_contract(CONTRACT_PATH)
    assert contract.schema_version == "1.0.0"
    assert contract.product_authority_id == "PS-SOT-2026-08-10-v1"
    assert contract.persistence == "prohibited"
    assert contract.private_inputs == "prohibited"
    assert set(contract.weekly_intelligence_requires) == {
        "company_evidence",
        "fit_thesis",
        "proposed_action",
    }


def test_company_evidence_import_has_no_persistence_or_runtime_dependencies() -> None:
    """Mutation caught: importing the domain contract reaches SQL/store/runtime APIs."""
    script = f"""
import importlib.abc
import sys
sys.path.insert(0, {str(ROOT)!r})
# Existing package initialization imports the legacy store.  Preload the Task 8
# domain dependency before installing the hook so this test measures only new
# imports caused by the Task 9 module.
import job_intel.product_search.contracts
blocked = (
    'sqlite3',
    'sqlalchemy',
    'job_intel.product_search.store',
    'job_intel.store',
    'job_intel.runtime',
    'job_intel.migrations',
    'job_intel.slack',
)
class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in blocked or fullname.startswith(tuple(name + '.' for name in blocked)):
            raise RuntimeError('forbidden dependency imported: ' + fullname)
        return None
sys.meta_path.insert(0, Blocker())
import job_intel.product_search.company_evidence
print('domain-import-ok')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "domain-import-ok"
