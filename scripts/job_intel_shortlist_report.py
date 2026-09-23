"""Frozen-verdict discrepancy report with crash-recoverable Slack publication."""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from job_intel_shortlist_publish import (
    _atomic_json, _json_bytes, _pages, _read_json, _release_lock, _source, _verify_frozen,
    load_receipt,
)
from job_intel_shortlist_workbook import projection_sha256


class ReportError(ValueError):
    """The frozen report cannot be computed or safely reconciled."""


REPORT_TAG = "JI_SHORTLIST_REPORT_V1"
DEFECTIVE_US_RULESET = "rf1-a428b6ed9234"
ENTRYPOINT = "job_intel.observability.record_daily_observability"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
DEFAULT_DB = Path("/var/lib/job-intel/state/job_intel.sqlite3")
_US_LOCATION_PATTERNS = tuple(re.compile(pattern, re.I) for pattern in (
    r"\bunited\s+states\b", r"\busa\b", r"\bu\.s\.a?\b", r"\bus\b",
    r"\bsan\s+francisco\b", r"\bnew\s+york\b", r"\bseattle\b",
    r"\bchicago\b", r"\bboston\b", r"\baustin\b", r"\bdenver\b",
    r"\blos\s+angeles\b", r"\bcalifornia\b", r"\bwashington\b",
    r"\bnew\s+jersey\b", r",\s*(?:ca|ny|wa|tx|ma|il|co|nj)\b",
))
_US_GATE_RULE_IDS = frozenset({"us_onsite_without_sponsorship", "us_remote_eligibility_unknown"})


def _defective_us_gate(item: dict[str, Any]) -> bool:
    if item.get("ruleset_version") != DEFECTIVE_US_RULESET:
        return False
    location = str(item.get("location") or "")
    return (any(pattern.search(location) for pattern in _US_LOCATION_PATTERNS)
            or bool(_US_GATE_RULE_IDS.intersection(item.get("rule_ids") or [])))


