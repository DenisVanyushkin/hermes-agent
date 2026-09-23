"""Discrepancy reporting uses frozen decisions and survives Slack write gaps."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import job_intel_shortlist_publish as publish  # noqa: E402
import job_intel_shortlist_report as report  # noqa: E402
import job_intel_shortlist_issued as issued  # noqa: E402


NOW = datetime(2026, 9, 23, 11, tzinfo=timezone.utc)
RELEASE = "shortlist-2026-W39"


def _row(key: str, verdict: str, version: str = "rf1-good") -> dict:
    return {"vacancy_key": key, "company": "Example", "title": f"VP {key}",
            "location": "US" if key == "k1" else "Berlin", "source": "ATS",
            "url": f"https://example.org/{key}", "role_fit_verdict": verdict,
            "rule_ids": ["us_onsite_without_sponsorship"] if key == "k1" else ["scope"],
            "ruleset_version": version, "run_id": 1, "description": "Own product roadmap."}


def fixture(tmp_path: Path) -> tuple[Path, Path, Path, dict]:
    source = tmp_path / "source"
    source.mkdir(parents=True)
    arguments = {"owner_languages": ["en", "ru"], "supported_languages": ["en", "ru"],
                 "short_contract_months": 18}
    args_sha = hashlib.sha256(json.dumps(arguments, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()
    artifact = {
        "artifact": "job_intel_weekly_shortlist", "release_id": RELEASE,
        "built_at": NOW.isoformat(), "census_sha256": "abc", "run_ids": [1],
        "commit": "abc", "ruleset_versions": ["rf1-good", "rf1-a428b6ed9234"],
        "role_fit_evaluation": {"available": True,
                                "entrypoint": "job_intel.observability.record_daily_observability",
                                "arguments": arguments, "arguments_sha256": args_sha},
        "items": [_row("k1", "accept", "rf1-a428b6ed9234"), _row("k2", "accept"),
                  _row("k5", "accept", "rf1-a428b6ed9234")],
        "rejected_sample": [_row("k3", "reject"), _row("k4", "blocked")],
    }
    (source / "shortlist.json").write_text(json.dumps(artifact), encoding="utf-8")
    canonical = json.dumps({k: v for k, v in artifact.items() if k != "built_at"},
                           ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    (source / "SHA256SUMS").write_text(f"{hashlib.sha256(canonical).hexdigest()}  canonical\n")
    releases = tmp_path / "releases"
    receipt = publish.prepare_release(source, releases, "COWNER", now=NOW)
    attempt = releases / RELEASE / "attempt-001"
    receipt.update(
        state="imported", thread_ts="1790161200.000001", bot_file_id="FBOT",
        delivered_at=NOW.isoformat(), owner_file_id="FOWNER",
        owner_file_sha256="a" * 64, response_uid="UOWNER",
        import_intent={"release_id": RELEASE, "attempt_id": "attempt-001",
                       "projection_sha256": receipt["projection_sha256"],
                       "file_id": "FOWNER", "file_hash": "a" * 64,
                       "response_uid": "UOWNER", "response_ts": "1790161203.000001",
                       "decisions": {
                           "k1": {"owner_decision": "no", "owner_note": "US gate", "sheet": "shortlist"},
                           "k2": {"owner_decision": "blocked_language", "owner_note": "Language", "sheet": "shortlist"},
                           "k3": {"owner_decision": "yes", "owner_note": "Great role", "sheet": "rejected_sample"},
                           "k4": {"owner_decision": "no", "owner_note": "No", "sheet": "rejected_sample"},
                           "k5": {"owner_decision": "no", "owner_note": "Scope", "sheet": "shortlist"},
                       }},
    )
    publish._atomic_json(attempt / "receipt.json", receipt)
    return source, releases, attempt, artifact


class Slack:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.posts = 0
        self.crash_after_post = False
        self.scan_error = False

    def auth_test(self) -> dict:
        return {"ok": True, "user_id": "UBOT"}

    def conversations_replies(self, **kwargs: object) -> dict:
        if self.scan_error:
            raise RuntimeError("Slack unavailable")
        start = int(kwargs.get("cursor") or 0)
        page = self.messages[start:start + 1]
        cursor = str(start + 1) if start + 1 < len(self.messages) else ""
        return {"ok": True, "messages": page, "response_metadata": {"next_cursor": cursor}}

    def chat_postMessage(self, *, channel: str, thread_ts: str, text: str) -> dict:
        self.posts += 1
        ts = f"1790161300.{self.posts:06d}"
        self.messages.append({"ts": ts, "user": "UBOT", "text": text})
        if self.crash_after_post:
            self.crash_after_post = False
            raise RuntimeError("crash after Slack accepted report")
        return {"ok": True, "ts": ts}


def test_frozen_discrepancy_categories_and_narrow_ruleset_defect(tmp_path: Path) -> None:
    source, releases, attempt, artifact = fixture(tmp_path)
    receipt = publish.load_receipt(attempt)
    result = report.build_report(artifact, receipt)
    assert result["counts"] == {"false_positive": 1, "miss": 1,
                                "blocked_language": 1, "agreement": 1,
                                "undecided": 0, "ruleset_defect": 1}
    rows = {row["vacancy_key"]: row for row in result["decisions"]}
    assert rows["k1"]["category"] == "ruleset_defect"
    assert rows["k1"]["us_authorization_status"] == "ruleset_defect"
    assert rows["k5"]["category"] == "false_positive"
    assert rows["k5"]["us_authorization_status"] == "not_affected"
    assert rows["k3"]["category"] == "miss"
    assert rows["k3"]["us_authorization_status"] == "not_affected"
    assert result["evaluation_arguments_sha256"] == artifact["role_fit_evaluation"]["arguments_sha256"]


def test_report_accepts_weekly_recomputed_verdict_provenance(tmp_path: Path) -> None:
    _source, _releases, attempt, artifact = fixture(tmp_path)
    artifact["role_fit_evaluation"]["entrypoint"] = "scripts.job_intel_shortlist_build.build_weekly"
    result = report.build_report(artifact, publish.load_receipt(attempt))
    assert result["entrypoint"] == "scripts.job_intel_shortlist_build.build_weekly"


def test_argument_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    source, releases, attempt, artifact = fixture(tmp_path)
    artifact["role_fit_evaluation"]["arguments_sha256"] = "0" * 64
    with pytest.raises(report.ReportError, match="argument"):
        report.build_report(artifact, publish.load_receipt(attempt))


def test_different_row_argument_hash_is_not_combined(tmp_path: Path) -> None:
    source, releases, attempt, artifact = fixture(tmp_path)
    artifact["items"][0]["evaluation_arguments_sha256"] = "f" * 64
    with pytest.raises(report.ReportError, match="argument hash differs"):
        report.build_report(artifact, publish.load_receipt(attempt))


def test_observability_crosscheck_is_diagnostic_not_recalculation(tmp_path: Path) -> None:
    source, releases, attempt, artifact = fixture(tmp_path)
    db = tmp_path / "state.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE vacancy_observability (run_id INTEGER, vacancy_key TEXT, role_fit_verdict TEXT)")
        connection.executemany("INSERT INTO vacancy_observability VALUES (1, ?, ?)",
                               [("k1", "reject"), ("k2", "accept"), ("k3", "reject")])
    result = report.build_report(artifact, publish.load_receipt(attempt), db_path=db)
    rows = {row["vacancy_key"]: row for row in result["decisions"]}
    assert rows["k1"]["observability_check"] == "mismatch"
    assert rows["k1"]["category"] == "ruleset_defect"
    assert rows["k2"]["observability_check"] == "match"
    assert rows["k4"]["observability_check"] == "missing"


def test_report_crash_after_slack_post_adopts_without_duplicate(tmp_path: Path) -> None:
    source, releases, attempt, _ = fixture(tmp_path)
    slack = Slack()
    slack.crash_after_post = True
    with pytest.raises(RuntimeError, match="crash after Slack"):
        report.publish_report(source, releases, RELEASE, slack, now=NOW)
    assert publish.load_receipt(attempt)["state"] == "imported"
    assert publish.load_receipt(attempt)["report_intent"]["report_sha256"]
    receipt = report.publish_report(source, releases, RELEASE, slack, now=NOW)
    assert receipt["state"] == "reported"
    assert receipt["report_ts"] == slack.messages[0]["ts"]
    assert slack.posts == 1
    assert report.publish_report(source, releases, RELEASE, slack, now=NOW) == receipt
    assert slack.posts == 1


def test_report_retry_uses_frozen_content_when_crosscheck_db_changes(tmp_path: Path) -> None:
    source, releases, attempt, _ = fixture(tmp_path)
    db = tmp_path / "state.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE vacancy_observability (run_id INTEGER, vacancy_key TEXT, role_fit_verdict TEXT)")
        connection.execute("INSERT INTO vacancy_observability VALUES (1, 'k1', 'accept')")
    slack = Slack()
    slack.crash_after_post = True
    with pytest.raises(RuntimeError, match="crash after Slack"):
        report.publish_report(source, releases, RELEASE, slack, now=NOW, db_path=db)
    frozen = (attempt / "report.json").read_bytes()
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE vacancy_observability SET role_fit_verdict = 'reject'")
    receipt = report.publish_report(source, releases, RELEASE, slack, now=NOW, db_path=db)
    assert receipt["state"] == "reported"
    assert slack.posts == 1
    assert (attempt / "report.json").read_bytes() == frozen


def test_crash_before_report_intent_can_rebuild_unpublished_diagnostic(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, releases, attempt, _ = fixture(tmp_path)
    db = tmp_path / "state.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE vacancy_observability (run_id INTEGER, vacancy_key TEXT, role_fit_verdict TEXT)")
        connection.execute("INSERT INTO vacancy_observability VALUES (1, 'k1', 'accept')")
    original = report._atomic_json
    crashed = False

    def crash_on_intent(path: Path, value: dict) -> None:
        nonlocal crashed
        if path.name == "receipt.json" and "report_intent" in value and not crashed:
            crashed = True
            raise RuntimeError("crash before report intent")
        original(path, value)

    monkeypatch.setattr(report, "_atomic_json", crash_on_intent)
    with pytest.raises(RuntimeError, match="crash before report intent"):
        report.publish_report(source, releases, RELEASE, Slack(), now=NOW, db_path=db)
    before = (attempt / "report.json").read_bytes()
    assert "report_intent" not in publish.load_receipt(attempt)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE vacancy_observability SET role_fit_verdict = 'reject'")
    receipt = report.publish_report(source, releases, RELEASE, Slack(), now=NOW, db_path=db)
    assert receipt["state"] == "reported"
    assert (attempt / "report.json").read_bytes() != before


def test_duplicate_report_voids_only_report_and_scan_error_is_retryable(tmp_path: Path) -> None:
    source, releases, attempt, _ = fixture(tmp_path)
    slack = Slack()
    slack.scan_error = True
    with pytest.raises(report.ReportError, match="scan"):
        report.publish_report(source, releases, RELEASE, slack, now=NOW)
    assert publish.load_receipt(attempt)["state"] == "imported"
    assert "report_intent" in publish.load_receipt(attempt)
    assert issued.issued_keys(releases, require_seed=False) == {"k1", "k2", "k5"}
    slack.scan_error = False
    assert report.publish_report(source, releases, RELEASE, slack, now=NOW)["state"] == "reported"

    source2, releases2, attempt2, _ = fixture(tmp_path / "duplicate")
    slack2 = Slack()
    artifact = publish._read_json(source2 / "shortlist.json")
    receipt = publish.load_receipt(attempt2)
    marker = report.report_marker(receipt, report.report_sha256(report.build_report(artifact, receipt)))
    slack2.messages = [
        {"ts": "1790161300.000001", "user": "UBOT", "text": marker},
        {"ts": "1790161300.000002", "user": "UBOT", "text": marker},
    ]
    with pytest.raises(report.ReportError, match="ambiguous"):
        report.publish_report(source2, releases2, RELEASE, slack2, now=NOW)
    duplicate_receipt = publish.load_receipt(attempt2)
    assert duplicate_receipt["state"] == "imported"
    assert duplicate_receipt["report_state"] == "void"


@pytest.mark.parametrize("location", ["Toronto, CA", "Tel Aviv, IL", "Bogotá, CO", "Casablanca, MA"])
def test_country_suffix_does_not_look_like_us_state(location: str) -> None:
    assert not report._defective_us_gate({
        "ruleset_version": report.DEFECTIVE_US_RULESET, "location": location, "rule_ids": [],
    })


def test_us_state_suffix_requires_country_context() -> None:
    assert report._defective_us_gate({
        "ruleset_version": report.DEFECTIVE_US_RULESET, "location": "Dallas, TX, USA", "rule_ids": [],
    })
