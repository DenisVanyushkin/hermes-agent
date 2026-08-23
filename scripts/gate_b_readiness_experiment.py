from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from job_intel.product_search.company_evidence import load_company_thesis_input
from job_intel.product_search.decision_v2 import DecisionRequestV2, load_decision_policy, run_decision_v2
from job_intel.product_search.evidence_synthesis import _safe_output_sha256
from job_intel.product_search.gate_b_evidence_runner_v1 import build_decision_request_v2
from job_intel.product_search.gate_b import governed_pricing_schedule
from job_intel.product_search.gate_b_evidence_v3 import (
    EvidenceDimension,
    load_company_evidence_catalog_v3,
    load_reviewed_fragment_allowlist_v3,
    project_vacancy_evidence_v3,
    validate_provider_payload_v3,
)
from job_intel.vacancy_understanding.semantic.runtime.llm_provider import (
    _issue_structured_call_capability,
)


SCHEMA_VERSION = "gate-b-readiness-experiment-v1"
DIMENSIONS = tuple(dimension.value for dimension in EvidenceDimension)
EXPERIMENT_PRICING = governed_pricing_schedule()
UNMEASURABLE_NOTES = {
    1: "one unheaded description block; responsibilities cannot be recognized by the closed section set",
    17: "a company heading followed by German labels, not a recognized responsibility or requirement section",
    34: "a company heading followed by unlabeled English bullets, not a recognized responsibility or requirement section",
}


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _append_event(path: Path, event: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        stream.write(_canonical(dict(event)) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _events(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _git_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def _row_key(corpus_sha256: str, ordinal: int, input_sha256: str, projection_sha256: str) -> str:
    return _sha(_canonical({
        "corpus_sha256": corpus_sha256,
        "ordinal": ordinal,
        "input_sha256": input_sha256,
        "projection_sha256": projection_sha256,
    }))


@dataclass(frozen=True)
class ProviderCall:
    outcome: str
    response_payload: dict[str, object]
    raw_response_text: str
    provider_record: dict[str, object]


Provider = Callable[[dict[str, object], str, object], ProviderCall]


def assert_allowlist_covers_rows(rows: list[object], allowlist: object) -> None:
    """Fail before dispatch when a corpus row has no reviewed description entry."""
    reviewed_keys = {entry.selection_key for entry in allowlist.entries}
    uncovered = [
        str(item["ordinal"])
        for item in rows
        if item["record"]["selection_key"] not in reviewed_keys
    ]
    if uncovered:
        raise ValueError(f"allowlist_row_uncovered:{','.join(uncovered)}")


def issue_event_log_capability(
    *,
    results_path: Path,
    corpus_sha256: str,
    ordinal: int,
    row_key: str,
    input_sha256: str,
    projection_sha256: str,
    pricing: object = EXPERIMENT_PRICING,
) -> object:
    """Issue the governed runtime permit over this row's fsynced JSONL lifecycle."""
    reservations: set[str] = set()

    def reserve(dispatch_input_sha256: str, amount: Decimal) -> str:
        if dispatch_input_sha256 != input_sha256:
            raise ValueError("capability_input_sha256_mismatch")
        if amount != pricing.reservation_cost_usd:
            raise ValueError("capability_reservation_cost_mismatch")
        reservation_id = f"readiness:{row_key}"
        reservations.add(reservation_id)
        return reservation_id

    def mark_dispatching(reservation_id: str) -> None:
        if reservation_id not in reservations:
            raise ValueError("capability_reservation_unknown")
        _append_event(results_path, {
            "event": "dispatch_started", "row_key": row_key, "ordinal": ordinal,
            "input_sha256": input_sha256, "projection_sha256": projection_sha256,
        })

    def reconcile(reservation_id: str, actual_cost: Decimal, outcome: str) -> None:
        if reservation_id not in reservations:
            raise ValueError("capability_reservation_unknown")
        _append_event(results_path, {
            "event": "dispatch_reconciled", "row_key": row_key, "ordinal": ordinal,
            "outcome": outcome, "actual_cost_usd": str(actual_cost),
        })

    return _issue_structured_call_capability(
        run_identity_sha256=corpus_sha256,
        pricing=pricing,
        exact_call_cap=48,
        exact_spend_cap_usd=pricing.reservation_cost_usd * Decimal("48"),
        metadata_seal_key=hashlib.sha256(
            f"gate-b-readiness:{corpus_sha256}".encode()
        ).digest(),
        reserve=reserve,
        mark_dispatching=mark_dispatching,
        reconcile=reconcile,
    )


def _fake_payload(projected_payload: dict[str, object]) -> dict[str, object]:
    claims: list[dict[str, object]] = []
    for dimension in DIMENSIONS:
        selected: tuple[dict[str, object], dict[str, object]] | None = None
        for fragment in projected_payload["fragments"]:
            for claim in fragment["allowed_claims"]:
                if claim["dimension"] == dimension:
                    selected = (fragment, claim)
                    break
            if selected is not None:
                break
        if selected is None:
            raise ValueError(f"fake_provider_missing_dimension:{dimension}")
        fragment, claim = selected
        claims.append({
            "claim_id": f"claim:{dimension}",
            "dimension": dimension,
            "status": claim["status"],
            "claim_code": claim["claim_code"],
            "statement": claim["statement"],
            "citations": [fragment["fragment_id"]],
        })
    return {"schema_version": "2.0.0", "claims": claims, "conflicts": [], "question_candidates": []}


def fake_provider(projected_payload: dict[str, object], row_key: str, capability: object) -> ProviderCall:
    started = time.monotonic()
    response = _fake_payload(projected_payload)
    raw = _canonical(response).decode()
    latency_ms = max(1, int((time.monotonic() - started) * 1000))
    reservation_id = capability.reserve(row_key)
    capability.mark_dispatching(reservation_id)
    capability.reconcile(reservation_id, Decimal("0"), "success")
    return ProviderCall(
        outcome="success",
        response_payload=response,
        raw_response_text=raw,
        provider_record={
            # Match the V2 provider contract. The fake changes transport only;
            # it must exercise the same metadata and output-hash binding as live.
            "provider_id": "llm-observation",
            "provider_version": "product-search-evidence-replay/2.0",
            "model_id": "openai/gpt-5-mini",
            "semantic_prompt_version": "llm-obs-1.0.0",
            "prompt_version": "product-search-evidence-synthesis-2.0.0",
            "schema_version": "2.0.0",
            "output_sha256": _safe_output_sha256(response),
            "latency_ms": latency_ms,
            "cost_usd": "0",
            "measured_cost_usd": "0",
            "conservative_cost_usd": "0",
            "row_key": row_key,
        },
    )


def _thesis_for(projected: object, company_root: Path) -> object | None:
    authority = projected.company_authority
    bundle = getattr(authority, "company_evidence_bundle", None)
    if bundle is None:
        return None
    matches = sorted((company_root / bundle.company_identity.company_id).rglob("company-thesis-input.v1.yaml"))
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("company_thesis_not_unique")
    return load_company_thesis_input(matches[0], evidence_bundle=bundle)


def run_experiment(
    *,
    corpus_path: Path,
    allowlist_path: Path,
    company_evidence_root: Path,
    company_evidence_contract_sha256: str,
    decision_policy_path: Path,
    decision_clock: datetime,
    results_path: Path,
    provider: Provider,
) -> dict[str, int]:
    corpus_bytes = corpus_path.read_bytes()
    corpus_file_sha256 = _sha(corpus_bytes)
    rows = json.loads(corpus_bytes)
    if not isinstance(rows, list) or len(rows) != 48:
        raise ValueError("corpus_must_contain_exactly_48_rows")
    allowlist = load_reviewed_fragment_allowlist_v3(allowlist_path)
    source_corpus_sha256 = allowlist.gate_b_corpus_sha256
    assert_allowlist_covers_rows(rows, allowlist)
    allowlist_decisions = Counter(entry.decision.value for entry in allowlist.entries)
    catalog = load_company_evidence_catalog_v3(
        company_evidence_root,
        company_evidence_contract_sha256=company_evidence_contract_sha256,
    )
    policy = load_decision_policy(decision_policy_path)
    existing = _events(results_path)
    header = {
        "event": "run_header",
        "schema_version": SCHEMA_VERSION,
        "corpus_sha256": corpus_file_sha256,
        "source_corpus_sha256": source_corpus_sha256,
        "git_commit": _git_commit(),
        "decision_contract_sha256": policy.source_sha256,
        "model_id": "deterministic-fake-v1",
        "prompt_id": "product-search-evidence-synthesis-2.0.0",
        "schema_id": "evidence-synthesis-2.0.0",
        "decision_clock": decision_clock.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "evidence_admission": {
            "allowlist_entries": len(allowlist.entries),
            "admitted": allowlist_decisions["allow_role_responsibility"] + allowlist_decisions["allow_role_requirement"],
            "excluded_ambiguous": allowlist_decisions["exclude_ambiguous"],
            "excluded_company_fact": allowlist_decisions["exclude_company_fact"],
        },
    }
    if existing:
        if existing[0] != header:
            raise ValueError("run_header_mismatch")
    else:
        _append_event(results_path, header)
        existing = [header]
    terminal = {str(event["row_key"]): event for event in existing if event.get("event") == "terminal_result"}
    started = {str(event["row_key"]) for event in existing if event.get("event") == "dispatch_started"}
    counts = {
        "transport_success": 0,
        "terminal_failure": 0,
        "ambiguous": 0,
        "cached": 0,
        "assessed": 0,
        "decision_fail_closed": 0,
    }
    for item in rows:
        ordinal = int(item["ordinal"])
        raw = item["raw"]
        record = item["record"]
        projected = project_vacancy_evidence_v3(
            record, raw, allowlist, company_evidence_catalog=catalog
        )
        projected_payload = projected.provider_payload()
        input_sha256 = _sha(_canonical(projected_payload))
        projection_sha256 = _sha(_canonical(projected.model_dump(mode="json")))
        key = _row_key(corpus_file_sha256, ordinal, input_sha256, projection_sha256)
        if key in terminal:
            counts["cached"] += 1
            continue
        if key in started:
            event = {
                "event": "terminal_result", "row_key": key, "ordinal": ordinal,
                "outcome": "ambiguous", "actual_cost_usd": None,
                "conservative_cost_usd": "0.01", "reason": "dispatch_started_without_terminal",
            }
            _append_event(results_path, event)
            counts["ambiguous"] += 1
            continue
        capability = issue_event_log_capability(
            results_path=results_path,
            corpus_sha256=corpus_file_sha256,
            ordinal=ordinal,
            row_key=key,
            input_sha256=input_sha256,
            projection_sha256=projection_sha256,
            pricing=getattr(provider, "pricing", EXPERIMENT_PRICING),
        )
        wall_started = time.monotonic()
        try:
            call = provider(projected_payload, input_sha256, capability)
            if call.outcome != "success":
                event = {
                    "event": "terminal_result", "row_key": key, "ordinal": ordinal,
                    "company": raw.get("company"), "title": raw.get("title"),
                    "outcome": call.outcome, "input_sha256": input_sha256,
                    "projection_sha256": projection_sha256,
                    "raw_response_text": call.raw_response_text,
                    "response_payload": call.response_payload,
                    "provider_record": call.provider_record,
                    "actual_cost_usd": call.provider_record.get("measured_cost_usd"),
                    "conservative_cost_usd": call.provider_record.get("conservative_cost_usd"),
                    "provider_latency_ms": call.provider_record.get("latency_ms"),
                    "wall_latency_ms": max(1, int((time.monotonic() - wall_started) * 1000)),
                }
                _append_event(results_path, event)
                counts[call.outcome] = counts.get(call.outcome, 0) + 1
                continue
            validation_status = validate_provider_payload_v3(
                call.response_payload, synthesis_input=projected, reviewed_allowlist=allowlist
            )
            request = build_decision_request_v2(
                response_payload=call.response_payload,
                projected=projected,
                provider_input_sha256=input_sha256,
                raw=raw,
                provider_record=call.provider_record,
                validation_status=validation_status,
                decision_policy=policy,
                decision_clock=decision_clock,
                company_thesis_input=_thesis_for(projected, company_evidence_root),
            )
            decision = run_decision_v2(request, policy=policy)
            event = {
                "event": "terminal_result", "row_key": key, "ordinal": ordinal,
                "company": raw.get("company"), "title": raw.get("title"),
                "outcome": "success", "input_sha256": input_sha256,
                "projection_sha256": projection_sha256,
                "projected_payload": projected_payload,
                "raw_response_text": call.raw_response_text,
                "response_payload": call.response_payload,
                "provider_record": call.provider_record,
                "decision_request": request.model_dump(mode="json"),
                "decision_result": decision.model_dump(mode="json"),
                "measurement_status": (
                    "measurable" if decision.status.value == "assessed" else "not_measurable"
                ),
                "measurement_reason": (
                    None
                    if decision.status.value == "assessed"
                    else UNMEASURABLE_NOTES.get(
                        ordinal, str(decision.failure_reason or "decision_fail_closed")
                    )
                ),
                "actual_cost_usd": call.provider_record.get("measured_cost_usd", call.provider_record.get("cost_usd")),
                "conservative_cost_usd": call.provider_record.get("conservative_cost_usd"),
                "provider_latency_ms": call.provider_record.get("latency_ms"),
                "wall_latency_ms": max(1, int((time.monotonic() - wall_started) * 1000)),
            }
            _append_event(results_path, event)
            counts["transport_success"] += 1
            if decision.status.value == "assessed":
                counts["assessed"] += 1
            else:
                counts["decision_fail_closed"] += 1
        except Exception as exc:
            _append_event(results_path, {
                "event": "terminal_result", "row_key": key, "ordinal": ordinal,
                "outcome": "terminal_failure", "reason": f"{type(exc).__name__}:{exc}",
                "actual_cost_usd": None, "conservative_cost_usd": "0.01",
                "wall_latency_ms": max(1, int((time.monotonic() - wall_started) * 1000)),
            })
            counts["terminal_failure"] += 1
    return counts


def replay(results_path: Path, decision_policy_path: Path) -> dict[str, int]:
    events = _events(results_path)
    if not events or events[0].get("event") != "run_header":
        raise ValueError("run_header_missing")
    policy = load_decision_policy(decision_policy_path)
    if events[0].get("decision_contract_sha256") != policy.source_sha256:
        raise ValueError("replay_decision_contract_mismatch")
    counts = {"success": 0, "terminal_failure": 0, "ambiguous": 0}
    for event in events:
        if event.get("event") != "terminal_result":
            continue
        outcome = str(event["outcome"])
        counts[outcome] = counts.get(outcome, 0) + 1
        if outcome == "success":
            if _canonical(json.loads(str(event["raw_response_text"]))) != _canonical(event["response_payload"]):
                raise ValueError("raw_response_replay_mismatch")
            request = DecisionRequestV2.model_validate(event["decision_request"])
            replayed = run_decision_v2(request, policy=policy).model_dump(mode="json")
            if _canonical(replayed) != _canonical(event["decision_result"]):
                raise ValueError("decision_replay_mismatch")
    return counts


def summarize(results_path: Path, markdown_path: Path, adjudication_path: Path) -> None:
    events = _events(results_path)
    header = events[0] if events else {}
    terminal = [event for event in events if event.get("event") == "terminal_result"]
    existing_adjudication = {
        str(item["row_key"]): item for item in _events(adjudication_path)
    }
    reviewed = sum(item.get("correct") is not None for item in existing_adjudication.values())
    correct = sum(item.get("correct") is True for item in existing_adjudication.values())
    transport_success = sum(item.get("outcome") == "success" for item in terminal)
    assessed = sum(
        (item.get("decision_result") or {}).get("status") == "assessed"
        for item in terminal
    )
    not_measurable = sum(event.get("measurement_status") == "not_measurable" for event in terminal)
    admission = header.get("evidence_admission", {})
    lines = [
        "# Gate B readiness adjudication",
        "",
        "Fill `correct` and `note` in the adjudication JSONL after reviewing each row.",
        f"Transport completed: {transport_success}/{len(terminal)}. Decisions assessed: {assessed}/{len(terminal)}. Not measurable: {not_measurable}/{len(terminal)}.",
        "Evidence admission: "
        f"{admission.get('admitted', 0)}/{admission.get('allowlist_entries', 0)} fragments admitted; "
        f"{admission.get('excluded_ambiguous', 0)} ambiguous and "
        f"{admission.get('excluded_company_fact', 0)} company-fact fragments excluded.",
        f"Reviewed: {reviewed}/{len(terminal)}. Correct: {correct}/{reviewed if reviewed else 0}.",
        "",
        "| Row | Company | Vacancy | Verdict | Feasibility | Mandate | Company | Transferability | Career | Confidence | Unknowns | Correct | Note |",
        "|---:|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    adjudications: list[dict[str, object]] = []
    for event in sorted(terminal, key=lambda item: int(item["ordinal"])):
        result = event.get("decision_result") or {}
        assessment = result.get("assessment") if isinstance(result, dict) else None
        if assessment:
            dimensions = assessment["dimensions"]
            verdict = assessment["system_verdict"]
            unknowns = ", ".join(assessment.get("unknowns", [])) or "—"
            outcomes = [dimensions[name]["outcome"] for name in DIMENSIONS]
        else:
            verdict = "NOT MEASURABLE" if event.get("measurement_status") == "not_measurable" else str(event["outcome"])
            outcomes = ["—"] * 6
            unknowns = str(event.get("measurement_reason") or event.get("reason", "—"))
        saved = existing_adjudication.get(str(event["row_key"]), {})
        correct_cell = "" if saved.get("correct") is None else str(saved["correct"]).lower()
        cells = [str(event["ordinal"]), str(event.get("company", "")), str(event.get("title", "")), verdict, *outcomes, unknowns, correct_cell, str(saved.get("note", ""))]
        lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
        adjudications.append({"ordinal": event["ordinal"], "row_key": event["row_key"], "correct": None, "note": ""})
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not adjudication_path.exists():
        adjudication_path.write_bytes(b"".join(_canonical(item) + b"\n" for item in adjudications))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    for name in ("corpus", "allowlist", "company-evidence-root", "decision-policy", "results"):
        run.add_argument(f"--{name}", type=Path, required=True)
    run.add_argument("--company-evidence-contract-sha256", required=True)
    run.add_argument("--decision-clock", required=True)
    run.add_argument("--provider", choices=("fake",), required=True)
    replay_parser = sub.add_parser("replay")
    replay_parser.add_argument("--results", type=Path, required=True)
    replay_parser.add_argument("--decision-policy", type=Path, required=True)
    summary = sub.add_parser("summarize")
    summary.add_argument("--results", type=Path, required=True)
    summary.add_argument("--markdown", type=Path, required=True)
    summary.add_argument("--adjudication", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "run":
        counts = run_experiment(
            corpus_path=args.corpus, allowlist_path=args.allowlist,
            company_evidence_root=args.company_evidence_root,
            company_evidence_contract_sha256=args.company_evidence_contract_sha256,
            decision_policy_path=args.decision_policy,
            decision_clock=datetime.fromisoformat(args.decision_clock.replace("Z", "+00:00")),
            results_path=args.results, provider=fake_provider,
        )
    elif args.command == "replay":
        counts = replay(args.results, args.decision_policy)
    else:
        summarize(args.results, args.markdown, args.adjudication)
        counts = {"summarized": 1}
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
