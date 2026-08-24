from __future__ import annotations

import pytest

from job_intel.product_search import gate_b


def _record(
    index: int,
    description: str,
    *,
    company: str = "Acme",
    title: str = "VP Product",
    cell_id: str = "uk",
) -> dict[str, object]:
    return {
        "payload": {
            "title": title,
            "description": description,
            "company": company,
            "cell_id": cell_id,
            "location": "London",
        },
        "evidence": {
            "run_id": "gate-a-test",
            "source_family": "greenhouse",
            "source_id": f"source-{index}",
            "query_id": "query-1",
            "raw_content_sha256": f"{index:064x}",
            "raw_reference": f"raw-evidence/{index}.json",
        },
        "canonical_identity": f"https://example.test/jobs/{index}",
    }


def test_corpus_selection_excludes_empty_title_equal_and_short_descriptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gate_b, "_cell_lanes", lambda: {"uk": "europe"})
    long_description = "A real vacancy description. " * 40
    records = [
        _record(0, ""),
        _record(1, "VP Product"),
        _record(2, "too short"),
        _record(3, long_description),
    ]

    selected = gate_b._corpus_records(records, sample_size=1)

    assert [item["source_id"] for item in selected] == ["source-3"]


def test_threshold_is_strict_and_not_a_source_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gate_b, "_cell_lanes", lambda: {"uk": "europe"})
    boundary = "x" * gate_b.MIN_ELIGIBLE_DESCRIPTION_CHARS
    selected, diagnostics = gate_b._corpus_selection(
        [
            _record(0, boundary, company="Aggregator.example"),
            _record(1, boundary + "x", company="Aggregator.example"),
        ],
        sample_size=1,
    )

    assert [item["source_id"] for item in selected] == ["source-1"]
    assert diagnostics["eligible_count"] == 1
    assert diagnostics["min_description_chars_exclusive"] == 500


def test_long_description_equal_to_title_is_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gate_b, "_cell_lanes", lambda: {"uk": "europe"})
    duplicate = "A" * 501
    selected = gate_b._corpus_records(
        [
            _record(0, duplicate, title=duplicate),
            _record(1, duplicate + " different"),
        ],
        sample_size=1,
    )

    assert [item["source_id"] for item in selected] == ["source-1"]


def test_empty_description_is_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gate_b, "_cell_lanes", lambda: {"uk": "europe"})
    selected = gate_b._corpus_records(
        [_record(0, ""), _record(1, "A" * 501)],
        sample_size=1,
    )

    assert [item["source_id"] for item in selected] == ["source-1"]


def test_collapsed_strata_are_reported_after_content_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gate_b,
        "_cell_lanes",
        lambda: {"uk": "europe", "us": "north_america"},
    )
    selected, diagnostics = gate_b._corpus_selection(
        [
            _record(0, "junk", company="Only Europe"),
            {
                **_record(1, "x" * 501, company="Only North America"),
                "payload": {
                    **_record(1, "x" * 501, company="Only North America")["payload"],
                    "cell_id": "us",
                },
            },
        ],
        sample_size=1,
    )

    assert len(selected) == 1
    assert diagnostics["collapsed_strata"]["lane"] == {
        "count": 1,
        "values": ["europe"],
    }


def test_scope_declaration_is_derived_from_selected_rows() -> None:
    global_remote = {"lane": "global_remote", "role_pattern": "adjacent"}
    regional_chief = {
        "lane": "europe_including_uk",
        "role_pattern": "chief_product",
    }

    first = gate_b._scope_declaration([global_remote])
    second = gate_b._scope_declaration([global_remote, regional_chief])

    assert first["selected_lane_counts"] == {"global_remote": 1}
    assert second["selected_lane_counts"] == {
        "europe_including_uk": 1,
        "global_remote": 1,
    }
    assert "europe_including_uk" in second["represented_search_contract_lanes"]
    assert "chief_product" not in second["unrepresented_role_patterns"]
