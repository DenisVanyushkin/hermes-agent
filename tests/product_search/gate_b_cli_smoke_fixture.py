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
    GateBDispatchRequestV2,
    Limits,
    RuntimeIdentity,
)
from job_intel.product_search.gate_b_runtime_v1 import (
    AuthorityInputs,
    _authority_identity,
    assert_artifact_destination_safe,
)
from job_intel.product_search.gate_b_spend_record_v1 import SpendRecordStore
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import LLMProviderError


GATE_A_RUN_ID = "gate-a-20260816T141344Z"
CORPUS_AUTHORITY_SCHEMA = "gate-b-corpus-authority-v1"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _allowlist(
    entries: list[ReviewedFragmentEntryV3],
    corpus_sha256: str = "0" * 64,
) -> ReviewedFragmentAllowlistV3:
    return ReviewedFragmentAllowlistV3(
        schema_version="3.0.0",
        gate_a_run_id=GATE_A_RUN_ID,
        gate_b_corpus_sha256=corpus_sha256,
        entries=tuple(entries),
    )


def _load_corpus_authority(
    path: Path,
    *,
    expected_corpus_sha256: str,
) -> str:
    """Load and verify the machine-readable corpus authority."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("corpus_authority_unavailable")
    try:
        encoded = path.read_bytes()
        payload = json.loads(encoded)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("corpus_authority_invalid") from exc
    if _canonical(payload) != encoded:
        raise ValueError("corpus_authority_noncanonical")
    if not isinstance(payload, dict):
        raise ValueError("corpus_authority_invalid")
    if payload.get("schema_version") != CORPUS_AUTHORITY_SCHEMA:
        raise ValueError("corpus_authority_schema_mismatch")
    declared = payload.get("corpus_sha256")
    if not isinstance(declared, str) or len(declared) != 64:
        raise ValueError("corpus_authority_invalid")
    if declared != expected_corpus_sha256:
        raise ValueError("corpus_authority_mismatch")
    return declared


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
        request: GateBDispatchRequestV2,
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
                _provider_payload_from_serialized(dict(request.provider_payload)),
                sort_keys=True,
                separators=(",", ":"),
            )
        else:
            outcome = "terminal_failure"
            raw_response_text = "{}"
        provider_input_hash = _sha(_canonical(dict(request.provider_payload)))
        if raw_response_text:
            output_sha256 = _sha(_canonical(json.loads(raw_response_text)))
        else:
            output_sha256 = _sha(b"")
        record = {
            "input_hash": input_hash,
            "input_payload_sha256": provider_input_hash,
            "semantic_input_sha256": provider_input_hash,
            "provider_input_sha256": provider_input_hash,
            "input": request.synthesis_input.model_dump(mode="json"),
            "provider_id": "fake-provider",
            "provider_version": "product-search-evidence-replay/2.0",
            "model_id": "fake-model",
            "requested_model": "fake-model",
            "response_model": "fake-model",
            "semantic_prompt_version": "product-search-evidence-synthesis-2.0.0",
            "prompt_version": "product-search-evidence-synthesis-2.0.0",
            "schema_version": "2.0.0",
            "output_sha256": output_sha256,
            "provider_sha256": _sha(b"provider:smoke"),
            "model_sha256": _sha(b"model:smoke"),
            "prompt_sha256": _sha(b"prompt:smoke"),
            "structured_prompt_sha256": _sha(b"prompt:smoke"),
            "response_schema_sha256": _sha(b"schema:smoke"),
            "raw_response_text": raw_response_text,
            "post_dispatch_outcome_v3": outcome,
            "status": "success" if outcome == "success" else outcome,
            "failure_code": None if outcome == "success" else outcome,
            "failure_diagnostic": None,
            "latency_ms": 1,
            "cost_usd": "0",
            "measured_cost_usd": "0",
            "conservative_cost_usd": "0.01",
            "pricing_sha256": _sha(b"pricing:smoke"),
            "provider_authority_identity": dict(self.authority_identity),
            "pricing": {
                "identity_sha256": _sha(b"pricing:smoke"),
                "reservation_cost_usd": "0.01",
            },
            "max_output_tokens": 4096,
            "provider_record_kind": "gate-b-evidence-synthesis-v2",
        }
        generic_record = dict(record)
        generic_record.pop("provider_record_kind")
        generic_record.pop("provider_authority_identity")
        generic_record.pop("pricing")
        generic_record.pop("max_output_tokens")
        generic_record.pop("semantic_transport_record_sha256", None)
        capability.bind_record_identity(input_hash, provider_input_hash)
        self.verify_provider_record = capability.verify_record
        capability.seal_record(generic_record)
        record["semantic_transport_record_sha256"] = _sha(_canonical(generic_record))
        capability.seal_record(record)
        self.store.records[input_hash] = record
        capability.reconcile(reservation, Decimal("0"), outcome)
        capability.finalize_pending(input_hash)
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


def broken_decision_request_factory(context: Any) -> Any:
    del context
    raise RuntimeError("smoke_factory_injected_failure")


def prepare(
    *,
    root: Path,
    artifact_root: Path,
    repo_root: Path,
    runtime_manifest_path: Path | None = None,
    corpus_authority_path: Path | None = None,
) -> tuple[Path, Path, str]:
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
        # Bind the manifest authority to the exact policy bytes placed in the
        # artifact; never duplicate a policy hash as a free-standing literal.
        "decision_v2_bytes": policy_path.read_bytes(),
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
    if runtime_manifest_path is None:
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
    else:
        runtime_payload = json.loads(runtime_manifest_path.read_bytes())
        runtime_identity = RuntimeIdentity.model_validate(
            {
                "artifact_sha256": runtime_payload["artifact_sha256"],
                "artifact_tree_sha256": runtime_payload["artifact_tree_sha256"],
                "shim_sha256": runtime_payload["shim_sha256"],
                "interpreter_sha256": runtime_payload["python_executable_sha256"],
                "stdlib_inventory_sha256": runtime_payload["stdlib_tree_sha256"],
                "installed_distributions_sha256": runtime_payload[
                    "installed_distributions_sha256"
                ],
                "installed_files_sha256": runtime_payload["installed_files_sha256"],
                "sys_path_sha256": runtime_payload["sys_path_sha256"],
                "native_extensions_sha256": runtime_payload[
                    "native_extensions_sha256"
                ],
                "shared_libraries_sha256": runtime_payload[
                    "shared_libraries_sha256"
                ],
                "shared_library_provenance": runtime_payload.get(
                    "shared_library_provenance", {}
                ),
            }
        )
    authority_identity: AuthorityIdentity = _authority_identity(authorities)
    allow_entries: list[ReviewedFragmentEntryV3] = []
    rows: list[EvidenceManifestRow] = []
    corpus_rows = [
        {"ordinal": index, "record": record, "raw": raw}
        for index in range(48)
        for record, raw in (_make_record(index),)
    ]
    corpus_bytes = _canonical(corpus_rows)
    computed_corpus_sha256 = _sha(corpus_bytes)
    configured_authority = corpus_authority_path or (
        Path(os.environ["GATE_B_SMOKE_CORPUS_AUTHORITY_PATH"])
        if os.environ.get("GATE_B_SMOKE_CORPUS_AUTHORITY_PATH")
        else None
    )
    authority_path = configured_authority or (root / "corpus-authority.json")
    if not authority_path.exists():
        if configured_authority is not None:
            raise ValueError("corpus_authority_unavailable")
        authority_path.parent.mkdir(parents=True, exist_ok=True)
        authority_path.write_bytes(
            _canonical(
                {
                    "schema_version": CORPUS_AUTHORITY_SCHEMA,
                    "corpus_sha256": computed_corpus_sha256,
                }
            )
        )
    corpus_sha256 = _load_corpus_authority(
        authority_path,
        expected_corpus_sha256=computed_corpus_sha256,
    )
    allowlist = None
    for index in range(48):
        row_data = corpus_rows[index]
        record = row_data["record"]
        raw = row_data["raw"]
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
        allowlist = _allowlist(allow_entries, corpus_sha256)
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
        "corpus_sha256": corpus_sha256,
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
    spend_record_root = root / "spend-records"
    SpendRecordStore.provision(
        root=spend_record_root,
        manifest_sha256=str(payload["manifest_sha256"]),
        aggregate_maximum_cents=48,
    )
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
