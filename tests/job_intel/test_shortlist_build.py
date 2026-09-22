"""The batch must be reproducible, capped, deduplicated and fixed by its digest."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "job_intel_shortlist_build.py"
spec = importlib.util.spec_from_file_location("job_intel_shortlist_build", MODULE_PATH)
assert spec and spec.loader
builder = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = builder
spec.loader.exec_module(builder)


def row(key: str, company: str, title: str, first_seen: str, location: str = "London") -> dict:
    return {
        "vacancy_key": key,
        "company": company,
        "title": title,
        "location": location,
        "source": "LinkedIn",
        "url": f"https://example.test/{key}",
        "role_fit_rules_json": json.dumps(
            {"ruleset_version": "rf1-test", "rule_ids": ["software_product_leadership"]}
        ),
        "first_seen_at": first_seen,
        "last_seen_at": first_seen,
        "posted_at": None,
    }


def test_newest_first_with_a_total_order() -> None:
    rows = [
        row("a", "Acme", "VP Product", "2026-09-01T00:00:00+00:00"),
        row("c", "Cyan", "Head of Product", "2026-09-03T00:00:00+00:00"),
        row("b", "Beta", "Head of Product", "2026-09-02T00:00:00+00:00"),
    ]
    assert [item["company"] for item in builder.select_batch(rows)] == ["Cyan", "Beta", "Acme"]
    # Reordering the input cannot change the batch.
    assert builder.select_batch(rows) == builder.select_batch(list(reversed(rows)))


def test_one_role_in_two_cities_is_one_role() -> None:
    rows = [
        row("a", "Xero", "Head of Product - AI Shared Services", "2026-09-03T00:00:00+00:00", "Auckland"),
        row("b", "Xero", "Head of Product - AI Shared Services", "2026-09-03T00:00:00+00:00", "Wellington"),
    ]
    batch = builder.select_batch(rows)
    assert len(batch) == 1


def test_one_company_cannot_take_the_whole_batch() -> None:
    """okx had seven accepted roles in run 510; three of them may travel, not all."""
    rows = [row(f"k{index}", "okx", f"Product Director {index}", "2026-09-03T00:00:00+00:00") for index in range(7)]
    rows.append(row("other", "wise", "Product Lead", "2026-09-02T00:00:00+00:00"))
    batch = builder.select_batch(rows)
    companies = [item["company"] for item in batch]
    assert companies.count("okx") == builder.MAX_PER_COMPANY == 3
    assert companies.count("wise") == 1


def test_cap_is_hard_and_shortage_is_not_filled() -> None:
    many = [
        row(f"k{index}", f"Company{index:02d}", "VP Product", f"2026-09-{index % 28 + 1:02d}T00:00:00+00:00")
        for index in range(builder.BATCH_CAP + 5)
    ]
    assert len(builder.select_batch(many)) == builder.BATCH_CAP

    few = [row("k1", "Solo", "VP Product", "2026-09-03T00:00:00+00:00")]
    assert len(builder.select_batch(few)) == 1


def test_requisition_numbers_do_not_defeat_dedup() -> None:
    rows = [
        row("a", "Coupa", "Product Management Director - 11581", "2026-09-03T00:00:00+00:00"),
        row("b", "Coupa", "Product Management Director - 11999", "2026-09-03T00:00:00+00:00"),
    ]
    assert len(builder.select_batch(rows)) == 1


def test_digest_ignores_build_time_but_tracks_content() -> None:
    artifact = {
        "artifact": "job_intel_shortlist",
        "run_id": 511,
        "built_at": "2026-09-22T04:00:00+00:00",
        "items": [{"position": 1, "company": "Acme"}],
    }
    same_content_later = dict(artifact, built_at="2026-09-22T09:00:00+00:00")
    changed = dict(artifact, items=[{"position": 1, "company": "Other"}])

    assert builder.digest(artifact) == builder.digest(same_content_later)
    assert builder.digest(artifact) != builder.digest(changed)


def test_build_reads_only_accepted_rows_of_the_pinned_run(tmp_path) -> None:
    path = tmp_path / "shortlist.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE vacancy_observability (run_id INTEGER, vacancy_key TEXT, company TEXT, title TEXT,"
        " location TEXT, source TEXT, url TEXT, canonical_url TEXT, role_fit_verdict TEXT, role_fit_rules_json TEXT)"
    )
    conn.execute(
        "CREATE TABLE vacancies (vacancy_key TEXT, first_seen_at TEXT, last_seen_at TEXT, posted_at TEXT)"
    )
    payload = json.dumps({"ruleset_version": "rf1-test", "rule_ids": ["software_product_leadership"]})
    conn.executemany(
        "INSERT INTO vacancy_observability VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (511, "k1", "Acme", "VP Product", "London", "LinkedIn", "u1", "u1", "accept", payload),
            (511, "k2", "Beta", "Product Marketing Lead", "Berlin", "Greenhouse", "u2", "u2", "reject", payload),
            (510, "k3", "Older", "VP Product", "Madrid", "LinkedIn", "u3", "u3", "accept", payload),
        ],
    )
    conn.executemany(
        "INSERT INTO vacancies VALUES (?,?,?,?)",
        [("k1", "2026-09-21T00:00:00+00:00", "2026-09-21T00:00:00+00:00", None),
         ("k3", "2026-09-20T00:00:00+00:00", "2026-09-20T00:00:00+00:00", None)],
    )
    conn.commit()
    conn.close()

    artifact = builder.build(builder.connect_read_only(path), 511, "deadbeef")
    assert artifact["accepted_total"] == 1
    assert [item["company"] for item in artifact["items"]] == ["Acme"]
    assert artifact["ruleset_versions"] == ["rf1-test"]
    assert artifact["commit"] == "deadbeef"
    assert artifact["short_of_cap"] is True


def test_already_issued_roles_are_dropped_before_the_cap() -> None:
    """Filtering after the cap would let seen roles consume it and shrink the release."""
    rows = [
        row(f"k{index}", f"Company{index:02d}", "VP Product", f"2026-09-{index % 28 + 1:02d}T00:00:00+00:00")
        for index in range(builder.BATCH_CAP + 3)
    ]
    issued = frozenset(item["vacancy_key"] for item in rows[:3])

    batch = builder.select_batch(rows, issued_keys=issued)

    assert len(batch) == builder.BATCH_CAP
    assert not issued & {item["vacancy_key"] for item in batch}


def test_issued_keys_default_to_none_supplied() -> None:
    rows = [row("k1", "Acme", "VP Product", "2026-09-03T00:00:00+00:00")]
    assert len(builder.select_batch(rows)) == 1
