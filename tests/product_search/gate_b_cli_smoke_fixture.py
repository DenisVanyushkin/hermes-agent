from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
from typing import Any

from job_intel.product_search import gate_b_evidence_v3 as evidence
from job_intel.product_search.gate_b_evidence_v3 import (
    CompanyEvidenceCatalogV3,
    ReviewedFragmentAllowlistV3,
    ReviewedFragmentDecisionV3,
    ReviewedFragmentEntryV3,
    load_company_evidence_bundle,
)
from job_intel.product_search.gate_b_evidence_runner_v1 import (
    AuthorityIdentity,
    EvidenceManifest,
    EvidenceManifestRow,
    Limits,
    RuntimeIdentity,
)
from job_intel.product_search.gate_b_runtime_v1 import (
    AuthorityInputs,
    _authority_identity,
    assert_artifact_destination_safe,
)
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import LLMProviderError


CORPUS_SHA256 = "b1db802dbb3d0e2a18771f32da12b901b3bb9e941ae71b785a3c71142abf2d69"
GATE_A_RUN_ID = "gate-a-20260816T141344Z"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _allowlist(entries: list[ReviewedFragmentEntryV3]) -> ReviewedFragmentAllowlistV3:
    return ReviewedFragmentAllowlistV3(
        schema_version="3.0.0",
        gate_a_run_id=GATE_A_RUN_ID,
        gate_b_corpus_sha256=CORPUS_SHA256,
        entries=tuple(entries),
    )


def _make_record(index: int) -> tuple[dict[str, str], dict[str, str]]:
    return (
        {"selection_key": f"{index + 1:064x}"},
        {
            "company": "Northstar",
            "title": f"Head of Product {index}",
            "location": "Almaty",
            "description": (
                "<h2>Responsibilities</h2>"
                f"<p>Lead roadmap planning for product lane {index}.</p>"
            ),
        },
    )


def _provider_payload(synthesis_input: Any) -> dict[str, object]:
    claims: list[dict[str, object]] = []
    for dimension in evidence.EvidenceDimension:
        fragment = next(
            item
            for item in synthesis_input.fragments
            if any(claim.dimension is dimension for claim in item.allowed_claims)
        )
        allowed = next(
            claim for claim in fragment.allowed_claims if claim.dimension is dimension
        )
        claims.append(
            {
                "claim_id": f"claim:{dimension.value}",
                "dimension": dimension.value,
                "status": allowed.status.value,
                "claim_code": allowed.claim_code,
                "statement": allowed.statement,
                "citations": [fragment.fragment_id],
            }
        )
    return {
        "schema_version": "2.0.0",
        "claims": claims,
        "conflicts": [],
        "question_candidates": [],
    }


_seed_record, _seed_raw = _make_record(999)
_seed_candidates = evidence.build_vacancy_projection_candidates_v3(_seed_record, _seed_raw)
_seed_entries = [
    ReviewedFragmentEntryV3(
        selection_key=_seed_candidates.selection_key,
        vacancy_artifact_sha256=_seed_candidates.vacancy_artifact_sha256,
        source_locator=item.source_locator,
        text_sha256=item.text_sha256,
        decision=ReviewedFragmentDecisionV3.ALLOW_ROLE_RESPONSIBILITY,
        reviewer_role="independent_gate_b_evidence_reviewer",
        reviewed_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
    )
    for item in _seed_candidates.description_candidates
]
_SEED_INPUT = evidence.project_vacancy_evidence_v3(
    _seed_record, _seed_raw, _allowlist(_seed_entries)
)
_SEED_PROVIDER_PAYLOAD = _provider_payload(_SEED_INPUT)


def _provider_payload_from_serialized(payload: dict[str, object]) -> dict[str, object]:
    claims: list[dict[str, object]] = []
    for fragment in payload.get("fragments", []):
        if not isinstance(fragment, dict):
            continue
        fragment_id = fragment.get("fragment_id")
        for allowed in fragment.get("allowed_claims", []):
            if not isinstance(allowed, dict):
                continue
            dimension = allowed.get("dimension")
            if any(claim["dimension"] == dimension for claim in claims):
                continue
            claims.append(
                {
                    "claim_id": f"claim:{dimension}",
                    "dimension": dimension,
                    "status": allowed.get("status"),
                    "claim_code": allowed.get("claim_code"),
                    "statement": allowed.get("statement"),
                    "citations": [fragment_id],
                }
            )
    return {
        "schema_version": "2.0.0",
        "claims": claims,
        "conflicts": [],
        "question_candidates": [],
    }


