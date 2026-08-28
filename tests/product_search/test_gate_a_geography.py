from pathlib import Path

import pytest
import yaml

import job_intel.product_search.acquisition_probe as acquisition_probe
from test_acquisition_probe import ROOT, _b2_mapping, _b2_record, _b2_summary


@pytest.mark.parametrize(
    ("location", "primary_country"),
    (
        ("San Francisco, CA", None),
        ("Indianapolis, IN", None),
        ("Denver, CO", None),
        ("Washington, DC", None),
        ("London, UK", "GB"),
    ),
)
def test_b2_does_not_treat_arbitrary_two_letter_tokens_as_country_codes(
    location: str, primary_country: str | None
) -> None:
    evidence = acquisition_probe.normalize_geography_evidence(location)

    assert evidence.primary_country == primary_country
    if primary_country is None:
        assert evidence.mentioned_countries == ()


@pytest.mark.parametrize(
    ("location", "primary_country"),
    (("Dubai, AE", "AE"), ("London, GB", "GB")),
)
def test_b2_recognizes_declared_unambiguous_country_codes(
    location: str, primary_country: str
) -> None:
    evidence = acquisition_probe.normalize_geography_evidence(location)

    assert evidence.primary_country == primary_country
    assert evidence.mentioned_countries == (primary_country,)


@pytest.mark.parametrize(
    ("location", "primary_country"),
    (
        ("São Paulo, Brazil", "BR"),
        ("Jakarta, Indonesia", "ID"),
        ("Kuala Lumpur, Malaysia", "MY"),
        ("Reykjavik, Iceland", "IS"),
        ("Vaduz, Liechtenstein", "LI"),
    ),
)
def test_b2_country_aliases_cover_verified_mapping_owners(
    location: str, primary_country: str
) -> None:
    evidence = acquisition_probe.normalize_geography_evidence(location)

    assert evidence.primary_country == primary_country

def test_b2_primary_country_does_not_follow_query_cell() -> None:
    summary = _b2_summary(
        [_b2_record("kz-1", "turkmenistan", "Almaty, Kazakhstan")],
        _b2_mapping(turkmenistan=("TM",), kazakhstan=("KZ",)),
    )

    assert summary["cells"]["turkmenistan"]["credited"] == []
    assert summary["cells"]["turkmenistan"]["rejected_country_mismatch"] == 1
    assert summary["cells"]["kazakhstan"]["credited"] == ["https://www.linkedin.com/jobs/view/kz-1"]

def test_b2_unknown_location_is_unresolved_and_not_credited() -> None:
    summary = _b2_summary(
        [_b2_record("unknown-1", "kazakhstan", "Unknown")],
        _b2_mapping(kazakhstan=("KZ",)),
    )

    evidence = summary["records"]["unknown-1"]
    assert evidence["primary_country"] is None
    assert evidence["normalization_source"] == "unresolved"
    assert summary["cells"]["kazakhstan"]["credited"] == []
    assert summary["cells"]["kazakhstan"]["geography_unknown"] == 1

def test_b2_same_semantic_geography_ignores_raw_location_text_difference() -> None:
    same_url = "https://www.linkedin.com/jobs/view/geo-merge-same"
    summary = _b2_summary(
        [
            _b2_record("sg-verbose", "singapore", "Singapore Singapore", url=same_url),
            _b2_record("sg-short", "singapore", "Singapore", url=same_url),
        ],
        _b2_mapping(singapore=("SG",)),
    )

    assert summary["records"]["sg-verbose"]["primary_country"] == "SG"
    assert summary["records"]["sg-short"]["primary_country"] == "SG"
    assert summary["cells"]["singapore"]["credited"] == [same_url]
    assert summary["cells"]["singapore"]["geography_unknown"] == 0

def test_b2_resolved_geography_wins_over_unknown_duplicate() -> None:
    same_url = "https://www.linkedin.com/jobs/view/geo-merge-known"
    summary = _b2_summary(
        [
            _b2_record("sg-unknown", "singapore", "Unknown", url=same_url),
            _b2_record("sg-known", "singapore", "Singapore Singapore", url=same_url),
        ],
        _b2_mapping(singapore=("SG",)),
    )

    assert summary["records"]["sg-unknown"]["primary_country"] == "SG"
    assert summary["records"]["sg-known"]["primary_country"] == "SG"
    assert summary["cells"]["singapore"]["credited"] == [same_url]
    assert summary["cells"]["singapore"]["geography_unknown"] == 0

def test_b2_different_resolved_geographies_become_named_unknown() -> None:
    same_url = "https://www.linkedin.com/jobs/view/geo-merge-conflict"
    summary = _b2_summary(
        [
            _b2_record("sg", "singapore", "Singapore", url=same_url),
            _b2_record("my", "singapore", "Kuala Lumpur, Malaysia", url=same_url),
        ],
        _b2_mapping(singapore=("SG",)),
    )

    evidence = summary["records"]["sg"]
    assert evidence["primary_country"] is None
    assert evidence["geography_resolution_reason"] == "conflicting_resolved_geography"
    assert summary["cells"]["singapore"]["credited"] == []
    assert summary["cells"]["singapore"]["geography_unknown"] == 1