def _evaluation_hash(artifact: dict[str, Any]) -> str:
    evaluation = artifact.get("role_fit_evaluation")
    if (not isinstance(evaluation, dict) or evaluation.get("available") is not True
            or evaluation.get("entrypoint") != ENTRYPOINT
            or not isinstance(evaluation.get("arguments"), dict)):
        raise ReportError("frozen role-fit evaluation arguments unavailable")
    canonical = json.dumps(evaluation["arguments"], ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
    actual = hashlib.sha256(canonical).hexdigest()
    if evaluation.get("arguments_sha256") != actual:
        raise ReportError("role-fit argument hash mismatch")
    return actual


def _observability_check(path: Path | None, rows: dict[str, tuple[str, dict[str, Any]]]) -> dict[str, str]:
    if path is None:
        return {key: "not_checked" for key in rows}
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        with closing(connection):
            connection.execute("PRAGMA query_only=ON")
            statuses = {}
            for key, (_sheet, item) in rows.items():
                observed = {row[0] for row in connection.execute(
                    "SELECT DISTINCT role_fit_verdict FROM vacancy_observability "
                    "WHERE run_id = ? AND vacancy_key = ?", (item.get("run_id"), key),
                )}
                if not observed:
                    statuses[key] = "missing"
                elif len(observed) > 1:
                    statuses[key] = "ambiguous"
                elif item["role_fit_verdict"] in observed:
                    statuses[key] = "match"
                else:
                    statuses[key] = "mismatch"
        return statuses
    except sqlite3.Error as error:
        raise ReportError("cannot cross-check frozen verdicts against observability") from error


def build_report(artifact: dict[str, Any], receipt: dict[str, Any],
                 *, db_path: Path | None = None) -> dict[str, Any]:
    """Classify only frozen role verdicts against the first imported owner file."""
    if receipt.get("state") not in {"imported", "reported"}:
        raise ReportError("report requires an imported release")
    if (artifact.get("release_id") != receipt.get("release_id")
            or projection_sha256(artifact) != receipt.get("projection_sha256")):
        raise ReportError("frozen artifact projection mismatch")
    intent = receipt.get("import_intent")
    if (not isinstance(intent, dict) or intent.get("release_id") != receipt["release_id"]
            or intent.get("attempt_id") != receipt.get("attempt_id")
            or intent.get("projection_sha256") != receipt["projection_sha256"]
            or not isinstance(intent.get("decisions"), dict)):
        raise ReportError("imported owner intent missing or changed")
    if (intent.get("file_id") != receipt.get("owner_file_id")
            or intent.get("file_hash") != receipt.get("owner_file_sha256")
            or intent.get("response_uid") != receipt.get("response_uid")):
        raise ReportError("imported owner file identity mismatch")
    arguments_sha = _evaluation_hash(artifact)
    rows: dict[str, tuple[str, dict[str, Any]]] = {}
    for sheet, field, verdicts in (("shortlist", "items", {"accept"}),
                                   ("rejected_sample", "rejected_sample", {"reject", "blocked"})):
        items = artifact.get(field)
        if not isinstance(items, list):
            raise ReportError(f"artifact lacks {field}")
        for item in items:
            if not isinstance(item, dict):
                raise ReportError(f"invalid {field} row")
            key = item.get("vacancy_key")
            if not isinstance(key, str) or not key or key in rows or item.get("role_fit_verdict") not in verdicts:
                raise ReportError(f"duplicate or invalid frozen vacancy: {key}")
            per_row_hash = item.get("evaluation_arguments_sha256")
            if per_row_hash is not None and per_row_hash != arguments_sha:
                raise ReportError(f"role-fit argument hash differs for {key}")
            rows[key] = sheet, item
    decisions = intent["decisions"]
    for key, value in decisions.items():
        if (key not in rows or not isinstance(value, dict) or value.get("sheet") != rows[key][0]
                or value.get("owner_decision") not in {"yes", "no", "blocked_language"}):
            raise ReportError(f"invalid imported decision for {key}")

    observability = _observability_check(db_path, rows)
    counts: Counter[str] = Counter()
    outcomes: list[dict[str, Any]] = []
    for key, (sheet, item) in rows.items():
        frozen = item["role_fit_verdict"]
        owner = decisions.get(key, {}).get("owner_decision")
        if owner is None:
            category = "undecided"
        elif owner == "blocked_language":
            category = "blocked_language"
        elif sheet == "shortlist" and owner == "no":
            category = "false_positive"
        elif sheet == "rejected_sample" and owner == "yes":
            category = "miss"
        else:
            category = "agreement"
        us_defect = _defective_us_gate(item)
        if us_defect and category in {"false_positive", "miss"}:
            category = "ruleset_defect"
        counts[category] += 1
        outcomes.append({
            "vacancy_key": key, "sheet": sheet, "company": item.get("company") or "",
            "title": item.get("title") or "", "frozen_verdict": frozen,
            "rule_ids": item.get("rule_ids") or [],
            "ruleset_version": item.get("ruleset_version") or "unknown",
            "owner_decision": owner, "owner_note": decisions.get(key, {}).get("owner_note", ""),
            "category": category,
            "observability_check": observability[key],
            # This is a measurement status, not a revision of the frozen verdict.
            "us_authorization_status": (
                "ruleset_defect" if us_defect
                else "not_affected"
            ),
        })
    return {
        "report": "job_intel_weekly_shortlist_discrepancies", "version": 1,
        "release_id": receipt["release_id"], "attempt_id": receipt["attempt_id"],
        "projection_sha256": receipt["projection_sha256"],
        "owner_file_sha256": receipt["owner_file_sha256"],
        "evaluation_arguments_sha256": arguments_sha,
        "entrypoint": ENTRYPOINT,
        "observability_check_counts": dict(sorted(Counter(observability.values()).items())),
        "counts": {name: counts[name] for name in
                   ("false_positive", "miss", "blocked_language", "agreement", "undecided",
                    "ruleset_defect")},
        "decisions": outcomes,
    }


def report_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_json_bytes(payload)).hexdigest()


def report_marker(receipt: dict[str, Any], digest: str) -> str:
    return (f"{REPORT_TAG} release_id={receipt['release_id']} "
            f"attempt_id={receipt['attempt_id']} report_sha256={digest}")


def render_report(payload: dict[str, Any], marker: str) -> str:
    counts = payload["counts"]
    lines = [f"*Расхождения по недельному shortlist* {payload['release_id']}",
             (f"Ложные срабатывания: {counts['false_positive']}; пропуски: {counts['miss']}; "
              f"языковой блок: {counts['blocked_language']}; согласие: {counts['agreement']}; "
              f"без решения: {counts['undecided']}; дефектная US-версия: {counts['ruleset_defect']}.")]
    for row in payload["decisions"]:
        if row["category"] in {"false_positive", "miss", "blocked_language", "ruleset_defect"}:
            lines.append(f"• {row['category']}: {row['company']} — {row['title']} ({row['vacancy_key']})")
    if any(row["us_authorization_status"] == "ruleset_defect" for row in payload["decisions"]):
        lines.append("US work-authorization measurement: ruleset_defect for affected historical version; other categories retained.")
    lines.append(marker)
    return "\n".join(lines)


