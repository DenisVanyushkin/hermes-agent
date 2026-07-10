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


def test_review_verdict_changes_requested_on_type_not_severity(tmp_path, monkeypatch):
    from hermes_cli import legal_review_gate
    monkeypatch.setattr(legal_review_gate, "_REPORT_DIR", tmp_path)

    def fake_llm(messages, provider, model):
        return ('{"verdict": "approved", "findings": [{"type": "nonexistent_article", '
                '"severity": "low", "quote": "ст. 999", "explanation": "нет такой статьи", '
                '"suggested_fix": "удалить"}], "summary": "ok"}')

    result = legal_review_gate.run_legal_review(
        question="q", answer_markdown="a", answer_kind="conclusions",
        citations=[], llm_call=fake_llm,
    )
    assert result["verdict"] == "changes_requested"
    assert result["report_path"] is not None


def test_review_unavailable_on_llm_failure_still_returns_stage1(tmp_path, monkeypatch):
    from hermes_cli import legal_review_gate
    monkeypatch.setattr(legal_review_gate, "_REPORT_DIR", tmp_path)

    def broken_llm(messages, provider, model):
        raise RuntimeError("model down")

    result = legal_review_gate.run_legal_review(
        question="q", answer_markdown="a", answer_kind="conclusions",
        citations=[], llm_call=broken_llm,
    )
    assert result["verdict"] == "review_unavailable"
    assert "stage1_evidence" in result


def test_lookup_answers_skip_llm_review(tmp_path, monkeypatch):
    from hermes_cli import legal_review_gate
    monkeypatch.setattr(legal_review_gate, "_REPORT_DIR", tmp_path)
    calls = []

    def counting_llm(messages, provider, model):
        calls.append(1)
        return '{"verdict": "approved", "findings": [], "summary": ""}'

    result = legal_review_gate.run_legal_review(
        question="q", answer_markdown="a", answer_kind="lookup",
        citations=[], llm_call=counting_llm,
    )
    assert calls == []
    assert result["verdict"] == "approved"


def test_legal_review_tier_resolves_to_terra():
    from hermes_cli.legal_review_gate import resolve_legal_review_model
    provider, model = resolve_legal_review_model()
    assert provider == "openai-codex"
    assert model == "gpt-5.6-terra"
