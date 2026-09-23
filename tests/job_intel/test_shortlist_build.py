"""The batch must be reproducible, capped, deduplicated and fixed by its digest."""

from __future__ import annotations

import importlib.util
from datetime import date, datetime, timezone
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
    batch, _ = builder.select_batch(rows)
    assert [item["company"] for item in batch] == ["Cyan", "Beta", "Acme"]
    # Reordering the input cannot change the batch.
    assert builder.select_batch(rows) == builder.select_batch(list(reversed(rows)))


def test_one_role_in_two_cities_is_one_role() -> None:
    rows = [
        row("a", "Xero", "Head of Product - AI Shared Services", "2026-09-03T00:00:00+00:00", "Auckland"),
        row("b", "Xero", "Head of Product - AI Shared Services", "2026-09-03T00:00:00+00:00", "Wellington"),
    ]
    batch, _ = builder.select_batch(rows)
    assert len(batch) == 1


def test_one_company_cannot_take_the_whole_batch() -> None:
    """okx had seven accepted roles in run 510; three of them may travel, not all."""
    rows = [row(f"k{index}", "okx", f"Product Director {index}", "2026-09-03T00:00:00+00:00") for index in range(7)]
    rows.append(row("other", "wise", "Product Lead", "2026-09-02T00:00:00+00:00"))
    batch, _ = builder.select_batch(rows)
    companies = [item["company"] for item in batch]
    assert companies.count("okx") == builder.MAX_PER_COMPANY == 3
    assert companies.count("wise") == 1


def test_cap_is_hard_and_shortage_is_not_filled() -> None:
    many = [
        row(f"k{index}", f"Company{index:02d}", "VP Product", f"2026-09-{index % 28 + 1:02d}T00:00:00+00:00")
        for index in range(builder.BATCH_CAP + 5)
    ]
    assert len(builder.select_batch(many)[0]) == builder.BATCH_CAP

    few = [row("k1", "Solo", "VP Product", "2026-09-03T00:00:00+00:00")]
    assert len(builder.select_batch(few)[0]) == 1


def test_requisition_numbers_do_not_defeat_dedup() -> None:
    rows = [
        row("a", "Coupa", "Product Management Director - 11581", "2026-09-03T00:00:00+00:00"),
        row("b", "Coupa", "Product Management Director - 11999", "2026-09-03T00:00:00+00:00"),
    ]
    assert len(builder.select_batch(rows)[0]) == 1


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

    batch, _ = builder.select_batch(rows, issued_keys=issued)

    assert len(batch) == builder.BATCH_CAP
    assert not issued & {item["vacancy_key"] for item in batch}


def test_issued_keys_default_to_none_supplied() -> None:
    rows = [row("k1", "Acme", "VP Product", "2026-09-03T00:00:00+00:00")]
    assert len(builder.select_batch(rows)[0]) == 1


def test_every_held_back_row_is_named_with_its_reason() -> None:
    """A suppressed role and a role that was never a candidate must not look alike."""
    rows = [row("issued", "Acme", "VP Product", "2026-09-09T00:00:00+00:00")]
    rows += [
        row("x1", "Xero", "Head of Product - AI", "2026-09-08T00:00:00+00:00", "Auckland"),
        row("x2", "Xero", "Head of Product - AI", "2026-09-07T00:00:00+00:00", "Wellington"),
    ]
    rows += [
        row(f"okx{index}", "okx", f"Product Director {index}", f"2026-09-0{index + 1}T00:00:00+00:00")
        for index in range(5)
    ]

    batch, suppressed = builder.select_batch(
        rows, cap=4, max_per_company=3, issued_keys=frozenset({"issued"})
    )

    assert [item["vacancy_key"] for item in suppressed["already_issued"]] == ["issued"]
    assert [item["vacancy_key"] for item in suppressed["title_collapsed"]] == ["x2"]
    assert suppressed["title_collapsed"][0]["collapsed_into"] == "x1"
    # okx fills its allowance of three, so nothing of it is company-capped here;
    # the cap check precedes the per-company check, so the remainder is over_cap.
    assert [item["vacancy_key"] for item in suppressed["company_capped"]] == []
    assert [item["vacancy_key"] for item in suppressed["over_cap"]] == ["okx1", "okx0"]
    assert [item["vacancy_key"] for item in batch] == ["x1", "okx4", "okx3", "okx2"]
    assert len(batch) == 4


