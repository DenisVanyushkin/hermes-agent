"""API field mapping preserves the existing Vacancy and dedup contracts."""
import json
from pathlib import Path

from job_intel.dedup import canonical_vacancy_key
from job_intel.models import Vacancy
from job_intel.sources import hh_item_to_vacancy, hh_item_to_vacancy_filtered


FIX = Path(__file__).resolve().parents[1] / "fixtures" / "job_intel"


def _item():
    return json.loads((FIX / "hh_api_search_item.json").read_text())


def test_uses_alternate_url_not_the_api_url():
    vacancy = hh_item_to_vacancy(_item(), None)
    assert vacancy.url.startswith("https://hh.ru/vacancy/")
    assert "api.hh.ru" not in vacancy.url


def test_maps_company_title_location_salary():
    vacancy = hh_item_to_vacancy(_item(), None)
    assert vacancy.source == "headhunter"
    assert vacancy.company == "Acme Payments"
    assert vacancy.title == "Head of Product"
    assert vacancy.location == "Алматы"
    assert vacancy.salary


def test_description_is_empty_without_a_detail_payload():
    assert hh_item_to_vacancy(_item(), None).description == ""


def test_description_comes_from_detail_and_is_plain_text():
    detail = json.loads((FIX / "hh_api_detail.json").read_text())
    vacancy = hh_item_to_vacancy(_item(), detail)
    assert len(vacancy.description) > 200
    assert "<p>" not in vacancy.description
    assert "&amp;" not in vacancy.description


def test_mapping_does_not_persist_raw_api_payloads():
    vacancy = hh_item_to_vacancy(_item(), _item())

    assert "raw_item" not in vacancy.metadata
    assert "raw_detail" not in vacancy.metadata


def test_archived_flag_is_preserved_in_metadata():
    item = _item()
    item["archived"] = True
    assert hh_item_to_vacancy(item, None).metadata["archived"] is True


def test_vacancy_key_matches_the_existing_browser_era_row():
    api_vacancy = hh_item_to_vacancy(_item(), None)
    legacy = Vacancy(
        source="headhunter",
        source_id="old",
        company=api_vacancy.company,
        title=api_vacancy.title,
        location=api_vacancy.location,
        url=api_vacancy.url + "?query=foo&hhtmFrom=vacancy_search_list",
        description="",
    )
    assert canonical_vacancy_key(api_vacancy) == canonical_vacancy_key(legacy)


def test_non_executive_titles_are_filtered_at_ingest():
    item = _item()
    item["name"] = "Курьер"
    assert hh_item_to_vacancy_filtered([item]) == []