def test_b2_ambiguous_multi_country_location_is_unknown_not_guessed() -> None:
    summary = _b2_summary(
        [_b2_record("multi-1", "dach", "Austria / Australia")],
        _b2_mapping(dach=("AT",), australia=("AU",)),
    )

    evidence = summary["records"]["multi-1"]
    assert evidence["mentioned_countries"] == ["AT", "AU"]
    assert evidence["primary_country"] is None
    assert summary["cells"]["dach"]["credited"] == []
    assert summary["cells"]["australia"]["credited"] == []

def test_b2_dach_uses_explicit_country_codes_not_name_prefix() -> None:
    summary = _b2_summary(
        [
            _b2_record("at-1", "dach", "Vienna, Austria"),
            _b2_record("au-1", "dach", "Sydney, Australia"),
        ],
        _b2_mapping(dach=("AT",), australia=("AU",)),
    )

    assert summary["cells"]["dach"]["credited"] == [
        "https://www.linkedin.com/jobs/view/at-1"
    ]
    assert summary["cells"]["dach"]["rejected_country_mismatch"] == 1

def test_b2_country_remote_stays_with_country_not_global_remote() -> None:
    summary = _b2_summary(
        [_b2_record("remote-kz", "kazakhstan", "Remote, Kazakhstan")],
        _b2_mapping(kazakhstan=("KZ",), genuinely_location_independent=()),
    )

    evidence = summary["records"]["remote-kz"]
    assert evidence["primary_country"] == "KZ"
    assert evidence["remote_scope"] == "country_remote"
    assert summary["cells"]["kazakhstan"]["credited"] == [
        "https://www.linkedin.com/jobs/view/remote-kz"
    ]
    assert summary["cells"]["genuinely_location_independent"]["credited"] == []

def test_b2_location_independent_remote_is_credited_to_global_remote() -> None:
    summary = _b2_summary(
        [_b2_record("remote-global", "dach", "Remote")],
        _b2_mapping(dach=("AT",), genuinely_location_independent=()),
    )

    evidence = summary["records"]["remote-global"]
    assert evidence["primary_country"] is None
    assert evidence["mentioned_countries"] == []
    assert evidence["remote_scope"] == "location_independent"
    assert summary["cells"]["genuinely_location_independent"]["credited"] == [
        "https://www.linkedin.com/jobs/view/remote-global"
    ]

def test_b2_non_linkedin_record_is_credited_by_primary_country() -> None:
    summary = _b2_summary(
        [_b2_record("hh-kz", "kazakhstan", "Almaty, Kazakhstan", source_family="headhunter")],
        _b2_mapping(kazakhstan=("KZ",)),
    )

    assert summary["cells"]["kazakhstan"]["credited"] == [
        "https://www.linkedin.com/jobs/view/hh-kz"
    ]

def test_b2_city_normalization_uses_closed_versioned_aliases() -> None:
    mapping = acquisition_probe.load_linkedin_geography_mapping(
        ROOT / "config/product_search/linkedin_geography.v1.yaml"
    )
    summary = _b2_summary(
        [
            _b2_record("hh-almaty", "kazakhstan", "Алматы"),
            _b2_record("hh-tashkent", "uzbekistan", "Ташкент"),
            _b2_record("remoteok-krishnagiri", "genuinely_location_independent", "Krishnagiri, "),
            _b2_record("unknown-city", "kazakhstan", "Бишкек"),
        ],
        mapping,
    )

    assert summary["records"]["hh-almaty"]["primary_country"] == "KZ"
    assert summary["records"]["hh-tashkent"]["primary_country"] == "UZ"
    assert summary["records"]["remoteok-krishnagiri"]["primary_country"] == "IN"
    assert summary["records"]["unknown-city"]["primary_country"] is None

def test_b2_city_normalization_matches_unseen_address_in_known_city() -> None:
    mapping = acquisition_probe.load_linkedin_geography_mapping(
        ROOT / "config/product_search/linkedin_geography.v1.yaml"
    )

    evidence = acquisition_probe.normalize_geography_evidence(
        "Ташкент, улица Независимости, 77",
        mapping_version=mapping.version,
        city_country_codes=mapping.city_country_codes,
    )

    assert evidence.primary_country == "UZ"

def test_b2_city_mapping_is_closed_at_city_granularity() -> None:
    mapping = acquisition_probe.load_linkedin_geography_mapping(
        ROOT / "config/product_search/linkedin_geography.v1.yaml"
    )

    assert mapping.city_country_codes == {
        "Алматы": "KZ",
        "Астана": "KZ",
        "Ташкент": "UZ",
        "Самарканд": "UZ",
        "Krishnagiri": "IN",
        "Paramaribo": "SR",
        "Tadworth": "GB",
        "Temecula": "US",
    }

