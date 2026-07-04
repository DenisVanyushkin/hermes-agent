"""Tests for external company web research gathering."""

from __future__ import annotations

import json

import hermes_cli.recruiter_company_web_research as web_mod
from hermes_cli.recruiter_company_web_research import gather_company_web_research


def _fake_search(responses):
    calls = []

    def _search(query, limit=3):
        calls.append(query)
        return json.dumps({"success": True, "data": {"web": responses}})

    return _search, calls


class TestGatherCompanyWebResearch:
    def test_collects_and_dedupes_results(self, monkeypatch) -> None:
        items = [
            {"title": "GitLab <strong>layoffs</strong>", "url": "https://news.example/a", "description": "cut 350 jobs", "position": 1},
            {"title": "dup", "url": "https://news.example/a", "description": "dup", "position": 2},
        ]
        search, calls = _fake_search(items)
        monkeypatch.setattr(web_mod, "_load_search_tool", lambda: search, raising=False)
        import tools.web_tools as wt

        monkeypatch.setattr(wt, "web_search_tool", search)
        results, warnings = gather_company_web_research("GitLab")
        assert len(results) == 1
        assert results[0]["url"] == "https://news.example/a"
        assert "<strong>" not in results[0]["title"]
        assert len(calls) == 4  # all query templates ran
        assert warnings == []

    def test_no_company_name(self) -> None:
        results, warnings = gather_company_web_research("")
        assert results == []
        assert warnings

    def test_search_failure_is_soft(self, monkeypatch) -> None:
        import tools.web_tools as wt

        def _boom(query, limit=3):
            raise RuntimeError("no api key")

        monkeypatch.setattr(wt, "web_search_tool", _boom)
        results, warnings = gather_company_web_research("GitLab")
        assert results == []
        assert any("web search failed" in w for w in warnings)
