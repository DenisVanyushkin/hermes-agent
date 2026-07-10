"""Unit tests for the adilet.zan.kz client (fixtures only, no network)."""

from pathlib import Path

from tools.legal_research.adilet_client import AdiletClient, FetchResult

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "legal_research"


def make_client(fixture_map: dict[str, str]) -> AdiletClient:
    client = AdiletClient()

    def fake_fetch(url: str) -> FetchResult:
        for marker, filename in fixture_map.items():
            if marker in url:
                text = (FIXTURES / filename).read_text(encoding="utf-8")
                return FetchResult(url=url, text=text, warnings=[])
        raise AssertionError(f"unexpected fetch: {url}")

    client.fetch = fake_fetch
    return client


def test_act_text_is_chrome_free():
    client = make_client({"/rus/docs/R1200000082": "small_act.html"})
    result = client.get_act_text("R1200000082")
    assert "Полноэкранный" not in result["text"]
    assert "Скачать" not in result["text"]
    assert "Утвердить прилагаемый" in result["text"]
    assert "act_text_body_fallback_selector_used" not in result["warnings"]


def test_article_extraction_from_code():
    client = make_client({"/rus/docs/K1500000414": "code_act.html"})
    result = client.get_act_text("K1500000414", article="77")
    assert "Сверхурочная работа" in result["text"]
    assert len(result["text"]) < 20_000
    assert "Статья 78" not in result["text"]


def test_full_code_text_is_truncated():
    client = make_client({"/rus/docs/K1500000414": "code_act.html"})
    result = client.get_act_text("K1500000414")
    assert result["truncated"] is True
    assert len(result["text"]) <= 50_000
    assert result["total_chars"] > 400_000


def test_info_has_no_empty_keys():
    client = make_client({"/rus/docs/K1500000414/info": "info_page.html"})
    info = client.get_act_info("K1500000414")
    assert "" not in info
    assert info["date_adopted"] == "23.11.2015"


def test_zero_search_results_is_not_an_error():
    client = make_client({"/rus/search/docs": "zero_search.html"})
    result = client.search_acts("xyzzyqwertyzzz")
    assert result["results"] == []
    assert result["total_results"] == 0
    assert "no_results_for_query" in result["warnings"]


def test_tool_schemas_cover_seven_tools():
    names = {schema["name"] for schema in AdiletClient.tool_schemas()}
    assert names == {
        "search_acts", "get_act_text", "get_act_info", "get_act_history",
        "get_act_links", "get_act_downloads", "healthcheck_source",
    }


def test_clause_extraction_from_rules_doc():
    client = make_client({"/rus/docs/V1900018961": "rules_act.html"})
    result = client.get_act_text("V1900018961", article="12")
    assert "не допускается продавать" in result["text"]
    assert len(result["text"]) < 30_000
    assert not any(str(w).startswith("article_not_found") for w in result["warnings"])


def test_clause_extraction_returns_all_duplicate_numbered_clauses():
    client = make_client({"/rus/docs/V1900018961": "rules_act.html"})
    result = client.get_act_text("V1900018961", article="11")
    assert "Юридические лица-поставщики" in result["text"]
    assert len(result["text"]) < 30_000
    assert not any(str(w).startswith("article_not_found") for w in result["warnings"])


def test_fabricated_clause_still_not_found():
    client = make_client({"/rus/docs/V1900018961": "rules_act.html"})
    result = client.get_act_text("V1900018961", article="9999")
    assert any(str(w).startswith("article_not_found") for w in result["warnings"])