def test_company_cap_is_reported_when_it_actually_binds() -> None:
    rows = [
        row(f"okx{index}", "okx", f"Product Director {index}", f"2026-09-0{index + 1}T00:00:00+00:00")
        for index in range(5)
    ]
    rows.append(row("wise", "wise", "Product Lead", "2026-09-01T00:00:00+00:00"))

    batch, suppressed = builder.select_batch(rows, cap=10, max_per_company=3)

    assert [item["vacancy_key"] for item in suppressed["company_capped"]] == ["okx1", "okx0"]
    assert [item["company"] for item in batch] == ["okx", "okx", "okx", "wise"]


def test_weekly_census_uses_all_runs_and_latest_verdict(tmp_path) -> None:
    path = tmp_path / "weekly.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE vacancy_observability (run_id INTEGER, vacancy_key TEXT, company TEXT, title TEXT, location TEXT, source TEXT, url TEXT, canonical_url TEXT, role_fit_verdict TEXT, role_fit_rules_json TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE vacancies (vacancy_key TEXT, first_seen_at TEXT, last_seen_at TEXT, posted_at TEXT, description TEXT)")
    rules = json.dumps({"ruleset_version": "rf1-test", "rule_ids": ["one_rule"]})
    observations = [
        (1, "older", "A", "VP Product", "London", "x", "u1", "u1", "accept", rules, "2026-09-14T00:00:00+00:00"),
        (2, "change", "B", "VP Product", "London", "x", "u2", "u2", "accept", rules, "2026-09-15T10:00:00+00:00"),
        (3, "change", "B", "VP Product", "London", "x", "u2", "u2", "reject", rules, "2026-09-17T10:00:00+00:00"),
        (4, "new", "C", "Head of Product", "London", "x", "u3", "u3", "accept", rules, "2026-09-20T10:00:00+00:00"),
        (4, "posted", "F", "Product Lead", "London", "x", "u6", "u6", "accept", rules, "2026-09-20T11:00:00+00:00"),
        (4, "empty", "D", "Product Director", "London", "x", "u4", "u4", "accept", rules, "2026-09-20T10:00:00+00:00"),
        (5, "later", "E", "VP Product", "London", "x", "u5", "u5", "accept", rules, "2026-09-21T22:00:00+00:00"),
    ]
    conn.executemany("INSERT INTO vacancy_observability VALUES (?,?,?,?,?,?,?,?,?,?,?)", observations)
    conn.executemany("INSERT INTO vacancies VALUES (?,?,?,?,?)", [
        (key, "2026-09-15T00:00:00+00:00", None, None, "real job description")
        for key in ("older", "change", "new", "later")
    ] + [("posted", "2026-09-14T00:00:00+00:00", None, "2026-09-19T00:00:00+00:00", "real job description")
    ] + [("empty", "2026-09-20T00:00:00+00:00", None, None, "")])
    conn.commit()
    conn.close()

    artifact = builder.build_weekly(
        builder.connect_read_only(path), date(2026, 9, 14), "deadbeef",
        release_id="shortlist-2026-W38", issued_keys=frozenset({"older"}), _recompute=False,
    )

    assert artifact["run_ids"] == [1, 2, 3, 4]
    assert artifact["census_count"] == 5
    assert [item["vacancy_key"] for item in artifact["items"]] == ["posted", "new"]
    assert [item["vacancy_key"] for item in artifact["rejected_sample"]] == ["change"]
    assert [item["vacancy_key"] for item in artifact["suppressed"]["already_issued"]] == ["older"]
    assert [item["vacancy_key"] for item in artifact["excluded"]["empty_description"]] == ["empty"]
    assert "description" not in artifact["excluded"]["empty_description"][0]
    assert len(artifact["census"]) == artifact["census_count"]
    assert artifact["partition_count"] == artifact["census_count"]
    assert builder.digest(artifact) == builder.digest(dict(artifact, built_at="tomorrow"))
    assert artifact["items"][0]["description"] == "real job description"
    assert artifact["items"][0]["description_provenance"] == "vacancies_at_build"


