"""Cron-side role pin: jobs.json ``role`` field → [ROLE PIN] prompt directive."""

import pytest


class TestCreateJobRoleField:
    def test_create_job_persists_role(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from cron import jobs as cron_jobs

        job = cron_jobs.create_job(prompt="do the report", schedule="every 1d", role="scribe")
        assert job["role"] == "scribe"
        stored = cron_jobs.get_job(job["id"])
        assert stored["role"] == "scribe"

    def test_create_job_rejects_malformed_role(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from cron import jobs as cron_jobs

        with pytest.raises(ValueError):
            cron_jobs.create_job(prompt="x", schedule="every 1d", role="rm -rf /")

    def test_create_job_defaults_role_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from cron import jobs as cron_jobs

        job = cron_jobs.create_job(prompt="x", schedule="every 1d")
        assert job.get("role") is None


class TestBuildJobPromptRolePin:
    def test_prompt_contains_role_pin_directive(self):
        from cron.scheduler import _build_job_prompt

        job = {"id": "a" * 12, "name": "morning-report", "prompt": "составь отчёт", "role": "scribe"}
        prompt = _build_job_prompt(job)
        assert "[ROLE PIN: scribe]" in prompt

    def test_prompt_without_role_has_no_pin(self):
        from cron.scheduler import _build_job_prompt

        job = {"id": "a" * 12, "name": "morning-report", "prompt": "составь отчёт"}
        prompt = _build_job_prompt(job)
        assert "[ROLE PIN:" not in prompt

    def test_pinned_prompt_routes_to_pinned_role(self):
        """End-to-end: assembled cron prompt with pin selects the pinned role."""
        from cron.scheduler import _build_job_prompt
        from hermes_cli.profile_execution import _select_role

        job = {
            "id": "a" * 12,
            "name": "morning-report",
            "prompt": "сгенерируй изображение кота",  # artist bait
            "role": "scribe",
        }
        prompt = _build_job_prompt(job)
        role, _, _ = _select_role(prompt, None)
        assert role == "scribe"
