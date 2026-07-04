"""Tests for read-only vacancy content fetching."""

from __future__ import annotations

from types import SimpleNamespace

import hermes_cli.recruiter_vacancy_fetch as fetch_mod
from hermes_cli.recruiter_vacancy_fetch import fetch_vacancy_details


def _fake_get(payload=None, text=""):
    def _get(url):
        return SimpleNamespace(json=lambda: payload, text=text)

    return _get


class TestUrlRouting:
    def test_hh(self, monkeypatch) -> None:
        monkeypatch.setattr(
            fetch_mod,
            "_get",
            _fake_get(
                {
                    "name": "CPO",
                    "employer": {"name": "Айтигенио"},
                    "area": {"name": "Москва"},
                    "description": "<p>Ведущий <b>Edtech</b></p>",
                    "schedule": {"id": "remote"},
                }
            ),
        )
        details = fetch_vacancy_details("https://hh.ru/vacancy/134606080")
        assert details["fetch_status"] == "ok"
        assert details["title"] == "CPO"
        assert details["company"] == "Айтигенио"
        assert "Edtech" in details["description_text"]
        assert details["remote"] is True

    def test_greenhouse(self, monkeypatch) -> None:
        monkeypatch.setattr(
            fetch_mod,
            "_get",
            _fake_get({"title": "Head of Product", "location": {"name": "Chicago"}, "content": "&lt;p&gt;Payments&lt;/p&gt;"}),
        )
        details = fetch_vacancy_details("https://job-boards.greenhouse.io/adyen/jobs/7077880")
        assert details["fetch_status"] == "ok"
        assert details["title"] == "Head of Product"
        assert details["company"] == "adyen"

    def test_ashby_matches_job_by_id(self, monkeypatch) -> None:
        job_id = "16b7b0fd-a514-4c3a-802a-15dcf3b6fb64"
        monkeypatch.setattr(
            fetch_mod,
            "_get",
            _fake_get(
                {
                    "jobs": [
                        {"id": "other", "jobUrl": "https://jobs.ashbyhq.com/airwallex/other"},
                        {
                            "id": job_id,
                            "title": "Product Lead, T:0 (Agentic) Finance",
                            "location": "San Francisco",
                            "descriptionHtml": "<p>Agentic finance products</p>",
                            "isRemote": False,
                        },
                    ]
                }
            ),
        )
        details = fetch_vacancy_details(f"https://jobs.ashbyhq.com/airwallex/{job_id}")
        assert details["fetch_status"] == "ok"
        assert "Agentic finance" in details["description_text"]

    def test_generic_jobposting_jsonld(self, monkeypatch) -> None:
        html = (
            '<html><script type="application/ld+json">'
            '{"@type": "JobPosting", "title": "PM", "description": "<p>Own roadmap</p>",'
            ' "hiringOrganization": {"name": "Acme"}}'
            "</script></html>"
        )
        monkeypatch.setattr(fetch_mod, "_get", _fake_get(text=html))
        details = fetch_vacancy_details("https://careers.example.com/jobs/1")
        assert details["fetch_status"] == "ok"
        assert details["company"] == "Acme"
        assert "Own roadmap" in details["description_text"]

    def test_fetch_failure_is_soft(self, monkeypatch) -> None:
        def _boom(url):
            raise RuntimeError("network down")

        monkeypatch.setattr(fetch_mod, "_get", _boom)
        details = fetch_vacancy_details("https://hh.ru/vacancy/1")
        assert details["fetch_status"].startswith("fetch_failed")


class TestHelperEnrichment:
    def test_enrich_adds_content_and_no_warning(self, monkeypatch) -> None:
        from hermes_cli import recruiter_decision_execution as exec_mod

        monkeypatch.setattr(
            fetch_mod,
            "fetch_vacancy_details",
            lambda url: {"fetch_status": "ok", "title": "CPO", "description_text": "text"},
        )
        request = exec_mod.build_decision_request_from_message("оцени https://hh.ru/vacancy/5")
        warnings = exec_mod._enrich_vacancy_source(request)
        assert warnings == []
        assert request.vacancy_source["title"] == "CPO"
        assert request.vacancy_source["description_text"] == "text"

    def test_enrich_failure_warns(self, monkeypatch) -> None:
        from hermes_cli import recruiter_decision_execution as exec_mod

        monkeypatch.setattr(fetch_mod, "fetch_vacancy_details", lambda url: {"fetch_status": "fetch_failed:X"})
        request = exec_mod.build_decision_request_from_message("оцени https://hh.ru/vacancy/5")
        warnings = exec_mod._enrich_vacancy_source(request)
        assert warnings and "could not be fetched" in warnings[0]


class TestDeadPostingDetection:
    def test_redirect_to_board_detected(self, monkeypatch) -> None:
        from types import SimpleNamespace

        monkeypatch.setattr(
            fetch_mod,
            "_get",
            lambda url: SimpleNamespace(
                url="https://job-boards.greenhouse.io/gitlab?error=true", text="<html>board</html>", json=lambda: {}
            ),
        )
        details = fetch_mod._fetch_generic("https://job-boards.greenhouse.io/gitlab/jobs/1")
        assert details["fetch_status"] == "posting_unavailable"

    def test_company_from_url(self) -> None:
        from hermes_cli.recruiter_vacancy_fetch import company_from_vacancy_url

        assert company_from_vacancy_url("https://job-boards.greenhouse.io/gitlab/jobs/1") == "gitlab"
        assert company_from_vacancy_url("https://jobs.ashbyhq.com/airwallex/16b7b0fd-a514-4c3a-802a-15dcf3b6fb64") == "airwallex"
        assert company_from_vacancy_url("https://hh.ru/vacancy/1") is None
