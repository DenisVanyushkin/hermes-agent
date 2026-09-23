"""Summary failures stay visible while the full vacancy remains reviewable."""

from __future__ import annotations

import importlib.util
from pathlib import Path


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
