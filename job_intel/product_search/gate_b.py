"""Content-addressed Gate B corpus selection and no-call preflight.

This module prepares the exact inputs needed for a later owner-approved record
run.  It intentionally contains no provider invocation, Slack integration, or
production persistence boundary.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping

import yaml

from job_intel.product_search.acquisition_probe import (
    _canonical_url,
    _minimum_evidence_sufficient,
)
from job_intel.product_search.evidence_synthesis import (
    OUTPUT_SCHEMA_VERSION,
    load_evidence_synthesis_policy,
    provider_output_schema_sha256,
    task10_prompt_sha256,
)
from job_intel.vacancy_understanding.semantic.contract import load_semantic_contract
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import (
    build_prompt_for_version,
)


GATE_A_RUN_ID = "gate-a-20260816T141344Z"
GATE_A_COMMIT = "65d60daae16093a9a7e34a11a159e2f789dd14dd"
GATE_A_MANIFEST_SHA256 = (
    "6ecc500c291061a34c4482edb5c2a0d6c547993bea0d346ad306041dfa81df3d"
)
DEFAULT_SAMPLE_SIZE = 48
DEFAULT_MAX_COST_PER_CALL_USD = Decimal("0.01")
REPO_ROOT = Path(__file__).resolve().parents[2]


class GateBPreflightError(RuntimeError):
    """Closed, machine-readable Gate B preparation failure."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _sha256_json(value: object) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def snapshot_paths(paths: Iterable[Path]) -> dict[str, tuple[int, int, int, str]]:
    snapshot: dict[str, tuple[int, int, int, str]] = {}
    for path in sorted({Path(item) for item in paths}):
        stat = path.stat()
        snapshot[str(path)] = (
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            _sha256_file(path),
        )
    return snapshot


def assert_paths_unchanged(
    before: Mapping[str, tuple[int, int, int, str]],
    after: Mapping[str, tuple[int, int, int, str]],
) -> None:
    if dict(before) != dict(after):
        changed = sorted(set(before) | set(after))
        raise GateBPreflightError("forbidden_side_effect_mutation:" + ",".join(changed))


def validate_gate_a_run_ids(
    *, evidence_run_ids: Iterable[str], probe_run_ids: Iterable[str]
) -> None:
    evidence = set(evidence_run_ids)
    runs = list(probe_run_ids)
    if evidence != {GATE_A_RUN_ID} or runs != [GATE_A_RUN_ID]:
        raise GateBPreflightError(
            f"mixed_gate_a_run_ids:evidence={sorted(evidence)},probe_runs={runs}"
        )


