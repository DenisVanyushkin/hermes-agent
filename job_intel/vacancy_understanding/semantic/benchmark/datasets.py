"""Benchmark dataset builders (Step 5B, Slice 5B-3).

Four corpora, one case shape: {case_id, vacancy_key, title, text, company,
location, source_system}. Each builder is deterministic — same inputs, same
ordered case list — so dataset_hash() pins the run identity.

The construction of every case mirrors the environment its source used
(calibration's ControlCo/synthetic_control, golden fixtures' replay_input,
replay_full's corpus classification) so baseline numbers are comparable to
the Step 4B evidence rather than a subtly different re-extraction.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

import yaml

from job_intel.vacancy_understanding.semantic.contract import load_semantic_contract
from job_intel.vacancy_understanding.semantic.runtime.calibration import (
    iter_control_cases,
)
from job_intel.vacancy_understanding.semantic.runtime.replay_full import (
    DB_PATH,
    classify_corpus,
)

_REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURES_DIR = _REPO_ROOT / "tests/fixtures/vacancy_understanding"
DECISION_CASES_PATH = _REPO_ROOT / "tests/fixtures/shadow_evaluator/golden-decision-cases.yaml"

Case = dict[str, Any]


def control_cases(contract=None) -> tuple[str, list[Case]]:
    contract = contract or load_semantic_contract()
    cases = [
        {
            "case_id": c["key"], "vacancy_key": c["key"],
            "title": c["title"], "text": c["text"],
            "company": "ControlCo", "location": "Remote",
            "source_system": "synthetic_control",
        }
        for c in iter_control_cases(contract) if c["runnable"]
    ]
    return f"controls-{contract.metadata.contract_version}", cases


def _load_fixtures() -> dict[str, dict]:
    fixtures = {}
    for path in sorted(FIXTURES_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        fixtures[data["fixture_id"]] = data
    return fixtures


def _fixture_case(fixture: dict, case_id: str) -> Case:
    ri = fixture["replay_input"]
    return {
        "case_id": case_id, "vacancy_key": ri["vacancy_key"],
        "title": ri["title"], "text": ri.get("description") or "",
        "company": ri.get("company") or "Unknown",
        "location": ri.get("location") or "Unknown",
        "source_system": ri.get("source_system") or "golden_fixture",
    }


def golden_cases() -> tuple[str, list[Case]]:
    fixtures = _load_fixtures()
    cases = [_fixture_case(fx, fixture_id)
             for fixture_id, fx in sorted(fixtures.items())]
    return f"golden-fixtures-{len(cases)}", cases


def decision_cases() -> tuple[str, list[Case]]:
    doc = yaml.safe_load(DECISION_CASES_PATH.read_text())["golden_decision_cases"]
    fixtures = _load_fixtures()
    cases = []
    for entry in doc["cases"]:
        if entry.get("policy_only") or not entry.get("fixture_ref"):
            continue  # built from synthetic canonical records, no source text to extract
        cases.append(_fixture_case(fixtures[entry["fixture_ref"]], entry["id"]))
    return f"decision-golden-{doc['dataset_version']}", cases


def eligible_cases(db_path: str = DB_PATH) -> tuple[str, list[Case]]:
    """Historical eligible corpus via the SAME classification replay_full
    uses (read-only DB access). case_id is derived from vacancy_key, and the
    list is sorted by case_id, so the dataset is independent of DB scan
    order. The DB grows daily — snapshot with export_cases_jsonl() so later
    provider runs (5B-7) replay the IDENTICAL dataset."""
    _, eligible = classify_corpus(db_path)
    cases = [
        {
            "case_id": "e-" + hashlib.sha256(rec["vacancy_key"].encode()).hexdigest()[:16],
            "vacancy_key": rec["vacancy_key"],
            "title": rec["title"], "text": rec["clean_text"],
            "company": rec.get("company") or "Unknown",
            "location": rec.get("location") or "Unknown",
            "source_system": rec.get("source") or "unknown",
        }
        for rec in eligible
    ]
    cases.sort(key=lambda c: c["case_id"])
    return "eligible-corpus", cases


def export_cases_jsonl(cases: list[Case], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for case in cases:
            fh.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")


def load_cases_jsonl(path: Path) -> list[Case]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