def _void_report(attempt_dir: Path, receipt: dict[str, Any], reason: str) -> None:
    _atomic_json(attempt_dir / "receipt.json", {**receipt, "report_state": "void",
                                                 "report_void_reason": reason})
    raise ReportError(reason)


def publish_report(source_dir: Path, release_root: Path, release_id: str, client: Any,
                   *, now: datetime | None = None, db_path: Path | None = None) -> dict[str, Any]:
    """Post one typed report or adopt a prior post after a crash."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ReportError("now must be timezone-aware")
    artifact, source_sha = _source(source_dir)
    if artifact["release_id"] != release_id:
        raise ReportError("source release mismatch")
    attempt_dir = release_root / release_id / "attempt-001"
    if not attempt_dir.is_dir():
        raise ReportError("release attempt missing")
    with _release_lock(attempt_dir.parent):
        receipt = load_receipt(attempt_dir)
        _verify_frozen(attempt_dir, receipt, source_sha=source_sha, channel=receipt["channel"])
        if receipt["state"] == "reported":
            report_path = attempt_dir / "report.json"
            if (not report_path.is_file() or
                    hashlib.sha256(report_path.read_bytes()).hexdigest() != receipt.get("report_sha256")):
                raise ReportError("reported release lacks its frozen report")
            return receipt
        if receipt["state"] != "imported":
            raise ReportError(f"release not imported: {receipt['state']}")
        if receipt.get("report_state") == "void":
            raise ReportError("report ambiguity requires operator resolution")
        report_path = attempt_dir / "report.json"
        intent = receipt.get("report_intent")
        if intent is not None:
            digest = intent.get("report_sha256") if isinstance(intent, dict) else None
            if (not isinstance(digest, str) or not _SHA.fullmatch(digest)
                    or not report_path.is_file()
                    or hashlib.sha256(report_path.read_bytes()).hexdigest() != digest):
                _void_report(attempt_dir, receipt, "frozen report intent or content changed")
            payload = _read_json(report_path)
            if (payload.get("release_id") != receipt["release_id"]
                    or payload.get("attempt_id") != receipt["attempt_id"]
                    or payload.get("projection_sha256") != receipt["projection_sha256"]
                    or payload.get("owner_file_sha256") != receipt["owner_file_sha256"]):
                _void_report(attempt_dir, receipt, "frozen report identity changed")
        else:
            payload = build_report(artifact, receipt, db_path=db_path)
            digest = report_sha256(payload)
            if report_path.exists():
                if hashlib.sha256(report_path.read_bytes()).hexdigest() != digest:
                    # No intent and no Slack write exist yet. A crash after
                    # writing report.json may be rebuilt from a changed
                    # diagnostic DB before the report is frozen.
                    _atomic_json(report_path, payload)
            else:
                _atomic_json(report_path, payload)
            receipt = {**receipt, "report_intent": {"report_sha256": digest}}
            _atomic_json(attempt_dir / "receipt.json", receipt)

        try:
            auth = client.auth_test()
            if not auth.get("ok") or not auth.get("user_id"):
                raise ReportError("Slack auth_test failed")
            messages = _pages(client.conversations_replies, collection="messages",
                              channel=receipt["channel"], ts=receipt["thread_ts"], inclusive=True)
        except Exception as error:
            raise ReportError(f"Slack report scan failed: {error}") from error
        marker = report_marker(receipt, digest)
        matches = [str(message["ts"]) for message in messages
                   if message.get("user") == str(auth["user_id"])
                   and marker in str(message.get("text") or "").splitlines()
                   and message.get("ts")]
        if len(matches) > 1:
            _void_report(attempt_dir, receipt, "ambiguous Slack reports")
        if matches:
            report_ts = matches[0]
        else:
            response = client.chat_postMessage(channel=receipt["channel"], thread_ts=receipt["thread_ts"],
                                               text=render_report(payload, marker))
            report_ts = response.get("ts")
            if not report_ts:
                raise ReportError("Slack report post lacks ts; retry must scan")
        reported = {**receipt, "state": "reported", "report_sha256": digest,
                    "report_ts": str(report_ts), "reported_at": now.astimezone(timezone.utc).isoformat()}
        _atomic_json(attempt_dir / "receipt.json", reported)
        return reported


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    from slack_sdk import WebClient
    from job_intel_shortlist_send import load_token

    receipt = publish_report(args.artifact_dir, args.release_root, args.release_id,
                             WebClient(token=load_token()), db_path=args.db)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
