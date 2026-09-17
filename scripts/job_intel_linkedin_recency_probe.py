from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

WORKTREE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKTREE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKTREE_ROOT))

from job_intel.browser_sourcing import build_linkedin_search_url
from job_intel.product_search.acquisition_probe import LinkedInExecutionPlan
from job_intel.sources import (
    LINKEDIN_URL_VARIANT_DEFAULT,
    LINKEDIN_URL_VARIANT_RECENCY_24H,
    QUERY_EXPERIMENT_BRANCH_CONTROL,
    QUERY_EXPERIMENT_BRANCH_TREATMENT,
    fetch_linkedin_vacancies,
    rotating_linkedin_queries,
    ROLE_FAMILIES,
    _join_group,
    _linkedin_terms,
)


DEFAULT_CELLS = (
    "dach/transformation_builder",
    "kazakhstan/digital_business",
    "singapore/customer_growth_commercial_hybrid",
)
PAIR_ARM_ORDER = (
    (QUERY_EXPERIMENT_BRANCH_CONTROL, QUERY_EXPERIMENT_BRANCH_TREATMENT),
    (QUERY_EXPERIMENT_BRANCH_TREATMENT, QUERY_EXPERIMENT_BRANCH_CONTROL),
    (QUERY_EXPERIMENT_BRANCH_CONTROL, QUERY_EXPERIMENT_BRANCH_TREATMENT),
)


class ProbeSafetyError(RuntimeError):
    pass


_SERVICE_ACTIVE_STATES = frozenset(
    {"active", "activating", "deactivating", "reloading"}
)
_DAILY_SAFE_LOAD_STATES = frozenset({"loaded", "masked", "not-found"})
_SHADOW_SERVICE = "job-intel-shadow-collection.service"
_DAILY_SERVICE = "job-intel-daily.service"
_SHADOW_TIMER = "job-intel-shadow-collection.timer"
_MIN_TIMER_LEAD_SECONDS = 20 * 60