def _load_gate_a(gate_a_root: Path) -> tuple[list[dict[str, Any]], list[Path]]:
    manifest_path = gate_a_root / "manifest.yaml"
    database_path = gate_a_root / "experiment.sqlite3"
    if _sha256_file(manifest_path) != GATE_A_MANIFEST_SHA256:
        raise GateBPreflightError("gate_a_manifest_sha256_mismatch")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("commit") != GATE_A_COMMIT:
        raise GateBPreflightError("gate_a_commit_mismatch")
    if manifest.get("paths", {}).get("experiment.sqlite3") != str(database_path):
        raise GateBPreflightError("gate_a_database_identity_mismatch")

    connection = sqlite3.connect(f"file:{database_path}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = [
            dict(row)
            for row in connection.execute(
                "SELECT run_id, raw_content_sha256, query_id, source_family, "
                "source_id, raw_reference, redaction_class "
                "FROM probe_evidence ORDER BY raw_content_sha256"
            )
        ]
        run_ids = [
            str(row[0])
            for row in connection.execute(
                "SELECT run_id FROM probe_runs ORDER BY run_id"
            )
        ]
        integrity_row = connection.execute("PRAGMA integrity_check").fetchone()
        integrity = tuple(integrity_row) if integrity_row is not None else None
    finally:
        connection.close()
    if integrity != ("ok",):
        raise GateBPreflightError("gate_a_database_integrity_failure")
    validate_gate_a_run_ids(
        evidence_run_ids=(str(row["run_id"]) for row in rows),
        probe_run_ids=run_ids,
    )
    if len(rows) != 2414:
        raise GateBPreflightError(f"gate_a_raw_denominator_mismatch:{len(rows)}")

    canonical: dict[
        str, tuple[tuple[str, str, str], dict[str, Any], dict[str, Any]]
    ] = {}
    protected_paths = [manifest_path, database_path]
    for row in rows:
        if row["redaction_class"] != "vacancy_public_evidence":
            raise GateBPreflightError("gate_a_redaction_class_mismatch")
        raw_path = gate_a_root / str(row["raw_reference"])
        protected_paths.append(raw_path)
        raw_bytes = raw_path.read_bytes()
        if _sha256_bytes(raw_bytes) != row["raw_content_sha256"]:
            raise GateBPreflightError("gate_a_raw_content_sha256_mismatch")
        payload = json.loads(raw_bytes)
        for name in ("source_id", "query_id", "source_family"):
            if payload.get(name) != row[name]:
                raise GateBPreflightError(f"gate_a_raw_identity_mismatch:{name}")
        identity = _canonical_url(str(payload.get("url") or ""))
        if not identity:
            identity = hashlib.sha256(
                f"{payload.get('company')}\0{payload.get('title')}".encode()
            ).hexdigest()
        candidate_key = (
            str(row["source_family"]),
            str(row["source_id"]),
            str(row["raw_content_sha256"]),
        )
        current = canonical.get(identity)
        if current is None or candidate_key < current[0]:
            canonical[identity] = (candidate_key, payload, row)
    if len(canonical) != 1814:
        raise GateBPreflightError(
            f"gate_a_canonical_denominator_mismatch:{len(canonical)}"
        )
    sufficient = [
        item for item in canonical.values() if _minimum_evidence_sufficient(item[1])
    ]
    if len(sufficient) != 1314:
        raise GateBPreflightError(
            f"gate_a_minimum_evidence_denominator_mismatch:{len(sufficient)}"
        )
    return [
        {"payload": payload, "evidence": row, "canonical_identity": identity}
        for identity, (_, payload, row) in canonical.items()
        if _minimum_evidence_sufficient(payload)
    ], protected_paths


def _cell_lanes() -> dict[str, str]:
    payload = yaml.safe_load(
        (REPO_ROOT / "config/product_search/search_contract.v1.yaml").read_text(
            encoding="utf-8"
        )
    )
    result: dict[str, str] = {}
    for lane_id, lane in payload["lanes"].items():
        for cell in lane["cells"].values():
            result[cell["cell_id"]] = lane_id
    result["ats_global_snapshot"] = "global_ats"
    return result


def _role_pattern(title: str) -> str:
    value = title.casefold()
    if "chief product" in value or "cpo" in value:
        return "chief_product"
    if "vp product" in value or "vice president" in value:
        return "vp_product"
    if "head of product" in value or "head product" in value:
        return "head_product"
    if "director" in value:
        return "director"
    if "general manager" in value or value.startswith("gm "):
        return "general_manager"
    if "product" in value:
        return "product_other"
    return "adjacent"


def _sampling_case_type(payload: Mapping[str, Any], role_pattern: str) -> str:
    title = str(payload.get("title") or "").casefold()
    description = str(payload.get("description") or "").casefold()
    hard_terms = (
        "sales",
        "marketing",
        "analyst",
        "engineer",
        "developer",
        "intern",
    )
    if any(term in title for term in hard_terms) or any(
        term in description for term in ("on-site only", "onsite only")
    ):
        return "hard_block_hypothesis"
    if role_pattern in {"chief_product", "vp_product", "head_product"}:
        return "core_hypothesis"
    if (
        not payload.get("location")
        or str(payload.get("location")).casefold() == "unknown"
        or not payload.get("company")
        or len(str(payload.get("description") or "")) < 120
    ):
        return "important_unknown"
    return "exploration_hypothesis"


