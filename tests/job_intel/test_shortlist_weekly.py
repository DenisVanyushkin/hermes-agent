"""The weekly entry point freezes one census and adopts it on retry."""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys


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
