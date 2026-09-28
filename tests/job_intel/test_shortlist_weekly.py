"""The weekly entry point freezes one census and adopts it on retry."""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def test_retry_keeps_the_first_frozen_census_after_database_changes(tmp_path: Path, monkeypatch) -> None:
    module_path = SCRIPTS / "job_intel_shortlist_weekly.py"
    assert module_path.is_file(), "weekly entry point is missing"
    spec = importlib.util.spec_from_file_location("job_intel_shortlist_weekly", module_path)
    assert spec and spec.loader
    weekly = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(weekly)
    monkeypatch.setattr(weekly, "load_company_blacklist", lambda db_path, repo: {})

    db = tmp_path / "jobs.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE vacancy_observability (run_id INTEGER, vacancy_key TEXT, company TEXT, title TEXT, location TEXT, source TEXT, url TEXT, canonical_url TEXT, role_fit_verdict TEXT, role_fit_rules_json TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE vacancies (vacancy_key TEXT, first_seen_at TEXT, last_seen_at TEXT, posted_at TEXT, description TEXT)")
    conn.execute("CREATE TABLE runs (id INTEGER, mode TEXT, started_at TEXT, finished_at TEXT, status TEXT, run_type TEXT)")
    conn.execute("INSERT INTO runs VALUES (1, 'daily', '2026-09-23T05:00:00+00:00', '2026-09-23T06:00:00+00:00', 'ok', 'shadow')")
    rules = json.dumps({"ruleset_version": "rf1-test", "rule_ids": ["seniority"]})
    conn.execute("INSERT INTO vacancy_observability VALUES (?,?,?,?,?,?,?,?,?,?,?)", (1, "fresh", "Acme", "VP Product", "London", "LinkedIn", "https://example.org/fresh", "https://example.org/fresh", "accept", rules, "2026-09-18T12:00:00+00:00"))
    original_description = "Own product strategy, roadmap, engineering partnership and P&L for a software platform."
    conn.execute("INSERT INTO vacancies VALUES (?,?,?,?,?)", ("fresh", "2026-09-18T10:00:00+00:00", None, None, original_description))
    conn.commit()
    conn.execute("ALTER TABLE vacancy_observability ADD COLUMN selection_boundary_reasons_json TEXT DEFAULT '[]'")
    conn.commit()
    release_root = tmp_path / "releases"
    release_root.mkdir()
    keys = ["historical"]
    import hashlib
    key_sha = hashlib.sha256(json.dumps(keys, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    (release_root / "legacy-seed-v1.json").write_text(json.dumps({"schema": "job_intel_legacy_seed_v1", "keys": keys, "count": 1, "keys_sha256": key_sha}))
    now = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
    source_root = tmp_path / "shortlist"

    first = weekly.run_weekly(
        db, source_root, release_root, now=now, deliver=False, commit="test", repo=Path(__file__).resolve().parents[2],
        summarizer=lambda title, description: "Owns the product roadmap.", summary_model="test-model",
    )
    source = source_root / "shortlist-2026-W38" / "shortlist.json"
    original_bytes = source.read_bytes()
    assert first["release_id"] == "shortlist-2026-W38"
    assert json.loads(original_bytes)["items"][0]["description"] == original_description
    assert json.loads(original_bytes)["items"][0]["summary"] == "Owns the product roadmap."
    assert json.loads(original_bytes)["summary_evaluation"]["complete"] is True

    conn.execute("UPDATE vacancies SET description = 'Changed after freezing' WHERE vacancy_key = 'fresh'")
    conn.commit()
    second = weekly.run_weekly(db, source_root, release_root, now=now, deliver=False, commit="test", repo=Path(__file__).resolve().parents[2])
    assert source.read_bytes() == original_bytes
    assert second["source_sha256"] == first["source_sha256"]


@pytest.mark.parametrize(
    ("finished_at", "error_type", "reason"),
    [
        ("2026-09-23T20:00:00+00:00", ValueError, "last 24 hours"),
        ("2026-09-28T06:00:00+00:00", ValueError, "no observations"),
        (None, sqlite3.OperationalError, "no such table: runs"),
    ],
)
def test_new_weekly_source_refuses_unusable_shadow_collection(
    tmp_path: Path, monkeypatch, finished_at: str | None,
    error_type: type[Exception], reason: str,
) -> None:
    module_path = SCRIPTS / "job_intel_shortlist_weekly.py"
    spec = importlib.util.spec_from_file_location("job_intel_shortlist_weekly", module_path)
    assert spec and spec.loader
    weekly = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(weekly)
    monkeypatch.setattr(weekly, "load_company_blacklist", lambda db_path, repo: {})

    db = tmp_path / "jobs.sqlite3"
    conn = sqlite3.connect(db)
    if finished_at is not None:
        conn.execute("CREATE TABLE runs (id INTEGER, mode TEXT, started_at TEXT, finished_at TEXT, status TEXT, run_type TEXT)")
        conn.execute("INSERT INTO runs VALUES (1, 'daily', '2026-09-23T19:00:00+00:00', ?, 'ok', 'shadow')", (finished_at,))
    conn.execute("CREATE TABLE vacancy_observability (run_id INTEGER, vacancy_key TEXT, company TEXT, title TEXT, location TEXT, source TEXT, url TEXT, canonical_url TEXT, role_fit_verdict TEXT, role_fit_rules_json TEXT, selection_boundary_reasons_json TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE vacancies (vacancy_key TEXT, first_seen_at TEXT, last_seen_at TEXT, posted_at TEXT, description TEXT)")
    conn.commit()
    conn.close()

    source_root = tmp_path / "shortlist"
    release_root = tmp_path / "releases"
    release_root.mkdir()
    import hashlib
    keys = ["historical"]
    key_sha = hashlib.sha256(json.dumps(keys, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    (release_root / "legacy-seed-v1.json").write_text(json.dumps({"schema": "job_intel_legacy_seed_v1", "keys": keys, "count": 1, "keys_sha256": key_sha}))
    alerts: list[str] = []
    with pytest.raises(error_type, match=reason):
        weekly.run_weekly(
            db, source_root, release_root,
            now=datetime(2026, 9, 28, 7, 16, tzinfo=timezone.utc),
            deliver=True, client=object(), commit="test", repo=Path(__file__).resolve().parents[2],
            stale_alert=alerts.append,
        )
    assert not (source_root / "shortlist-2026-W39").exists()
    assert len(alerts) == 1
    assert "shadow collection" in alerts[0]
    assert "shortlist-2026-W39" in alerts[0]


def test_stale_alert_uses_the_send_command(tmp_path: Path) -> None:
    module_path = SCRIPTS / "job_intel_shortlist_weekly.py"
    spec = importlib.util.spec_from_file_location("job_intel_shortlist_weekly", module_path)
    assert spec and spec.loader
    weekly = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(weekly)
    received = tmp_path / "received.txt"
    sender = tmp_path / "sender.py"
    sender.write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(received)!r}).write_text(sys.argv[1])\n"
    )
    weekly._send_stale_alert("stale weekly release", command=[sys.executable, str(sender)])
    assert received.read_text() == "stale weekly release"


def test_unreadable_weekly_database_alerts_without_freezing(tmp_path: Path) -> None:
    module_path = SCRIPTS / "job_intel_shortlist_weekly.py"
    spec = importlib.util.spec_from_file_location("job_intel_shortlist_weekly", module_path)
    assert spec and spec.loader
    weekly = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(weekly)
    release_root = tmp_path / "releases"
    release_root.mkdir()
    import hashlib
    keys = ["historical"]
    key_sha = hashlib.sha256(json.dumps(keys, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    (release_root / "legacy-seed-v1.json").write_text(json.dumps({"schema": "job_intel_legacy_seed_v1", "keys": keys, "count": 1, "keys_sha256": key_sha}))
    source_root = tmp_path / "shortlist"
    alerts: list[str] = []
    with pytest.raises(sqlite3.OperationalError):
        weekly.run_weekly(
            tmp_path / "missing.sqlite3", source_root, release_root,
            now=datetime(2026, 9, 28, 7, 16, tzinfo=timezone.utc),
            deliver=True, client=object(), commit="test", repo=Path(__file__).resolve().parents[2],
            stale_alert=alerts.append,
        )
    assert not (source_root / "shortlist-2026-W39").exists()
    assert len(alerts) == 1
    assert "shortlist-2026-W39" in alerts[0]