def _corpus_records(
    records: list[dict[str, Any]], sample_size: int
) -> list[dict[str, Any]]:
    lanes = _cell_lanes()
    enriched: list[dict[str, Any]] = []
    for item in records:
        payload = item["payload"]
        evidence = item["evidence"]
        role_pattern = _role_pattern(str(payload.get("title") or ""))
        case_type = _sampling_case_type(payload, role_pattern)
        record = {
            "run_id": evidence["run_id"],
            "source_family": evidence["source_family"],
            "source_id": evidence["source_id"],
            "query_id": evidence["query_id"],
            "raw_content_sha256": evidence["raw_content_sha256"],
            "raw_reference": evidence["raw_reference"],
            "canonical_identity_sha256": _sha256_bytes(
                item["canonical_identity"].encode("utf-8")
            ),
            "company": payload.get("company") or "unknown",
            "cell_id": payload.get("cell_id") or "unknown",
            "lane": lanes.get(str(payload.get("cell_id") or ""), "unknown"),
            "role_pattern": role_pattern,
            "origin": "open_market",
            "sampling_case_type": case_type,
            "decision_selection_mode": None,
        }
        record["selection_key"] = _sha256_json({
            "run_id": record["run_id"],
            "source_family": record["source_family"],
            "source_id": record["source_id"],
            "raw_content_sha256": record["raw_content_sha256"],
        })
        enriched.append(record)
    enriched.sort(key=lambda item: item["selection_key"])

    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()

    def add_first(group_field: str, values: Iterable[str] | None = None) -> None:
        groups = sorted(set(values or (str(item[group_field]) for item in enriched)))
        for group in groups:
            for item in enriched:
                if (
                    str(item[group_field]) == group
                    and item["selection_key"] not in selected_keys
                ):
                    selected.append(item)
                    selected_keys.add(item["selection_key"])
                    break

    add_first("sampling_case_type")
    add_first("lane")
    add_first("source_family")
    add_first("role_pattern")
    add_first("company", sorted({str(item["company"]) for item in enriched})[:12])

    strata: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in enriched:
        strata[
            (
                str(item["lane"]),
                str(item["source_family"]),
                str(item["role_pattern"]),
                str(item["sampling_case_type"]),
            )
        ].append(item)
    while len(selected) < sample_size:
        progressed = False
        for key in sorted(strata):
            for item in strata[key]:
                if item["selection_key"] not in selected_keys:
                    selected.append(item)
                    selected_keys.add(item["selection_key"])
                    progressed = True
                    break
            if len(selected) >= sample_size:
                break
        if not progressed:
            break
    if len(selected) != sample_size:
        raise GateBPreflightError(f"corpus_sample_size_unavailable:{len(selected)}")
    return sorted(selected, key=lambda item: item["selection_key"])


def _record_identity(corpus_sha256: str) -> dict[str, str]:
    policy = load_evidence_synthesis_policy()
    semantic_prompt = build_prompt_for_version(
        policy.semantic_prompt_version, load_semantic_contract()
    )
    paths = {
        "decision_contract_sha256": REPO_ROOT
        / "config/product_search/decision_contract.v2.yaml",
        "search_contract_sha256": REPO_ROOT
        / "config/product_search/search_contract.v1.yaml",
        "profile_sha256": REPO_ROOT / "config/product_search/career_profile.v2.yaml",
        "policy_sha256": REPO_ROOT / "config/product_search/evidence_synthesis.v1.yaml",
    }
    identity = {name: _sha256_file(path) for name, path in paths.items()}
    identity.update({
        "corpus_manifest_sha256": corpus_sha256,
        "provider_output_schema_sha256": provider_output_schema_sha256(),
        "provider_output_schema_version": OUTPUT_SCHEMA_VERSION,
        "semantic_prompt_sha256": _sha256_bytes(semantic_prompt.encode("utf-8")),
        "semantic_prompt_version": policy.semantic_prompt_version,
        "task10_prompt_version": policy.prompt_version,
        "task10_prompt_sha256": task10_prompt_sha256(policy),
        "model_id": policy.model_id,
        "model_sha256": _sha256_bytes(policy.model_id.encode("utf-8")),
    })
    return identity


def expected_record_approval_token(identity: Mapping[str, str]) -> str:
    return "approve-gate-b-record:" + _sha256_json(dict(identity))