def test_recent_rejected_sample_is_not_reissued(tmp_path) -> None:
    path = tmp_path / "cooldown.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE vacancy_observability (run_id INTEGER, vacancy_key TEXT, company TEXT, title TEXT, location TEXT, source TEXT, url TEXT, canonical_url TEXT, role_fit_verdict TEXT, role_fit_rules_json TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE vacancies (vacancy_key TEXT, first_seen_at TEXT, last_seen_at TEXT, posted_at TEXT, description TEXT)")
    rules = json.dumps({"ruleset_version": "rf1-test", "rule_ids": ["one_rule"]})
    for key in ("cooled", "new"):
        conn.execute("INSERT INTO vacancy_observability VALUES (?,?,?,?,?,?,?,?,?,?,?)", (1, key, key, "VP Product", "London", "LinkedIn", key, key, "reject", rules, "2026-09-18T12:00:00+00:00"))
        conn.execute("INSERT INTO vacancies VALUES (?,?,?,?,?)", (key, "2026-09-18T00:00:00+00:00", None, None, "Complete job text"))
    conn.commit()

    artifact = builder.build_weekly(
        builder.connect_read_only(path), date(2026, 9, 14), "deadbeef",
        release_id="shortlist-2026-W38", sample_cooldown_keys=frozenset({"cooled"}),
    )

    assert [row["vacancy_key"] for row in artifact["rejected_sample"]] == ["new"]
    assert [row["vacancy_key"] for row in artifact["excluded"]["sample_cooldown"]] == ["cooled"]
    assert artifact["partition_count"] == artifact["census_count"] == 2


def test_weekly_release_recomputes_verdict_from_frozen_vacancy_text(tmp_path) -> None:
    path = tmp_path / "recompute.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE vacancy_observability (run_id INTEGER, vacancy_key TEXT, company TEXT, title TEXT, location TEXT, source TEXT, url TEXT, canonical_url TEXT, role_fit_verdict TEXT, role_fit_rules_json TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE vacancies (vacancy_key TEXT, first_seen_at TEXT, last_seen_at TEXT, posted_at TEXT, description TEXT)")
    prior = json.dumps({"ruleset_version": "rf1-prior", "rule_ids": ["product"]})
    cases = (
        ("product", "VP Product", "Own product strategy, roadmap, engineering partnership and P&L for a software platform."),
        ("marketing", "Product Marketing Manager", "Create marketing campaigns and demand generation for products."),
    )
    for key, title, description in cases:
        conn.execute("INSERT INTO vacancy_observability VALUES (?,?,?,?,?,?,?,?,?,?,?)", (1, key, key, title, "London", "LinkedIn", key, key, "accept", prior, "2026-09-18T12:00:00+00:00"))
        conn.execute("INSERT INTO vacancies VALUES (?,?,?,?,?)", (key, "2026-09-18T10:00:00+00:00", None, None, description))
    conn.commit()

    artifact = builder.build_weekly(builder.connect_read_only(path), date(2026, 9, 14), "test", release_id="shortlist-2026-W38")

    assert [row["vacancy_key"] for row in artifact["items"]] == ["product"]
    assert [row["vacancy_key"] for row in artifact["rejected_sample"]] == ["marketing"]
    assert artifact["rejected_sample"][0]["observed_role_fit_verdict"] == "accept"
    assert artifact["rejected_sample"][0]["role_fit_verdict"] == "reject"
    assert artifact["rejected_sample"][0]["ruleset_version"] != "rf1-prior"


def test_weekly_window_is_berlin_local_monday() -> None:
    start, end = builder.weekly_bounds(date(2026, 9, 14))
    assert start.isoformat() == "2026-09-13T22:00:00+00:00"
    assert end.isoformat() == "2026-09-20T22:00:00+00:00"
    autumn_start, autumn_end = builder.weekly_bounds(date(2026, 10, 19))
    assert autumn_start.isoformat() == "2026-10-18T22:00:00+00:00"
    assert autumn_end.isoformat() == "2026-10-25T23:00:00+00:00"


def test_weekly_build_refuses_an_incomplete_week() -> None:
    import pytest

    with pytest.raises(ValueError, match="complete"):
        builder.build_weekly(
            None, date(2026, 9, 21), "deadbeef", release_id="shortlist-2026-W39",
            as_of=datetime(2026, 9, 23, tzinfo=timezone.utc),
        )


