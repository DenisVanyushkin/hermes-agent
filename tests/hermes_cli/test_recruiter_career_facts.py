"""Tests for the career facts source-of-truth loader."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from hermes_cli.recruiter_career_facts import load_career_facts
from hermes_cli.recruiter_decision_execution import build_decision_request_from_message

_FACTS = {"schema_version": "1.1.0", "candidate": {"full_name": "Denis Vanyushkin"}}
_PREFS = "schema_version: '1.0.0'\ntarget_roles:\n  seniority_levels: [VP, Director]\n"


def _write_sot(home: Path, *, corrupt: bool = False, approved: bool = True) -> None:
    base = home / "job_intel" / "career_facts"
    base.mkdir(parents=True)
    facts_path = base / "career_facts.json"
    prefs_path = base / "preferences.yaml"
    facts_path.write_text(json.dumps(_FACTS), encoding="utf-8")
    prefs_path.write_text(_PREFS, encoding="utf-8")
    facts_hash = hashlib.sha256(facts_path.read_bytes()).hexdigest()
    prefs_hash = hashlib.sha256(prefs_path.read_bytes()).hexdigest()
    if corrupt:
        facts_path.write_text(json.dumps({**_FACTS, "tampered": True}), encoding="utf-8")
    (base / "manifest.yaml").write_text(
        "schema_version: '1.0.0'\n"
        f"approved: {'true' if approved else 'false'}\n"
        "files:\n"
        "  - path: career_facts.json\n"
        "    source_kind: structured_resume\n"
        "    source_type: structured_resume\n"
        f"    sha256: {facts_hash}\n"
        "  - path: preferences.yaml\n"
        "    source_kind: candidate_preferences\n"
        "    source_type: candidate_preferences\n"
        f"    sha256: {prefs_hash}\n",
        encoding="utf-8",
    )


class TestLoadCareerFacts:
    def test_loads_valid_bundle(self, tmp_path: Path) -> None:
        _write_sot(tmp_path)
        bundle = load_career_facts(tmp_path)
        assert bundle.available
        assert len(bundle.sources) == 2
        assert all(source["approved"] for source in bundle.sources)
        assert bundle.facts["candidate"]["full_name"] == "Denis Vanyushkin"
        assert bundle.preferences["target_roles"]["seniority_levels"] == ["VP", "Director"]
        assert bundle.warnings == []

    def test_hash_mismatch_disables_bundle(self, tmp_path: Path) -> None:
        _write_sot(tmp_path, corrupt=True)
        bundle = load_career_facts(tmp_path)
        assert not bundle.available
        assert bundle.facts is None
        assert any("integrity" in warning for warning in bundle.warnings)

    def test_unapproved_manifest_disables_bundle(self, tmp_path: Path) -> None:
        _write_sot(tmp_path, approved=False)
        bundle = load_career_facts(tmp_path)
        assert not bundle.available
        assert any("not approved" in warning for warning in bundle.warnings)

    def test_missing_manifest(self, tmp_path: Path) -> None:
        bundle = load_career_facts(tmp_path)
        assert not bundle.available
        assert any("not found" in warning for warning in bundle.warnings)


class TestRequestWiring:
    def test_request_carries_facts_and_sources(self, tmp_path: Path) -> None:
        _write_sot(tmp_path)
        bundle = load_career_facts(tmp_path)
        request = build_decision_request_from_message(
            "оцени вакансию https://hh.ru/vacancy/1", career_facts=bundle
        )
        assert request.career_facts == bundle.facts
        assert request.candidate_preferences == bundle.preferences
        assert len(request.career_fact_sources) == 2
        assert request.vacancy_source["source_id"] == "https://hh.ru/vacancy/1"

    def test_request_without_bundle_stays_safe(self) -> None:
        request = build_decision_request_from_message("оцени вакансию")
        assert request.career_fact_sources == []
        assert request.career_facts is None