def build_dry_run_preflight(
    *, gate_a_root: Path, output_root: Path, sample_size: int = DEFAULT_SAMPLE_SIZE
) -> dict[str, Any]:
    if sample_size <= 0:
        raise GateBPreflightError("invalid_sample_size")
    gate_a_root = gate_a_root.resolve()
    output_root = output_root.resolve()
    records, protected_paths = _load_gate_a(gate_a_root)
    before = snapshot_paths(protected_paths)
    selected = _corpus_records(records, sample_size)
    corpus = {
        "schema_version": "1.0.0",
        "gate": "gate-b",
        "gate_a": {
            "run_id": GATE_A_RUN_ID,
            "commit": GATE_A_COMMIT,
            "manifest_sha256": GATE_A_MANIFEST_SHA256,
        },
        "selection": {
            "algorithm": "deterministic-coverage-first-stratified-round-robin-v1",
            "denominator": 1314,
            "sample_size": sample_size,
            "core_exploration_values_are_sampling_hypotheses_not_decision_outputs": True,
        },
        "records": selected,
    }
    corpus_bytes = (
        json.dumps(corpus, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    corpus_sha256 = _sha256_bytes(corpus_bytes)
    experiment_root = output_root / corpus_sha256
    manifest_path = experiment_root / "corpus-manifest.json"
    experiment_root.mkdir(parents=True, exist_ok=True)
    if manifest_path.exists():
        if manifest_path.read_bytes() != corpus_bytes:
            raise GateBPreflightError("content_address_collision")
    else:
        manifest_path.write_bytes(corpus_bytes)
    assert_paths_unchanged(before, snapshot_paths(protected_paths))

    coverage = {
        "lanes": sorted({item["lane"] for item in selected}),
        "source_families": sorted({item["source_family"] for item in selected}),
        "role_patterns": sorted({item["role_pattern"] for item in selected}),
        "companies": len({item["company"] for item in selected}),
        "origins": ["open_market"],
        "strategic_watchlist_available": False,
        "sampling_case_types": sorted({
            item["sampling_case_type"] for item in selected
        }),
    }
    max_spend = DEFAULT_MAX_COST_PER_CALL_USD * sample_size
    identity = _record_identity(corpus_sha256)
    return {
        "schema_version": "1.0.0",
        "status": "ready_for_record_approval",
        "gate_a": {
            "run_id": GATE_A_RUN_ID,
            "commit": GATE_A_COMMIT,
            "manifest_sha256": GATE_A_MANIFEST_SHA256,
            "raw_observed": 2414,
            "corrected_canonical_current": 1814,
            "minimum_evidence_sufficient": 1314,
            "minimum_evidence_is_not_qualified": True,
        },
        "corpus": {
            "status": "materialized",
            "selection_denominator": 1314,
            "selected_count": sample_size,
            "manifest_path": str(manifest_path),
            "manifest_sha256": corpus_sha256,
            "coverage": coverage,
        },
        "budget": {
            "estimated_calls": sample_size,
            "max_cost_per_call_usd": f"{DEFAULT_MAX_COST_PER_CALL_USD:.2f}",
            "maximum_spend_usd": f"{max_spend:.2f}",
        },
        "record_identity": identity,
        "approval_token_sha256": _sha256_bytes(
            expected_record_approval_token(identity).encode("utf-8")
        ),
        "record_authorized": False,
        "task_13_authorized": False,
        "provider": {"calls_attempted": 0, "network_enabled": False},
        "side_effects": {
            "forbidden_mutations": 0,
            "slack_credentials_accessed": 0,
            "slack_calls": 0,
            "production_writes": 0,
            "runtime_mutations": 0,
            "gate_a_mutations": 0,
        },
    }


def authorize_record_run(
    preflight: Mapping[str, Any],
    *,
    supplied_identity: Mapping[str, str],
    approval_token: str | None,
    call_cap: int,
    spend_cap_usd: str,
) -> dict[str, Any]:
    expected_identity = preflight.get("record_identity")
    if dict(supplied_identity) != expected_identity:
        raise GateBPreflightError("record_identity_mismatch")
    if approval_token != expected_record_approval_token(supplied_identity):
        raise GateBPreflightError("approval_token_missing_or_mismatch")
    estimated_calls = int(preflight["budget"]["estimated_calls"])
    max_spend = Decimal(preflight["budget"]["maximum_spend_usd"])
    if call_cap < estimated_calls:
        raise GateBPreflightError("call_cap_below_estimate")
    if Decimal(spend_cap_usd) < max_spend:
        raise GateBPreflightError("spend_cap_below_estimate")
    return {
        "record_authorized": True,
        "provider_calls_started": False,
        "record_identity": dict(supplied_identity),
        "call_cap": call_cap,
        "spend_cap_usd": str(Decimal(spend_cap_usd)),
    }