class FakeProvider:
    def __init__(self) -> None:
        self.store = SimpleNamespace(records={})
        self.dispatch_count = 0
        self.dispatch_inputs: list[str] = []
        self.dispatch_log_path = os.environ.get("GATE_B_SMOKE_DISPATCH_LOG")
        self.pricing = SimpleNamespace(
            identity_sha256=_sha(b"pricing:smoke"),
            reservation_cost_usd=Decimal("0.01"),
        )
        self.authority_identity = {
            "provider_sha256": _sha(b"provider:smoke"),
            "model_sha256": _sha(b"model:smoke"),
            "prompt_sha256": _sha(b"prompt:smoke"),
            "response_schema_sha256": _sha(b"schema:smoke"),
            "pricing_sha256": _sha(b"pricing:smoke"),
        }

        def load(input_hash: str) -> dict[str, object]:
            return self.store.records[input_hash]

        self.store.load = load

    def dispatch(
        self,
        payload: dict[str, object],
        *,
        input_hash: str,
        capability: object,
    ) -> object:
        reservation = capability.reserve(input_hash)
        capability.mark_dispatching(reservation)
        ordinal = self.dispatch_count
        self.dispatch_count += 1
        self.dispatch_inputs.append(input_hash)
        if ordinal % 10 == 0:
            outcome = "terminal_unknown"
            raw_response_text = ""
        elif ordinal % 2 == 0:
            outcome = "success"
            raw_response_text = json.dumps(
                _provider_payload_from_serialized(payload),
                sort_keys=True,
                separators=(",", ":"),
            )
        else:
            outcome = "terminal_failure"
            raw_response_text = "{}"
        record = {
            "provider_id": "fake-provider",
            "model_id": "fake-model",
            "provider_sha256": _sha(b"provider:smoke"),
            "model_sha256": _sha(b"model:smoke"),
            "prompt_sha256": _sha(b"prompt:smoke"),
            "structured_prompt_sha256": _sha(b"prompt:smoke"),
            "response_schema_sha256": _sha(b"schema:smoke"),
            "raw_response_text": raw_response_text,
            "post_dispatch_outcome_v3": outcome,
            "measured_cost_usd": "0",
            "conservative_cost_usd": "0.01",
            "pricing_sha256": _sha(b"pricing:smoke"),
        }
        self.store.records[input_hash] = record
        capability.reconcile(reservation, Decimal("0"), outcome)
        extra_dispatch_refused = None
        if (
            os.environ.get("GATE_B_SMOKE_PROBE_CAP") == "1"
            and ordinal == 47
        ):
            try:
                extra_reservation = capability.reserve(input_hash)
                capability.mark_dispatching(extra_reservation)
            except ValueError as exc:
                extra_dispatch_refused = str(exc)
            else:
                extra_dispatch_refused = "not_refused"
        if self.dispatch_log_path:
            Path(self.dispatch_log_path).write_bytes(
                _canonical(
                    {
                        "dispatch_count": self.dispatch_count,
                        "dispatch_inputs": self.dispatch_inputs,
                        "extra_dispatch_refused": extra_dispatch_refused,
                    }
                )
            )
        if outcome == "terminal_failure":
            raise LLMProviderError("schema_invalid", "composition smoke fixture")
        return SimpleNamespace(record=record)


def _record_isolation_probe() -> None:
    destination = os.environ.get("GATE_B_SMOKE_ISOLATION_PROBE")
    if not destination:
        return
    protected_paths = (
        "/home/hermes/.hermes/state.db",
        "/var/lib/job-intel/state",
        "/home/hermes/.cache",
        "/var/lib/browser-desktop/profiles",
        "/home/hermes/.hermes/sessions",
    )
    observed: dict[str, dict[str, object]] = {}
    for raw_path in protected_paths:
        try:
            flags = os.O_RDONLY | (os.O_DIRECTORY if Path(raw_path).is_dir() else 0)
            descriptor = os.open(raw_path, flags)
        except OSError as exc:
            observed[raw_path] = {"reachable": False, "errno": exc.errno}
        else:
            os.close(descriptor)
            observed[raw_path] = {"reachable": True, "errno": None}
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_canonical(observed))


def provider_factory() -> FakeProvider:
    _record_isolation_probe()
    return FakeProvider()


