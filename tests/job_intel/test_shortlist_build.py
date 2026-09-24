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
        "selection_boundary_reasons": [],
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
        " location TEXT, source TEXT, url TEXT, canonical_url TEXT, role_fit_verdict TEXT, role_fit_rules_json TEXT,"
        " selection_boundary_reasons_json TEXT)"
    )
    conn.execute(
        "CREATE TABLE vacancies (vacancy_key TEXT, first_seen_at TEXT, last_seen_at TEXT, posted_at TEXT)"
    )
    payload = json.dumps({"ruleset_version": "rf1-test", "rule_ids": ["software_product_leadership"]})
    conn.executemany(
        "INSERT INTO vacancy_observability VALUES (?,?,?,?,?,?,?,?,?,?,'[]')",
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

    artifact = builder.build(builder.connect_read_only(path), 511, "deadbeef", company_blacklist={})
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
        " location TEXT, source TEXT, url TEXT, canonical_url TEXT, role_fit_verdict TEXT, role_fit_rules_json TEXT,"
        " selection_boundary_reasons_json TEXT)"
    )
    conn.execute("CREATE TABLE vacancies (vacancy_key TEXT, first_seen_at TEXT, last_seen_at TEXT, posted_at TEXT)")
    payload = json.dumps({"ruleset_version": "rf1-test", "rule_ids": ["software_product_leadership"]})
    conn.executemany(
        "INSERT INTO vacancy_observability VALUES (?,?,?,?,?,?,?,?,?,?,'[]')",
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

    artifact = builder.build(builder.connect_read_only(path), 511, "deadbeef", company_blacklist={})

    assert artifact["delivered_count"] == 1
    assert artifact["suppressed_counts"]["title_collapsed"] == 1
    assert artifact["suppressed"]["title_collapsed"][0]["collapsed_into"] == "a"


def test_evaluation_inputs_are_pinned_beside_the_ruleset_version() -> None:
    """Identical rules with different arguments are a different evaluation."""
    repo = Path(__file__).resolve().parents[2]
    inputs = builder.role_fit_evaluation_inputs(repo)
    assert inputs["available"] is True, inputs
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


def boundary_db(tmp_path, reasons_by_key: dict[str, str | None]) -> Path:
    path = tmp_path / "boundaries.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE vacancy_observability (run_id INTEGER, vacancy_key TEXT, company TEXT, title TEXT,"
        " location TEXT, source TEXT, url TEXT, canonical_url TEXT, role_fit_verdict TEXT, role_fit_rules_json TEXT,"
        " selection_boundary_reasons_json TEXT)"
    )
    conn.execute("CREATE TABLE vacancies (vacancy_key TEXT, first_seen_at TEXT, last_seen_at TEXT, posted_at TEXT)")
    payload = json.dumps({"ruleset_version": "rf1-test", "rule_ids": ["software_product_leadership"]})
    for index, (key, reasons) in enumerate(sorted(reasons_by_key.items())):
        conn.execute(
            "INSERT INTO vacancy_observability VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (511, key, f"Company{index}", "VP Product", "London", "LinkedIn", key, key, "accept", payload, reasons),
        )
        conn.execute(
            "INSERT INTO vacancies VALUES (?,?,?,?)",
            (key, f"2026-09-{index + 1:02d}T00:00:00+00:00", f"2026-09-{index + 1:02d}T00:00:00+00:00", None),
        )
    conn.commit()
    conn.close()
    return path


def test_a_role_the_selection_boundaries_reject_is_never_delivered() -> None:
    """Run 511 delivered three okx roles while okx was explicitly blacklisted."""
    banned = row("okx1", "okx", "Product Director, Trading Infrastructure", "2026-09-09T00:00:00+00:00")
    banned["selection_boundary_reasons"] = [
        "company_blacklist", "company_blacklist:explicit", "crypto_industry_mismatch",
    ]
    rows = [banned, row("wise", "wise", "Product Lead", "2026-09-01T00:00:00+00:00")]

    batch, suppressed = builder.select_batch(rows)

    assert [item["vacancy_key"] for item in batch] == ["wise"]
    assert [item["vacancy_key"] for item in suppressed["boundary_rejected"]] == ["okx1"]
    assert suppressed["boundary_rejected"][0]["reasons"] == [
        "company_blacklist", "company_blacklist:explicit", "crypto_industry_mismatch",
    ]