def test_role_dates_with_different_offsets_compare_as_instants() -> None:
    assert builder._utc_instant("2026-09-16T11:00:00-04:00") == builder._utc_instant("2026-09-16T15:00:00Z")


def test_fresh_undated_role_competes_with_older_dated_roles(tmp_path) -> None:
    path = tmp_path / "recency.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE vacancy_observability (run_id INTEGER, vacancy_key TEXT, company TEXT, title TEXT, location TEXT, source TEXT, url TEXT, canonical_url TEXT, role_fit_verdict TEXT, role_fit_rules_json TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE vacancies (vacancy_key TEXT, first_seen_at TEXT, last_seen_at TEXT, posted_at TEXT, description TEXT)")
    rules = json.dumps({"ruleset_version": "rf1-test", "rule_ids": ["software_product_leadership"]})
    for index in range(25):
        key = f"old-{index:02d}"
        conn.execute("INSERT INTO vacancy_observability VALUES (?,?,?,?,?,?,?,?,?,?,?)", (1, key, f"Company {index:02d}", "VP Product", "London", "Greenhouse", key, key, "accept", rules, "2026-09-17T12:00:00+00:00"))
        posted = {0: "1779266357514", 1: "2026-07-03 15:57:43 UTC", 2: "bad scraper date"}.get(index, "2026-07-29T18:00:00-04:00")
        conn.execute("INSERT INTO vacancies VALUES (?,?,?,?,?)", (key, "2026-09-14T00:00:00+00:00", None, posted, "Complete job text"))
    conn.execute("INSERT INTO vacancy_observability VALUES (?,?,?,?,?,?,?,?,?,?,?)", (2, "fresh", "LinkedIn role", "VP Product", "London", "LinkedIn", "fresh", "fresh", "accept", rules, "2026-09-20T10:00:00+00:00"))
    conn.execute("INSERT INTO vacancies VALUES (?,?,?,?,?)", ("fresh", "2026-09-20T09:00:00+00:00", None, None, "Complete job text"))
    conn.commit()
    artifact = builder.build_weekly(builder.connect_read_only(path), date(2026, 9, 14), "deadbeef", release_id="shortlist-2026-W38", _recompute=False)
    assert artifact["delivered_count"] == 25
    assert artifact["items"][0]["vacancy_key"] == "fresh"
    assert "fresh" not in {row["vacancy_key"] for row in artifact["suppressed"]["over_cap"]}
    keyed = {row["vacancy_key"]: row for row in artifact["items"] + artifact["suppressed"]["over_cap"]}
    assert keyed["old-00"]["posted_at_utc"] is not None
    assert keyed["old-01"]["posted_at_utc"] == "2026-07-03T15:57:43+00:00"
    assert keyed["old-02"]["posted_at_unparsed"] is True
    assert keyed["old-02"]["recency_source"] == "first_seen_at"


def test_future_scraper_date_cannot_displace_current_roles_at_cap(tmp_path) -> None:
    path = tmp_path / "future-date.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE vacancy_observability (run_id INTEGER, vacancy_key TEXT, company TEXT, title TEXT, location TEXT, source TEXT, url TEXT, canonical_url TEXT, role_fit_verdict TEXT, role_fit_rules_json TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE vacancies (vacancy_key TEXT, first_seen_at TEXT, last_seen_at TEXT, posted_at TEXT, description TEXT)")
    rules = json.dumps({"ruleset_version": "rf1-test", "rule_ids": ["one_rule"]})
    for index in range(25):
        key = f"current-{index:02d}"
        conn.execute("INSERT INTO vacancy_observability VALUES (?,?,?,?,?,?,?,?,?,?,?)", (1, key, key, "VP Product", "London", "LinkedIn", key, key, "accept", rules, "2026-09-19T12:00:00+00:00"))
        conn.execute("INSERT INTO vacancies VALUES (?,?,?,?,?)", (key, "2026-09-18T00:00:00+00:00", None, "2026-09-18T00:00:00+00:00", "Complete job text"))
    conn.execute("INSERT INTO vacancy_observability VALUES (?,?,?,?,?,?,?,?,?,?,?)", (1, "future", "future", "VP Product", "London", "LinkedIn", "future", "future", "accept", rules, "2026-09-19T12:00:00+00:00"))
    conn.execute("INSERT INTO vacancies VALUES (?,?,?,?,?)", ("future", "2026-09-14T00:00:00+00:00", None, "9999999999999", "Complete job text"))
    conn.commit()

    artifact = builder.build_weekly(builder.connect_read_only(path), date(2026, 9, 14), "test", release_id="shortlist-2026-W38", as_of=datetime(2026, 9, 21, tzinfo=timezone.utc), _recompute=False)

    assert artifact["delivered_count"] == 25
    assert "future" not in {row["vacancy_key"] for row in artifact["items"]}
    held = next(row for row in artifact["suppressed"]["over_cap"] if row["vacancy_key"] == "future")
    assert held["posted_at_future"] is True
    assert held["recency_source"] == "first_seen_at"