def decision_request_factory(context: Any) -> Any:
    from tests.product_search.test_gate_b_evidence_skeleton import _decision_result

    payload = context.response_payload
    ref = context.manifest_ref

    if isinstance(payload.get("claims"), list) and payload["claims"]:
        decision_payload = payload
    else:
        decision_payload = {"claims": []}
    return _decision_result(decision_payload, ref.input_sha256)


def prepare(*, root: Path, artifact_root: Path, repo_root: Path) -> tuple[Path, Path, str]:
    assert_artifact_destination_safe(artifact_root)
    root.mkdir(parents=True, exist_ok=True)
    authority_root = artifact_root / "authority"
    authority_root.mkdir(parents=True)
    company_evidence_root = root / "company-evidence"
    shutil.copytree(
        repo_root / "tests/product_search/fixtures/company_evidence",
        company_evidence_root,
        dirs_exist_ok=True,
    )
    company_bundle = load_company_evidence_bundle(
        company_evidence_root / "company-evidence-bundle.v1.yaml"
    )
    company_catalog = CompanyEvidenceCatalogV3(
        company_evidence_contract_sha256=_sha(
            (repo_root / "config/product_search/company_evidence_contract.v1.yaml").read_bytes()
        ),
        bundles=(company_bundle,),
    )
    policy_path = artifact_root / "authority/decision_contract.v2.yaml"
    policy_path.write_bytes(
        (repo_root / "config/product_search/decision_contract.v2.yaml").read_bytes()
    )
    authority_values = {
        "model_bytes": b"model:smoke",
        "prompt_bytes": b"prompt:smoke",
        "response_schema_bytes": b"schema:smoke",
        "profile_bytes": b"profile:smoke",
        "policy_bytes": policy_path.read_bytes(),
        "decision_v2_bytes": b"decision-v2:smoke",
        "pricing_bytes": b"pricing:smoke",
        "source:gate_a": b"gate-a:smoke",
        "source:provider": b"provider:smoke",
        "source:company_evidence_contract": (
            repo_root / "config/product_search/company_evidence_contract.v1.yaml"
        ).read_bytes(),
    }
    authority_paths: dict[str, Path] = {}
    for name, value in authority_values.items():
        filename = name.replace(":", "-") + ".bin"
        path = authority_root / filename
        path.write_bytes(value)
        authority_paths[name] = path
    authorities = AuthorityInputs(
        model_bytes=authority_values["model_bytes"],
        prompt_bytes=authority_values["prompt_bytes"],
        response_schema_bytes=authority_values["response_schema_bytes"],
        profile_bytes=authority_values["profile_bytes"],
        policy_bytes=authority_values["policy_bytes"],
        decision_v2_bytes=authority_values["decision_v2_bytes"],
        pricing_bytes=authority_values["pricing_bytes"],
        source_authority_bytes={
            "gate_a": authority_values["source:gate_a"],
            "provider": authority_values["source:provider"],
            "company_evidence_contract": authority_values[
                "source:company_evidence_contract"
            ],
        },
    )
    shim = artifact_root / "python-runtime/venv/lib/python3.12/site-packages/00-pysqlite3-shim.pth"
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text("# smoke shim\n", encoding="utf-8")
    interpreter = artifact_root / "python-runtime/venv/bin/python"
    interpreter.parent.mkdir(parents=True, exist_ok=True)
    interpreter.write_text(
        f'''#!/usr/bin/env bash
if [[ "$1" == "-c" && "$2" == *"sysconfig.get_paths()['purelib']"* ]]; then
  printf '%s\\n' "$PYTHONHOME/lib/python3.12/site-packages"
  exit 0
fi
exec {sys.executable!s} "$@"
''',
        encoding="utf-8",
    )
    interpreter.chmod(0o755)
    runtime_identity = RuntimeIdentity(
        artifact_sha256="1" * 64,
        artifact_tree_sha256=artifact_root.name,
        shim_sha256=_sha(shim.read_bytes()),
        interpreter_sha256=_sha(interpreter.read_bytes()),
        stdlib_inventory_sha256="3" * 64,
        installed_distributions_sha256="4" * 64,
        installed_files_sha256="5" * 64,
        sys_path_sha256="6" * 64,
        native_extensions_sha256="7" * 64,
        shared_libraries_sha256="8" * 64,
    )
    authority_identity: AuthorityIdentity = _authority_identity(authorities)
    allow_entries: list[ReviewedFragmentEntryV3] = []
    rows: list[EvidenceManifestRow] = []
    corpus_rows: list[dict[str, object]] = []
    allowlist = None
    for index in range(48):
        record, raw = _make_record(index)
        candidates = evidence.build_vacancy_projection_candidates_v3(record, raw)
        entries = [
            ReviewedFragmentEntryV3(
                selection_key=candidates.selection_key,
                vacancy_artifact_sha256=candidates.vacancy_artifact_sha256,
                source_locator=item.source_locator,
                text_sha256=item.text_sha256,
                decision=ReviewedFragmentDecisionV3.ALLOW_ROLE_RESPONSIBILITY,
                reviewer_role="independent_gate_b_evidence_reviewer",
                reviewed_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
            )
            for item in candidates.description_candidates
        ]
        allow_entries.extend(entries)
        allowlist = _allowlist(allow_entries)
        projected = evidence.project_vacancy_evidence_v3(
            record,
            raw,
            allowlist,
            company_evidence_catalog=company_catalog,
        )
        rows.append(
            EvidenceManifestRow(
                ordinal=index,
                corpus_key=f"row-{index}",
                raw_sha256=_sha(_canonical(raw)),
                input_sha256=_sha(_canonical(projected.provider_payload())),
                projection_sha256=_sha(_canonical(projected.model_dump(mode="json"))),
            )
        )
        corpus_rows.append({"ordinal": index, "record": record, "raw": raw})
    assert allowlist is not None
    allowlist_path = root / "reviewed-allowlist.json"
    allowlist_path.write_bytes(_canonical(allowlist.model_dump(mode="json")))
    corpus_path = root / "corpus-rows.json"
    corpus_path.write_bytes(_canonical(corpus_rows))
    payload: dict[str, object] = {
        "schema_version": "gate-b-evidence-manifest-v1",
        "run_id": "gate-b-evidence-v1-0123456789abcdef",
        "created_at": "2026-08-22T12:00:00Z",
        "decision_clock": "2026-08-22T12:00:00Z",
        "benchmark_kind": "gate_b_description_evidence",
        "row_count": 48,
        "rows": [row.model_dump(mode="json") for row in rows],
        "runtime": runtime_identity.model_dump(mode="json"),
        "authorities": authority_identity.model_dump(mode="json"),
        "limits": Limits(
            ordered_call_cap=48,
            per_call_maximum_usd="0.01",
            aggregate_maximum_usd="0.48",
        ).model_dump(mode="json"),
    }
    identity = dict(payload)
    identity.pop("created_at")
    payload["manifest_sha256"] = _sha(_canonical(identity))
    manifest_path = root / "evidence-manifest.json"
    manifest_bytes = _canonical(payload)
    manifest_path.write_bytes(manifest_bytes)
    runtime_manifest = {
        "artifact_sha256": runtime_identity.artifact_sha256,
        "artifact_tree_sha256": runtime_identity.artifact_tree_sha256,
        "candidate_commit": "0" * 40,
        "python_version": "3.12.13",
        "shim_sha256": runtime_identity.shim_sha256,
        "python_executable_sha256": runtime_identity.interpreter_sha256,
        "stdlib_tree_sha256": runtime_identity.stdlib_inventory_sha256,
        "installed_distributions_sha256": runtime_identity.installed_distributions_sha256,
        "installed_files_sha256": runtime_identity.installed_files_sha256,
        "sys_path_sha256": runtime_identity.sys_path_sha256,
        "native_extensions_sha256": runtime_identity.native_extensions_sha256,
        "shared_libraries_sha256": runtime_identity.shared_libraries_sha256,
        "shared_library_provenance": runtime_identity.shared_library_provenance,
    }
    (artifact_root / "runtime-manifest.json").write_bytes(_canonical(runtime_manifest))
    (artifact_root / "runtime-manifest.sha256").write_text(
        _sha(_canonical(runtime_manifest)) + "\n", encoding="ascii"
    )
    config = {
        "manifest_path": str(manifest_path),
        "corpus_rows_path": str(corpus_path),
        "reviewed_allowlist_path": str(allowlist_path),
        "decision_policy_path": str(policy_path),
        "company_evidence_root": str(company_evidence_root),
        "provider_factory": "gate_b_cli_smoke_fixture:provider_factory",
        "decision_request_factory": "job_intel.product_search.gate_b_evidence_runner_v1:build_decision_request_from_context_v2",
        "authority_paths": {key: str(path) for key, path in authority_paths.items()},
    }
    config_path = root / "collection-config.json"
    config_path.write_bytes(_canonical(config))
    return manifest_path, config_path, _sha(manifest_bytes)