def test_boundary_rejections_do_not_consume_the_cap() -> None:
    rows = [
        row(f"k{index}", f"Company{index:02d}", "VP Product", f"2026-09-{index % 28 + 1:02d}T00:00:00+00:00")
        for index in range(builder.BATCH_CAP + 3)
    ]
    for item in rows[-3:]:
        item["selection_boundary_reasons"] = ["russia_employer_country"]

    batch, suppressed = builder.select_batch(rows)

    assert len(batch) == builder.BATCH_CAP
    assert suppressed["over_cap"] == []
    assert len(suppressed["boundary_rejected"]) == 3


def test_a_role_whose_boundaries_were_never_assessed_is_held_back_by_name() -> None:
    """No reasons recorded is not the same fact as no reasons found."""
    unassessed = row("u", "Acme", "VP Product", "2026-09-09T00:00:00+00:00")
    unassessed["selection_boundary_reasons"] = None
    missing = row("m", "Beta", "VP Product", "2026-09-08T00:00:00+00:00")
    del missing["selection_boundary_reasons"]

    batch, suppressed = builder.select_batch([unassessed, missing])

    assert batch == []
    assert [item["vacancy_key"] for item in suppressed["boundary_unassessed"]] == ["u", "m"]


def test_the_release_manifest_names_boundary_rejections(tmp_path) -> None:
    path = boundary_db(tmp_path, {
        "ok": "[]",
        "banned": json.dumps(["company_blacklist", "company_blacklist:explicit"]),
        "unassessed": None,
    })

    artifact = builder.build(builder.connect_read_only(path), 511, "deadbeef", company_blacklist={})

    assert [item["vacancy_key"] for item in artifact["items"]] == ["ok"]
    assert artifact["suppressed_counts"]["boundary_rejected"] == 1
    assert artifact["suppressed_counts"]["boundary_unassessed"] == 1
    assert artifact["suppressed"]["boundary_rejected"][0]["reasons"] == [
        "company_blacklist", "company_blacklist:explicit",
    ]
    assert artifact["delivered_count"] + sum(artifact["suppressed_counts"].values()) == artifact["accepted_total"]


def test_malformed_boundary_reasons_stop_the_build(tmp_path) -> None:
    """A corrupt column must not read as 'no reasons' and let a banned role through."""
    import pytest

    for corrupt in ("not json", json.dumps({"reasons": []}), json.dumps([1, 2])):
        path = boundary_db(tmp_path, {"bad": corrupt})
        with pytest.raises(ValueError):
            builder.build(builder.connect_read_only(path), 511, "deadbeef", company_blacklist={})
        path.unlink()


def test_the_release_drops_what_the_scoring_path_marked_as_out_of_bounds(tmp_path) -> None:
    """Writer and reader together: the column the scoring path fills is the one the release reads.

    Each end has its own tests; only this one proves they are connected. The
    blacklisted role must be a role_fit accept, or the test would pass for the
    wrong reason.
    """
    from job_intel.models import Evaluation, Vacancy
    from job_intel.observability import record_daily_observability
    from job_intel.selection_boundaries import assess_selection_boundaries
    from job_intel.store import JobIntelStore

    store = JobIntelStore(tmp_path / "job-intel.sqlite3")
    store.bootstrap()
    blacklist = store.fetch_company_blacklist()

    def scored(index: int, company: str, title: str) -> tuple:
        item = Vacancy(
            source="linkedin", source_id=f"fixture-{index}", company=company, title=title,
            location="Singapore", url=f"https://www.linkedin.com/jobs/view/{index}", description=title,
        )
        assessment = assess_selection_boundaries(item, blacklisted_company_keys=blacklist)
        classification = {
            "classification": "vp_product",
            "executive_detected": True,
            "selection_boundary_reasons": list(assessment.rejection_reasons),
            "selection_boundary_unknowns": list(assessment.unknown_reasons),
        }
        return (item, Evaluation(score=60, tier="possible_fit", recommendation="needs_review"), classification, index, False)

    run_id = store.start_run("test")
    record_daily_observability(store, run_id, [
        scored(1, "Doit", "Product Lead - AI Neobank App"),
        scored(2, "Example", "Product Lead - AI Neobank App"),
    ])
    with store.connect(read_only=True) as conn:
        verdicts = dict(conn.execute(
            "SELECT company, role_fit_verdict FROM vacancy_observability WHERE run_id = ?", (run_id,)
        ).fetchall())
    assert verdicts == {"Doit": "accept", "Example": "accept"}

    artifact = builder.build(
        builder.connect_read_only(store.db_path), run_id, "deadbeef", company_blacklist=blacklist
    )

    assert [item["company"] for item in artifact["items"]] == ["Example"]
    assert [entry["company"] for entry in artifact["suppressed"]["boundary_rejected"]] == ["Doit"]
    assert "company_blacklist:explicit" in artifact["suppressed"]["boundary_rejected"][0]["reasons"]


