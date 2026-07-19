"""Provider-agnostic benchmark runner (Step 5B, Slice 5B-1).

One reusable path: dataset x provider -> per-case results + a manifest,
through the SAME semantic runtime and (optionally) the SAME decision engine
every other caller uses. Provider selection happens only at
provider_registry.build_benchmark_provider() — nothing below that boundary
branches on provider_id (Provider Contract 1.0.0 §9).

Aggregation (percentiles, cost formulas, precision/recall) is explicitly
OUT of scope here — Slice 5B-2. This module only produces
result-completeness-validating per-case rows and an identity-checked
manifest.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from job_intel.shadow_evaluator.contract import CONTRACT_PATH as DECISION_CONTRACT_PATH
from job_intel.shadow_evaluator.contract import load_contract as load_decision_contract
from job_intel.shadow_evaluator.engine import evaluate as evaluate_decision
from job_intel.shadow_evaluator.policy import RuntimePolicy, load_policy
from job_intel.vacancy_understanding.extractor import RawVacancy
from job_intel.vacancy_understanding.extractor import extract as det_extract
from job_intel.vacancy_understanding.model import VacancyUnderstanding
from job_intel.vacancy_understanding.semantic.contract import (
    SemanticFactContract,
    load_semantic_contract,
)
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import LLMProviderError
from job_intel.vacancy_understanding.semantic.runtime.models import RUNTIME_VERSION
from job_intel.vacancy_understanding.semantic.runtime.pipeline import extract_semantic

from .aggregate import aggregate_run
from .hashing import sha256_file, sha256_json
from .models import (
    RUNNER_VERSION,
    BenchmarkCaseResult,
    BenchmarkManifest,
    CaseStatus,
    LatencyMode,
    NumericState,
)
from .provider_registry import build_benchmark_provider

METRIC_CONTRACT_PATH = (
    Path(__file__).resolve().parents[4]
    / "docs/job-intel-improvements/jul19/step5b-benchmark-contract.md"
)
FIXED_EXTRACTION_TS = datetime(2026, 7, 19, tzinfo=timezone.utc)


class ResumeBlocked(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_commit(repo_root: Path) -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root,
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _relpath(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)  # output dir outside the repo (e.g. tests, /tmp) — absolute path is still stable


def dataset_hash(cases: list[dict[str, Any]]) -> str:
    return sha256_json(sorted(
        [{"case_id": c["case_id"], "vacancy_key": c["vacancy_key"],
          "title": c["title"], "text": c.get("text") or ""} for c in cases],
        key=lambda c: c["case_id"]))


def _atomic_write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True))
    os.replace(tmp, path)


def _load_manifest(path: Path) -> Optional[BenchmarkManifest]:
    if not path.exists():
        return None
    return BenchmarkManifest.model_validate(json.loads(path.read_text()))


def _load_existing_result(path: Path) -> Optional[BenchmarkCaseResult]:
    if not path.exists():
        return None
    try:
        return BenchmarkCaseResult.model_validate(json.loads(path.read_text()))
    except Exception:
        return None  # corrupt/partial: treated as absent -> re-run


def _build_manifest(
    *, benchmark_id: str, run_id: str, provider_identity: dict[str, Any],
    dataset_id: str, cases: list[dict[str, Any]], contract: SemanticFactContract,
    repo_root: Path,
) -> BenchmarkManifest:
    dc = load_decision_contract()
    return BenchmarkManifest(
        benchmark_id=benchmark_id, run_id=run_id, created_at=_now_iso(),
        git_commit=_git_commit(repo_root),
        provider_id=provider_identity["provider_id"],
        provider_version=provider_identity["provider_version"],
        provider_config_hash=provider_identity["provider_config_hash"],
        prompt_version=provider_identity["prompt_version"],
        model_requested=provider_identity["model_requested"],
        model_actual=provider_identity["model_actual"],
        transport=provider_identity["transport"],
        dataset_id=dataset_id, dataset_hash=dataset_hash(cases), dataset_size=len(cases),
        semantic_contract_version=contract.metadata.contract_version,
        runtime_version=RUNTIME_VERSION,
        decision_sot_version=dc.metadata.contract_version,
        runner_version=RUNNER_VERSION,
        recording_format_version=provider_identity["recording_format_version"],
        temperature=provider_identity["temperature"],
        retry_policy=provider_identity["retry_policy"],
        fallback_policy=provider_identity["fallback_policy"],
        metric_contract_path=_relpath(METRIC_CONTRACT_PATH, repo_root),
        metric_contract_hash=sha256_file(METRIC_CONTRACT_PATH),
        decision_matrix_path=_relpath(DECISION_CONTRACT_PATH, repo_root),
        decision_matrix_hash=sha256_file(DECISION_CONTRACT_PATH),
        price_input_usd_per_mtok=(provider_identity.get("pricing") or {}).get(
            "input_usd_per_mtok"),
        price_output_usd_per_mtok=(provider_identity.get("pricing") or {}).get(
            "output_usd_per_mtok"),
        pricing_source=(provider_identity.get("pricing") or {}).get("source"),
    )


_IDENTITY_FIELDS = (
    "dataset_hash", "provider_id", "provider_version", "provider_config_hash",
    "prompt_version", "metric_contract_hash", "decision_matrix_hash",
)


def _check_resume_identity(existing: BenchmarkManifest, fresh: BenchmarkManifest) -> None:
    mismatches = [f for f in _IDENTITY_FIELDS if getattr(existing, f) != getattr(fresh, f)]
    if mismatches:
        raise ResumeBlocked(
            f"resume blocked: identity mismatch on {mismatches} "
            f"(existing benchmark_id={existing.benchmark_id})")


def run_benchmark_case(
    *, provider: Any, case: dict[str, Any], contract: SemanticFactContract,
    policy: Optional[RuntimePolicy], latency_mode: LatencyMode,
) -> tuple[Optional[Any], Optional[Any], float, Optional[LLMProviderError]]:
    """Run ONE case through the common downstream path. Returns
    (SemanticExtraction | None, Evaluation | None, latency_ms, error)."""
    started = time.monotonic()
    vu = det_extract(RawVacancy(
        vacancy_key=case["vacancy_key"], source_system=case.get("source_system", "benchmark"),
        company=case.get("company", "Benchmark"), title=case["title"],
        location=case.get("location", "Unknown"), description=case.get("text") or ""),
        created_at=FIXED_EXTRACTION_TS)
    try:
        sem = extract_semantic(vu, title=case["title"], text=case.get("text") or "",
                               provider=provider, contract=contract)
    except LLMProviderError as exc:
        return None, None, (time.monotonic() - started) * 1000, exc
    decision = None
    if policy is not None:
        try:
            enriched = VacancyUnderstanding.model_validate(sem.fragment)
            decision = evaluate_decision(enriched, policy=policy, evaluated_at=FIXED_EXTRACTION_TS)
        except Exception:
            decision = None  # semantic result still valid; decision path is optional downstream proof
    return sem, decision, (time.monotonic() - started) * 1000, None


def _case_cost(
    provider_identity: dict[str, Any],
    input_tokens: Optional[int], output_tokens: Optional[int],
) -> tuple[Optional[float], NumericState]:
    if provider_identity["cost_known_zero"]:
        return 0.0, NumericState.known_zero
    pricing = provider_identity.get("pricing")
    if pricing and input_tokens is not None and output_tokens is not None:
        cost = (input_tokens * pricing["input_usd_per_mtok"]
                + output_tokens * pricing["output_usd_per_mtok"]) / 1_000_000
        return cost, NumericState.known_value
    # Cost SHOULD be measurable for this provider but either the recorded
    # usage or the run's pricing is missing — that is `unknown`, never a
    # silent zero (contract §6).
    return None, NumericState.unknown


def run_benchmark(
    *, benchmark_id: str, run_id: str, provider_spec: dict[str, Any],
    dataset_id: str, cases: list[dict[str, Any]], out_dir: Path,
    evaluate_downstream_decision: bool = True, force: bool = False,
    max_new_cases: Optional[int] = None,
) -> tuple[BenchmarkManifest, list[BenchmarkCaseResult]]:
    """max_new_cases caps how many NOT-yet-persisted cases this call may
    execute (cached rows don't count) — the budget hook for paid runs:
    callers execute in bounded chunks and check spend between calls, while
    manifest identity stays pinned to the FULL dataset."""
    repo_root = Path(__file__).resolve().parents[4]
    contract = load_semantic_contract()
    provider, identity = build_benchmark_provider(provider_spec, contract=contract)
    policy = load_policy() if evaluate_downstream_decision else None
    latency_mode = LatencyMode(identity["latency_mode"])

    manifest_path = out_dir / "manifest.json"
    fresh_manifest = _build_manifest(
        benchmark_id=benchmark_id, run_id=run_id, provider_identity=identity,
        dataset_id=dataset_id, cases=cases, contract=contract, repo_root=repo_root)

    existing = _load_manifest(manifest_path)
    if existing is not None:
        _check_resume_identity(existing, fresh_manifest)
        manifest = existing
    else:
        _atomic_write_json(manifest_path, fresh_manifest.model_dump(mode="json"))
        manifest = fresh_manifest

    cases_dir = out_dir / "cases"
    dumps_dir = out_dir / "semantic_dumps"
    decisions_dir = out_dir / "decisions"
    results: list[BenchmarkCaseResult] = []
    new_executed = 0

    for case in cases:
        case_id = case["case_id"]
        result_path = cases_dir / f"{case_id}.result.json"
        if not force:
            cached = _load_existing_result(result_path)
            if cached is not None:
                results.append(cached)
                continue
        if max_new_cases is not None and new_executed >= max_new_cases:
            break
        new_executed += 1

        started_at = _now_iso()
        sem, decision, latency_ms, error = run_benchmark_case(
            provider=provider, case=case, contract=contract, policy=policy,
            latency_mode=latency_mode)
        completed_at = _now_iso()

        if error is not None:
            result = BenchmarkCaseResult(
                benchmark_id=benchmark_id, run_id=run_id, case_id=case_id,
                vacancy_key=case["vacancy_key"], provider_id=identity["provider_id"],
                status=CaseStatus.failed,
                observations_emitted=0, observations_accepted=0, observations_rejected=0,
                latency_ms=latency_ms, latency_mode=latency_mode,
                live_latency_state=(NumericState.unknown
                                    if identity["reports_usage_metadata"]
                                    else NumericState.not_applicable),
                cost_state=(NumericState.known_zero if identity["cost_known_zero"]
                            else NumericState.unknown),
                cost_usd=0.0 if identity["cost_known_zero"] else None,
                error_code=error.reason,
                started_at=started_at, completed_at=completed_at,
            )
        else:
            dump_path = dumps_dir / f"{case_id}.semantic.json"
            _atomic_write_json(dump_path, sem.semantic_dump())
            decision_path = None
            if decision is not None:
                decision_path = decisions_dir / f"{case_id}.decision.json"
                _atomic_write_json(decision_path, decision.semantic_dump())
            recording_path = None
            live_latency_ms: Optional[float] = None
            live_latency_state = NumericState.not_applicable
            if identity["reports_usage_metadata"]:
                meta = getattr(provider, "last_call_metadata", {}) or {}
                usage = meta.get("usage") or {}
                input_tokens = usage.get("prompt_tokens")
                output_tokens = usage.get("completion_tokens")
                rec_latency = meta.get("latency_ms")
                if rec_latency is not None:
                    live_latency_ms = float(rec_latency)
                    live_latency_state = NumericState.known_value
                else:
                    live_latency_state = NumericState.unknown
                input_hash = meta.get("input_hash")
                if input_hash is not None:
                    rp = provider.store.path_for(input_hash)
                    recording_path = _relpath(rp, repo_root)
            else:
                input_tokens = output_tokens = 0
            cost_usd, cost_state = _case_cost(identity, input_tokens, output_tokens)

            result = BenchmarkCaseResult(
                benchmark_id=benchmark_id, run_id=run_id, case_id=case_id,
                vacancy_key=case["vacancy_key"], provider_id=identity["provider_id"],
                status=CaseStatus.ok,
                observations_emitted=sem.diagnostics.observations_total,
                observations_accepted=(sem.diagnostics.observations_total
                                       - sem.diagnostics.observations_rejected),
                observations_rejected=sem.diagnostics.observations_rejected,
                rejection_codes=sorted({r.reason for r in sem.rejected_observations}),
                semantic_hash=hashlib.sha256(
                    json.dumps(sem.semantic_dump(), sort_keys=True).encode()).hexdigest(),
                semantic_dump_path=_relpath(dump_path, repo_root),
                decision_output_path=(_relpath(decision_path, repo_root)
                                      if decision_path else None),
                latency_ms=latency_ms, latency_mode=latency_mode,
                live_latency_ms=live_latency_ms, live_latency_state=live_latency_state,
                input_tokens=input_tokens, output_tokens=output_tokens,
                cost_usd=cost_usd, cost_state=cost_state,
                recording_path=recording_path,
                started_at=started_at, completed_at=completed_at,
            )
        _atomic_write_json(result_path, result.model_dump(mode="json"))
        results.append(result)

    # Aggregate strictly from the persisted rows just written/skipped —
    # NEVER from the in-memory `results` list (resume must not double-count).
    aggregate_run(out_dir)
    return manifest, results