def test_the_audit_groups_account_for_every_input_row() -> None:
    """Disjoint by construction: each row leaves by the first matching branch."""
    rows = [
        row(f"k{index}", f"Company{index % 4:02d}", f"VP Product {index % 3}",
            f"2026-09-{index % 28 + 1:02d}T00:00:00+00:00")
        for index in range(30)
    ]
    issued = frozenset({"k0", "k1"})

    batch, suppressed = builder.select_batch(rows, cap=5, max_per_company=2, issued_keys=issued)

    seen_keys = [item["vacancy_key"] for item in batch]
    for group in suppressed.values():
        seen_keys += [item["vacancy_key"] for item in group]
    assert sorted(seen_keys) == sorted(item["vacancy_key"] for item in rows)
    assert len(seen_keys) == len(set(seen_keys))


def test_artifact_exposes_suppression_counts(tmp_path) -> None:
    path = tmp_path / "suppressed.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE vacancy_observability (run_id INTEGER, vacancy_key TEXT, company TEXT, title TEXT,"
        " location TEXT, source TEXT, url TEXT, canonical_url TEXT, role_fit_verdict TEXT, role_fit_rules_json TEXT)"
    )
    conn.execute("CREATE TABLE vacancies (vacancy_key TEXT, first_seen_at TEXT, last_seen_at TEXT, posted_at TEXT)")
    payload = json.dumps({"ruleset_version": "rf1-test", "rule_ids": ["software_product_leadership"]})
    conn.executemany(
        "INSERT INTO vacancy_observability VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (511, "a", "Xero", "Head of Product", "Auckland", "LinkedIn", "u1", "u1", "accept", payload),
            (511, "b", "Xero", "Head of Product", "Wellington", "LinkedIn", "u2", "u2", "accept", payload),
        ],
    )
    conn.executemany(
        "INSERT INTO vacancies VALUES (?,?,?,?)",
        [("a", "2026-09-21T00:00:00+00:00", "2026-09-21T00:00:00+00:00", None),
         ("b", "2026-09-20T00:00:00+00:00", "2026-09-20T00:00:00+00:00", None)],
    )
    conn.commit()
    conn.close()

    artifact = builder.build(builder.connect_read_only(path), 511, "deadbeef")

    assert artifact["delivered_count"] == 1
    assert artifact["suppressed_counts"]["title_collapsed"] == 1
    assert artifact["suppressed"]["title_collapsed"][0]["collapsed_into"] == "a"


def test_evaluation_inputs_are_pinned_beside_the_ruleset_version() -> None:
    """Identical rules with different arguments are a different evaluation."""
    repo = Path("/home/hermes/.hermes/hermes-agent")
    inputs = builder.role_fit_evaluation_inputs(repo)
    if not inputs.get("available"):
        import pytest

        pytest.skip(f"role_fit not importable here: {inputs.get('error')}")
    assert inputs["entrypoint"] == "job_intel.observability.record_daily_observability"
    assert inputs["arguments"]["owner_languages"] == ["en", "ru"]
    assert len(inputs["arguments_sha256"]) == 64


def test_digest_changes_when_the_evaluation_arguments_change() -> None:
    base = {
        "artifact": "job_intel_shortlist",
        "run_id": 511,
        "built_at": "2026-09-22T04:00:00+00:00",
        "ruleset_versions": ["rf1-test"],
        "role_fit_evaluation": {"arguments_sha256": "a" * 64},
        "items": [{"position": 1, "company": "Acme"}],
    }
    other_arguments = dict(base, role_fit_evaluation={"arguments_sha256": "b" * 64})
    assert builder.digest(base) != builder.digest(other_arguments)