def duplicate_db(tmp_path, members: list[tuple[str, str | None]],
                 companies: tuple[str, ...] = ()) -> Path:
    """One vacancy_key observed several times in one run under different URLs."""
    path = tmp_path / "duplicates.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE vacancy_observability (run_id INTEGER, vacancy_key TEXT, company TEXT, title TEXT,"
        " location TEXT, source TEXT, url TEXT, canonical_url TEXT, role_fit_verdict TEXT, role_fit_rules_json TEXT,"
        " selection_boundary_reasons_json TEXT)"
    )
    conn.execute("CREATE TABLE vacancies (vacancy_key TEXT, first_seen_at TEXT, last_seen_at TEXT, posted_at TEXT)")
    payload = json.dumps({"ruleset_version": "rf1-test", "rule_ids": ["software_product_leadership"]})
    for index, (url, reasons) in enumerate(members):
        company = companies[index] if companies else "Doit"
        conn.execute(
            "INSERT INTO vacancy_observability VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (511, "k", company, "Product Lead", "Tokyo", "LinkedIn", url, url, "accept", payload, reasons),
        )
    conn.execute("INSERT INTO vacancies VALUES ('k', '2026-09-21T00:00:00+00:00', '2026-09-21T00:00:00+00:00', NULL)")
    conn.commit()
    conn.close()
    return path


def test_a_clean_duplicate_cannot_mask_a_rejected_one(tmp_path) -> None:
    """SSB-1: grouping picked an arbitrary member, so [] could hide a rejection."""
    for members in (
        [("a", "[]"), ("b", json.dumps(["company_blacklist"]))],
        [("a", json.dumps(["company_blacklist"])), ("b", "[]")],
    ):
        path = duplicate_db(tmp_path, members)
        artifact = builder.build(builder.connect_read_only(path), 511, "deadbeef", company_blacklist={})
        assert artifact["items"] == []
        assert artifact["suppressed"]["boundary_rejected"][0]["reasons"] == ["company_blacklist"]
        assert artifact["accepted_total"] == 1
        path.unlink()


def test_an_unassessed_duplicate_holds_the_role_back(tmp_path) -> None:
    path = duplicate_db(tmp_path, [("a", "[]"), ("b", None)])
    artifact = builder.build(builder.connect_read_only(path), 511, "deadbeef", company_blacklist={})
    assert artifact["items"] == []
    assert artifact["suppressed_counts"]["boundary_unassessed"] == 1


def test_the_current_blacklist_applies_to_a_run_scored_before_it(tmp_path) -> None:
    """SSB-2: reasons are frozen at scoring time; a later blacklist entry must still bind."""
    path = boundary_db(tmp_path, {"old": "[]"})
    conn = sqlite3.connect(path)
    conn.execute("UPDATE vacancy_observability SET company = 'Doit'")
    conn.commit()
    conn.close()

    artifact = builder.build(
        builder.connect_read_only(path), 511, "deadbeef",
        company_blacklist={"doit": {"origin": "explicit", "negative_event_count": 0}},
    )

    assert artifact["items"] == []
    assert artifact["suppressed"]["boundary_rejected"][0]["reasons"] == [
        "company_blacklist", "company_blacklist:explicit",
    ]
    assert artifact["company_blacklist_applied"] == [{"company_key": "doit", "origin": "explicit"}]


def test_the_release_loads_the_effective_blacklist_from_the_repository(tmp_path) -> None:
    from job_intel.store import JobIntelStore

    store = JobIntelStore(tmp_path / "job-intel.sqlite3")
    store.bootstrap()
    repo = Path(__file__).resolve().parents[2]

    effective = builder.load_company_blacklist(store.db_path, repo)

    assert {"okx", "bjak", "doit", "actai", "kira"} <= set(effective)