def _systemctl_show(name: str, *properties: str) -> dict[str, str]:
    command = ["systemctl", "show"]
    for prop in properties:
        command.extend(["-p", prop])
    command.append(name)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:  # pragma: no cover - platform failure
        raise ProbeSafetyError(f"systemctl show failed for {name}: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        raise ProbeSafetyError(f"systemctl show failed for {name}: {detail}")
    values: dict[str, str] = {}
    for line in (result.stdout or "").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    missing = [prop for prop in properties if prop not in values]
    if missing:
        raise ProbeSafetyError(
            f"systemctl show returned no {', '.join(missing)} for {name}"
        )
    return values


def _check_service_load_and_activity(
    name: str,
    *,
    safe_load_states: frozenset[str],
) -> None:
    values = _systemctl_show(name, "LoadState", "ActiveState")
    load_state = values["LoadState"]
    active_state = values["ActiveState"]
    if load_state not in safe_load_states:
        raise ProbeSafetyError(
            f"{name} has unsafe LoadState={load_state or '<empty>'}"
        )
    if load_state == "loaded" and active_state in _SERVICE_ACTIVE_STATES:
        raise ProbeSafetyError(f"{name} is {active_state}; refusing live probe")


def _check_shadow_timer() -> None:
    values = _systemctl_show(_SHADOW_TIMER, "NextElapseUSecRealtime")
    raw_next_elapse = values["NextElapseUSecRealtime"]
    try:
        next_elapse_us = int(raw_next_elapse)
    except (TypeError, ValueError):
        try:
            next_elapse = datetime.strptime(
                raw_next_elapse, "%a %Y-%m-%d %H:%M:%S %Z"
            ).replace(tzinfo=timezone.utc)
            next_elapse_us = int(next_elapse.timestamp() * 1_000_000)
        except (TypeError, ValueError) as exc:
            raise ProbeSafetyError(
                f"cannot parse {_SHADOW_TIMER} NextElapseUSecRealtime={raw_next_elapse!r}"
            ) from exc
    now_us = int(datetime.now(timezone.utc).timestamp() * 1_000_000)
    if next_elapse_us - now_us < _MIN_TIMER_LEAD_SECONDS * 1_000_000:
        raise ProbeSafetyError(
            f"{_SHADOW_TIMER} fires in less than 20 minutes; refusing live probe"
        )


def _preflight_services() -> None:
    _check_service_load_and_activity(
        _SHADOW_SERVICE,
        safe_load_states=frozenset({"loaded"}),
    )
    _check_service_load_and_activity(
        _DAILY_SERVICE,
        safe_load_states=_DAILY_SAFE_LOAD_STATES,
    )
    _check_shadow_timer()


def _probe_plan(
    cells: list[str] | tuple[str, ...],
    *,
    as_of: date,
    rotation_slot: int,
) -> list[dict[str, Any]]:
    if len(cells) != 3 or len(set(cells)) != 3:
        raise ValueError("probe requires exactly three distinct LinkedIn cells")
    candidates = rotating_linkedin_queries(
        limit=24, as_of=as_of, rotation_slot=rotation_slot
    )
    by_cell = {item.cell_id: item for item in candidates}
    specs = [tuple(cell.split("/", 1)) for cell in cells]
    missing = [
        raw
        for raw, spec in zip(cells, specs)
        if len(spec) != 2
        or spec[0] not in by_cell
        or spec[1] not in dict(ROLE_FAMILIES)
    ]
    if missing:
        raise ValueError(f"unsupported LinkedIn probe cells: {', '.join(missing)}")
    plan: list[dict[str, Any]] = []
    order = 1
    for pair_index, ((cell, role_family), arm_order) in enumerate(zip(specs, PAIR_ARM_ORDER), 1):
        base = by_cell[cell]
        role_terms = dict(ROLE_FAMILIES)[role_family]
        query = f"({_join_group(_linkedin_terms(role_terms))})"
        for branch in arm_order:
            variant = (
                LINKEDIN_URL_VARIANT_RECENCY_24H
                if branch == QUERY_EXPERIMENT_BRANCH_TREATMENT
                else LINKEDIN_URL_VARIANT_DEFAULT
            )
            plan.append(
                {
                    "order": order,
                    "pair_index": pair_index,
                    "cell_id": base.cell_id,
                    "query": query,
                    "location": base.location,
                    "geo_id": base.geo_id,
                    "branch": branch,
                    "url_variant": variant,
                    "requested_url": build_linkedin_search_url(
                        keywords=query,
                        location=base.location,
                        geo_id=base.geo_id,
                        url_variant=variant,
                    ),
                }
            )
            order += 1
    return plan


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _numeric_ids(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        return []
    return sorted({str(value) for value in values if str(value).isdigit()})


def _query_record(
    spec: dict[str, Any],
    *,
    started: float,
    trace: dict[str, Any] | None,
    error: str | None = None,
) -> dict[str, Any]:
    pages = trace.get("pages", []) if isinstance(trace, dict) else []
    page = pages[0] if pages and isinstance(pages[0], dict) else {}
    safety = {
        "login_wall_hits": int((trace or {}).get("login_wall_hits") or 0),
        "auth_redirects": int((trace or {}).get("auth_redirects") or 0),
        "anti_bot_events": int((trace or {}).get("anti_bot_events") or 0),
        "failure_reason": (trace or {}).get("failure_reason"),
        "stop_reason": (trace or {}).get("stop_reason"),
    }
    record = {
        "order": spec["order"],
        "pair_index": spec["pair_index"],
        "cell_id": spec["cell_id"],
        "query": spec["query"],
        "branch": spec["branch"],
        "url_variant": spec["url_variant"],
        "timestamp": _utc_now(),
        "latency_ms": int(round((time.perf_counter() - started) * 1000)),
        "requested_url": page.get("requested_url") or spec["requested_url"],
        "final_url": page.get("final_url") or "",
        "page_classification": page.get("page_classification") or "not_observed",
        "safety_reason": page.get("safety_reason"),
        "safety": safety,
        "job_ids": _numeric_ids(page.get("dom_unique_job_ids", [])),
    }
    if error:
        record["error"] = error
    return record


def _write_report(report: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')}.json"
    path.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return path


def _probe_safety_stop_reason(record: dict[str, Any]) -> str | None:
    page_reason = record.get("safety_reason")
    if page_reason:
        return str(page_reason)
    safety = record.get("safety")
    if not isinstance(safety, dict):
        return None
    for field in ("login_wall_hits", "auth_redirects", "anti_bot_events"):
        try:
            if int(safety.get(field) or 0) > 0:
                return field
        except (TypeError, ValueError):
            continue
    trace_stop_reason = safety.get("stop_reason")
    if trace_stop_reason in {"critical_degradation", "cell_deadline"}:
        return str(trace_stop_reason)
    return None


def run_probe(
    *,
    cells: list[str] | tuple[str, ...] = DEFAULT_CELLS,
    as_of: date,
    rotation_slot: int,
    live: bool = False,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    if live:
        _preflight_services()
    plan = _probe_plan(cells, as_of=as_of, rotation_slot=rotation_slot)
    report: dict[str, Any] = {
        "probe": "linkedin_recency_ab",
        "live": live,
        "date": as_of.isoformat(),
        "rotation_slot": rotation_slot,
        "pair_order": [list(order) for order in PAIR_ARM_ORDER],
        "queries": [],
        "pairs": [],
        "stop_reason": None,
    }
    if not live:
        report["queries"] = plan
        return report

    queries = report["queries"]
    stopped = False
    for pair_index, pair_order in enumerate(PAIR_ARM_ORDER, 1):
        pair_specs = [item for item in plan if item["pair_index"] == pair_index]
        pair_records: dict[str, dict[str, Any]] = {}
        pair_stop_reason: str | None = None
        for spec in pair_specs:
            if stopped:
                pair_stop_reason = f"not_attempted_after: {report['stop_reason']}"
                break
            started = time.perf_counter()
            trace: dict[str, Any] | None = None
            try:
                fetch_linkedin_vacancies.last_trace = None  # type: ignore[attr-defined]
                fetch_linkedin_vacancies(
                    spec["query"],
                    max_pages=1,
                    location=spec["location"],
                    geo_id=spec["geo_id"],
                    url_variant=spec["url_variant"],
                    experiment_branch=spec["branch"],
                    execution_plan=LinkedInExecutionPlan(
                        page_offsets=(0,), max_scroll_checkpoints=1
                    ).model_dump(mode="json"),
                    allow_unauthenticated=True,
                    detail_page_budget=0,
                    no_retries=True,
                )
                trace = getattr(fetch_linkedin_vacancies, "last_trace", None)
                record = _query_record(spec, started=started, trace=trace)
                queries.append(record)
                pair_records[spec["branch"]] = record
                safety_stop_reason = _probe_safety_stop_reason(record)
                if safety_stop_reason:
                    pair_stop_reason = safety_stop_reason
                    report["stop_reason"] = pair_stop_reason
                    stopped = True
            except Exception as exc:  # noqa: BLE001 - first failure is the stop signal
                trace = getattr(fetch_linkedin_vacancies, "last_trace", None)
                error = str(exc)
                record = _query_record(
                    spec, started=started, trace=trace, error=error
                )
                queries.append(record)
                pair_records[spec["branch"]] = record
                pair_stop_reason = error
                report["stop_reason"] = error
                stopped = True
        control_ids = set(pair_records.get("control", {}).get("job_ids", []))
        treatment_ids = set(pair_records.get("treatment", {}).get("job_ids", []))
        report["pairs"].append(
            {
                "pair_index": pair_index,
                "cell_id": pair_specs[0]["cell_id"],
                "treatment_only": sorted(treatment_ids - control_ids),
                "control_only": sorted(control_ids - treatment_ids),
                "overlap": sorted(control_ids & treatment_ids),
                "stop_reason": pair_stop_reason,
            }
        )

    if output_dir is not None and live:
        report["report_path"] = str(_write_report(report, output_dir))
    return report


def _parse_date(text: str) -> date:
    return date.fromisoformat(text)


def main(argv: list[str] | None = None) -> int:
    utc_today = datetime.now(timezone.utc).date().isoformat()
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--cells", nargs=3, default=list(DEFAULT_CELLS), metavar="CELL")
    parser.add_argument(
        "--date",
        type=_parse_date,
        default=_parse_date(os.getenv("JOB_INTEL_QUERY_EXPERIMENT_DATE", "") or utc_today),
    )
    parser.add_argument(
        "--rotation-slot",
        type=int,
        default=int(os.getenv("JOB_INTEL_QUERY_EXPERIMENT_ROTATION_SLOT", "0") or "0"),
        choices=(0, 1),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/var/lib/job-intel/state/linkedin-recency-probe"),
    )
    args = parser.parse_args(argv)
    try:
        report = run_probe(
            cells=args.cells,
            as_of=args.date,
            rotation_slot=args.rotation_slot,
            live=args.live,
            output_dir=args.output_dir,
        )
    except ProbeSafetyError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True))
        return 2
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
