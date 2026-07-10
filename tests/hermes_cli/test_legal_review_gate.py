"""Tests for the legal answer review gate (stage 1 deterministic + stage 2 verdict)."""

from pathlib import Path

from hermes_cli.legal_review_gate import verify_citations
from tools.legal_research.adilet_client import AdiletClient, AdiletNetworkError, FetchResult

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "legal_research"


def make_client(fixture_map: dict[str, str]) -> AdiletClient:
    client = AdiletClient()

    def fake_fetch(url: str) -> FetchResult:
        for marker, filename in fixture_map.items():
            if marker in url:
                return FetchResult(
                    url=url,
                    text=(FIXTURES / filename).read_text(encoding="utf-8"),
                    warnings=[],
                )
        raise AssertionError(f"unexpected fetch: {url}")

    client.fetch = fake_fetch
    return client


CODE_CLIENT_MAP = {
    "/rus/docs/K1500000414/info": "info_page.html",
    "/rus/docs/K1500000414": "code_act.html",
}


def test_valid_citation_passes():
    client = make_client(CODE_CLIENT_MAP)
    evidence = verify_citations(
        [{"doc_id": "K1500000414", "article": "77",
          "quote": "Привлечение к сверхурочным работам допускается только с письменного согласия работника",
          "claim": "сверхурочная работа требует письменного согласия"}],
        client=client,
    )
    assert evidence[0]["checks_failed"] == []
    assert evidence[0]["article_found"] is True
    assert evidence[0]["quote_verified"] is True


def test_fabricated_article_is_caught_without_llm():
    client = make_client(CODE_CLIENT_MAP)
    evidence = verify_citations(
        [{"doc_id": "K1500000414", "article": "9999",
          "quote": None, "claim": "выдуманная статья"}],
        client=client,
    )
    assert "nonexistent_article" in evidence[0]["checks_failed"]


def test_misquoted_norm_is_caught():
    client = make_client(CODE_CLIENT_MAP)
    evidence = verify_citations(
        [{"doc_id": "K1500000414", "article": "77",
          "quote": "Работодатель вправе привлекать к сверхурочной работе без ограничений и без согласия в любое время",
          "claim": "сверхурочная работа не ограничена"}],
        client=client,
    )
    assert "misquoted_norm" in evidence[0]["checks_failed"]


def test_network_error_is_unverifiable_not_refuted():
    client = AdiletClient()

    def broken_fetch(url):
        raise AdiletNetworkError("boom")

    client.fetch = broken_fetch
    evidence = verify_citations(
        [{"doc_id": "K1500000414", "article": "77", "quote": None, "claim": "x"}],
        client=client,
    )
    assert evidence[0]["checks_failed"] == []
    assert evidence[0]["unverifiable"]