def test_the_company_key_matches_the_scoring_path() -> None:
    from job_intel.store import canonical_company_key

    for name in ("Doit", "DoiT International", "  ActAI ", "Kira Systems", "BJAK", "OKX", "Unknown", "", "Lamoda Tech"):
        assert builder.canonical_company_key(name) == canonical_company_key(name), name


def test_the_blacklist_is_refused_from_a_foreign_checkout(tmp_path) -> None:
    import pytest

    with pytest.raises(RuntimeError, match="not from"):
        builder.load_company_blacklist(tmp_path / "db.sqlite3", tmp_path)


def test_a_missing_assessment_is_written_as_unassessed_not_as_clean(tmp_path) -> None:
    """SSB-3: the writer turned an absent classification key into [], i.e. 'clean'."""
    from job_intel.models import Evaluation, Vacancy
    from job_intel.observability import record_daily_observability
    from job_intel.store import JobIntelStore

    store = JobIntelStore(tmp_path / "job-intel.sqlite3")
    store.bootstrap()

    def scored(index: int, company: str, classification: dict) -> tuple:
        item = Vacancy(
            source="linkedin", source_id=f"fixture-{index}", company=company,
            title="Product Lead - AI Neobank App", location="Singapore",
            url=f"https://www.linkedin.com/jobs/view/{index}", description="Product Lead - AI Neobank App",
        )
        evaluation = Evaluation(score=60, tier="possible_fit", recommendation="needs_review")
        return (item, evaluation, {"classification": "vp_product", **classification}, index, False)

    run_id = store.start_run("test")
    record_daily_observability(store, run_id, [
        scored(1, "Assessed", {"selection_boundary_reasons": [], "selection_boundary_unknowns": []}),
        scored(2, "Unassessed", {}),
    ])
    with store.connect(read_only=True) as conn:
        stored = dict(conn.execute(
            "SELECT company, selection_boundary_reasons_json FROM vacancy_observability WHERE run_id = ?", (run_id,)
        ).fetchall())
    assert stored == {"Assessed": "[]", "Unassessed": None}

    artifact = builder.build(builder.connect_read_only(store.db_path), run_id, "deadbeef", company_blacklist={})

    assert [item["company"] for item in artifact["items"]] == ["Assessed"]
    assert [entry["company"] for entry in artifact["suppressed"]["boundary_unassessed"]] == ["Unassessed"]


def test_a_blacklisted_company_on_any_observation_holds_the_key_back(tmp_path) -> None:
    """SSB-2 closure: one key can carry different company labels; the representative must not decide."""
    blacklist = {"doit": {"origin": "explicit", "negative_event_count": 0}}
    for companies in (("Example", "Doit"), ("Doit", "Example")):
        path = duplicate_db(tmp_path, [("a", "[]"), ("b", "[]")], companies=companies)
        artifact = builder.build(builder.connect_read_only(path), 511, "deadbeef", company_blacklist=blacklist)
        assert artifact["items"] == [], companies
        assert artifact["suppressed"]["boundary_rejected"][0]["reasons"] == [
            "company_blacklist", "company_blacklist:explicit",
        ]
        path.unlink()


def test_importing_from_the_repository_leaves_sys_path_as_it_was(tmp_path, monkeypatch) -> None:
    """The live repository used to stay on sys.path and win every later import.

    pytest already has this checkout on sys.path, which would make the check
    vacuous; it is removed first, as it is absent in the production call.
    """
    from job_intel.store import JobIntelStore

    store = JobIntelStore(tmp_path / "job-intel.sqlite3")
    store.bootstrap()
    repo = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(sys, "path", [item for item in sys.path if Path(item or ".").resolve() != repo])
    before = list(sys.path)

    builder.role_fit_evaluation_inputs(repo)
    builder.load_company_blacklist(store.db_path, repo)

    assert sys.path == before


def test_evaluation_inputs_from_a_foreign_checkout_are_reported_not_used(tmp_path) -> None:
    inputs = builder.role_fit_evaluation_inputs(tmp_path)

    assert inputs["available"] is False
    assert "not from" in inputs["error"]
