"""Summary failures stay visible while the full vacancy remains reviewable."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


def test_summary_results_and_failures_are_frozen_per_role() -> None:
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "job_intel_shortlist_summaries.py"
    assert module_path.is_file(), "summary enrichment is missing"
    spec = importlib.util.spec_from_file_location("job_intel_shortlist_summaries", module_path)
    assert spec and spec.loader
    summaries = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(summaries)

    artifact = {
        "items": [
            {"vacancy_key": "a", "title": "VP Product", "description": "Own product strategy and P&L."},
            {"vacancy_key": "b", "title": "Head of Product", "description": "Build and lead the product team."},
        ],
        "rejected_sample": [{"vacancy_key": "c", "description": "Rejected role text."}],
    }

    def summarize(title: str, description: str) -> str:
        if title == "Head of Product":
            raise TimeoutError("provider timeout")
        return "Owns product strategy and P&L."

    enriched = summaries.enrich_summaries(artifact, summarize, model_id="test-model")
    assert enriched["items"][0]["summary"] == "Owns product strategy and P&L."
    assert enriched["items"][0]["summary_status"] == "ok"
    assert enriched["items"][1]["summary"] == ""
    assert enriched["items"][1]["summary_status"] == "error"
    assert enriched["rejected_sample"][0]["description"] == "Rejected role text."
    assert enriched["summary_evaluation"] == {
        "model_id": "test-model", "prompt_version": "weekly-shortlist-summary-v1",
        "successful": 1, "failed": 1, "complete": False,
    }
    assert "summary" not in artifact["items"][0]


def test_empty_week_cannot_claim_successful_model_summaries() -> None:
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "job_intel_shortlist_summaries.py"
    spec = importlib.util.spec_from_file_location("job_intel_shortlist_summaries", module_path)
    assert spec and spec.loader
    summaries = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(summaries)
    result = summaries.enrich_summaries({"items": [], "rejected_sample": []},
                                        lambda title, description: "unused", model_id="test-model")
    assert result["summary_evaluation"]["complete"] is False


@pytest.mark.parametrize(
    ("served_model", "accepted"),
    [
        ("openai/gpt-5-mini", True),
        ("gpt-5-mini-2026-08-01", True),
        ("openai/gpt-5-nano", False),
        ("", False),
    ],
)
def test_live_summary_requires_pinned_model_without_provider_fallback(
    monkeypatch: pytest.MonkeyPatch, served_model: str, accepted: bool,
) -> None:
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "job_intel_shortlist_summaries.py"
    spec = importlib.util.spec_from_file_location("job_intel_shortlist_summaries", module_path)
    assert spec and spec.loader
    summaries = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(summaries)

    calls: list[dict[str, object]] = []

    def create(**kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(
            model=served_model,
            choices=[SimpleNamespace(message=SimpleNamespace(content="Frozen role summary."))],
        )

    class FakeClient:
        chat = SimpleNamespace(completions=SimpleNamespace(create=create))

        def with_options(self, **kwargs: object) -> "FakeClient":
            assert kwargs == {"max_retries": 0, "timeout": 25.0}
            return self

    fake_transport = ModuleType("agent.auxiliary_client")
    fake_transport.resolve_provider_client = lambda provider, model: (FakeClient(), model)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agent.auxiliary_client", fake_transport)
    monkeypatch.setenv("JOB_INTEL_SHORTLIST_SUMMARIES_ENABLED", "1")

    summarize = summaries.live_summarizer()
    if accepted:
        assert summarize("VP Product", "Own product strategy.") == "Frozen role summary."
    else:
        with pytest.raises(ValueError, match="model"):
            summarize("VP Product", "Own product strategy.")
    assert calls[0]["model"] == "openai/gpt-5-mini"
    assert calls[0]["extra_body"] == {"provider": {"allow_fallbacks": False}}