def test_b2_no_identity_is_credited_to_more_than_one_cell() -> None:
    summary = _b2_summary(
        [
            _b2_record("at-1", "dach", "Vienna, Austria"),
            _b2_record("at-1-copy", "australia", "Vienna, Austria", url="https://www.linkedin.com/jobs/view/at-1/?eBP=other"),
        ],
        _b2_mapping(dach=("AT",), australia=("AU",)),
    )

    owners = summary["credited_identity_owners"]
    assert owners == {"https://www.linkedin.com/jobs/view/at-1": "dach"}
    credited_sets = [set(cell["credited"]) for cell in summary["cells"].values()]
    assert all(not (left & right) for index, left in enumerate(credited_sets) for right in credited_sets[index + 1 :])

def test_b2_mapping_rejects_country_code_overlap_at_load(tmp_path: Path) -> None:
    import yaml

    document = {
        "version": "1.0",
        "product_authority_id": "PS-SOT-2026-08-10-v1",
        "search_contract_version": "1.0.0",
        "normalization_rule_version": "1.0",
        "contamination_formula_version": "jaccard_received_v1",
        "contamination_threshold": 0.6,
        "city_country_codes": {},
        "cells": {
            "dach": {"location": "DACH", "status": "verified", "verified_at": "2026-08-26", "country_codes": ["DE"]},
            "cee": {"location": "CEE", "status": "verified", "verified_at": "2026-08-26", "country_codes": ["DE"]},
        },
    }
    path = tmp_path / "overlap.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ValueError, match="country code overlap"):
        acquisition_probe.load_linkedin_geography_mapping(path)

def test_b2_contamination_uses_received_rows_even_when_credited_is_empty() -> None:
    same_url = "https://www.linkedin.com/jobs/view/shared/?trackingId=changed"
    summary = _b2_summary(
        [
            _b2_record("tm", "turkmenistan", "Almaty, Kazakhstan", url=same_url),
            _b2_record("tj", "tajikistan", "Almaty, Kazakhstan", url=same_url),
        ],
        _b2_mapping(turkmenistan=("TM",), tajikistan=("TJ",), kazakhstan=("KZ",)),
    )

    pair = summary["pairwise"]["tajikistan|turkmenistan"]
    assert pair["jaccard"] == 1.0
    assert pair["contamination_suspected"] is True
    assert summary["cells"]["turkmenistan"]["credited"] == []
    assert summary["cells"]["tajikistan"]["credited"] == []

def test_b2_multi_country_only_control_group_has_zero_jaccard() -> None:
    same_url = "https://www.linkedin.com/jobs/view/multi/?trackingId=changed"
    summary = _b2_summary(
        [
            _b2_record("multi-1", "dach", "Austria / Australia", url=same_url),
            _b2_record("multi-2", "australia", "Austria / Australia", url=same_url),
        ],
        _b2_mapping(dach=("AT",), australia=("AU",)),
    )

    pair = summary["pairwise"]["australia|dach"]
    assert pair["jaccard"] == 0.0
    assert pair["contamination_suspected"] is False

def test_b2_multi_country_exclusion_is_symmetric() -> None:
    multi_url = "https://www.linkedin.com/jobs/view/shared-multi/?trackingId=changed"
    single_url = "https://www.linkedin.com/jobs/view/shared-single/?trackingId=changed"
    summary = _b2_summary(
        [
            _b2_record("multi-dach", "dach", "Austria / Australia", url=multi_url),
            _b2_record("single-dach", "dach", "Austria", url=single_url),
            _b2_record("multi-australia", "australia", "Austria / Australia", url=multi_url),
            _b2_record("single-australia", "australia", "Austria", url=single_url),
        ],
        _b2_mapping(dach=("AT",), australia=("AU",)),
    )

    pair = summary["pairwise"]["australia|dach"]
    assert pair["jaccard"] == 1.0
    assert pair["contamination_suspected"] is True

def test_b2_manifest_versions_must_match_mapping_contract() -> None:
    with pytest.raises(ValueError, match="normalization rule version"):
        _b2_summary(
            [_b2_record("at-1", "dach", "Vienna, Austria")],
            _b2_mapping(dach=("AT",)),
            manifest_versions={"normalization_rule_version": "old"},
        )

@pytest.mark.parametrize(
    ("target", "error"),
    [
        (
            {
                "location": None,
                "geoId": None,
                "verified_at": "2026-08-27",
                "status": "verified",
                "country_codes": ["GB"],
            },
            "verified geography target requires location or geoId",
        ),
        (
            {
                "location": "United Kingdom",
                "geoId": None,
                "verified_at": None,
                "status": "verified",
                "country_codes": ["GB"],
            },
            "verified geography target requires verified_at",
        ),
    ],
    ids=["missing-location-and-geoid", "missing-verified-at"],
)
def test_b2_verified_target_requires_evidence_and_timestamp(
    tmp_path: Path, target: dict[str, object], error: str
) -> None:
    document = {
        "version": "1.0",
        "product_authority_id": "PS-SOT-2026-08-10-v1",
        "search_contract_version": "1.0.0",
        "normalization_rule_version": "1.0",
        "contamination_formula_version": "jaccard_received_v1",
        "contamination_threshold": 0.6,
        "city_country_codes": {},
        "cells": {"uk": target},
    }
    path = tmp_path / "verified-validation.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ValueError, match=error):
        acquisition_probe.load_linkedin_geography_mapping(path)
