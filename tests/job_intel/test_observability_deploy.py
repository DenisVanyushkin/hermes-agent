from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from job_intel.observability import JobIntelObservabilityExporter
from job_intel.store import JobIntelStore

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
GRAFANA = DEPLOY / "grafana"
SYSTEMD = DEPLOY / "systemd"
MONITORING = Path("/root/.hermes/monitoring")
DOCKERFILE = DEPLOY / "docker" / "job-intel-exporter.Dockerfile"
EXPORTER_SCRIPT = DEPLOY / "docker" / "job-intel-exporter.py"


DASHBOARD_FILES = {
    "overview": GRAFANA / "job-intel-executive-intelligence-overview.json",
    "sources": GRAFANA / "job-intel-source-effectiveness.json",
    "rejections": GRAFANA / "job-intel-rejection-analytics.json",
    "company": GRAFANA / "job-intel-company-intelligence.json",
    "health": GRAFANA / "job-intel-system-health.json",
}


def test_job_intel_observability_deploy_artifacts_exist() -> None:
    expected = [
        SYSTEMD / "job-intel-metrics-exporter.service",
        DOCKERFILE,
        EXPORTER_SCRIPT,
        *DASHBOARD_FILES.values(),
    ]

    for path in expected:
        assert path.exists(), path


def test_job_intel_metrics_exporter_service_executes_wrapper_command() -> None:
    service = (SYSTEMD / "job-intel-metrics-exporter.service").read_text(encoding="utf-8")

    assert "Description=Job-intel Prometheus metrics exporter" in service
    assert "ExecStart=/usr/bin/env bash __JOB_INTEL_REPO_ROOT__/scripts/job_intel_host_wrapper.sh metrics-exporter" in service
    assert "Type=simple" in service
    assert "Restart=on-failure" in service
    assert "ReadWritePaths=/var/lib/job-intel /var/log/job-intel /etc/job-intel /var/lib/browser-desktop" in service


def test_job_intel_exporter_container_uses_plain_python_base_image() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    script = EXPORTER_SCRIPT.read_text(encoding="utf-8")

    assert "FROM python:3.11-slim" in dockerfile
    assert "pip install --no-cache-dir 'pydantic>=2,<3'" in dockerfile
    assert "/workspace/live-hermes/deploy/docker/job-intel-exporter.py" in dockerfile
    assert "hermes-agent" not in dockerfile

    assert "from job_intel.observability import JobIntelObservabilityExporter" in script
    assert "from job_intel.store import JobIntelStore" in script
    assert "from job_intel.runtime import resolve_db_path" in script
    assert "from job_intel.cli import" not in script


def test_monitoring_compose_uses_plain_python_job_intel_exporter() -> None:
    compose = (MONITORING / "docker-compose.yml").read_text(encoding="utf-8")

    assert "context: /home/hermes/.hermes/hermes-agent" in compose
    assert "dockerfile: deploy/docker/job-intel-exporter.Dockerfile" in compose
    assert "/workspace/live-hermes/deploy/docker/job-intel-exporter.py" in compose
    assert "JOB_INTEL_DB_PATH=/root/.hermes/job_intel/job_intel.sqlite3" in compose
    assert "/home/hermes/.hermes/job_intel:/root/.hermes/job_intel:ro" in compose
    assert "HERMES_UID=0" not in compose
    assert "HERMES_GID=0" not in compose


def test_job_intel_exporter_returns_fallback_metrics_on_render_failure(tmp_path: Path) -> None:
    store = JobIntelStore(tmp_path / "job_intel.sqlite3")
    exporter = JobIntelObservabilityExporter(store)
    exporter.render_text = lambda: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[method-assign]

    payload = exporter.render_http_payload()

    assert "job_intel_exporter_up 0" in payload
    assert "job_intel_exporter_render_errors_total 1" in payload


def test_job_intel_exporter_reads_bootstrapped_sqlite_database_in_read_only_mode(tmp_path: Path) -> None:
    store = JobIntelStore(tmp_path / "job_intel.sqlite3")
    store.bootstrap()
    exporter = JobIntelObservabilityExporter(store)

    payload = exporter.render_text()

    assert "job_intel_vacancies_found_24h" in payload
    assert store.list_tables(read_only=True)


def test_job_intel_exporter_handles_schema_mismatch_in_read_only_database(tmp_path: Path) -> None:
    db_path = tmp_path / "job_intel.sqlite3"
    sqlite3.connect(db_path).close()
    exporter = JobIntelObservabilityExporter(JobIntelStore(db_path))

    payload = exporter.render_text()

    assert "job_intel_vacancies_found_24h 0" in payload
    assert "job_intel_daily_run_success_rate_7d" in payload


def test_observability_dashboards_reference_key_metrics() -> None:
    overview = json.loads(DASHBOARD_FILES["overview"].read_text(encoding="utf-8"))
    sources = json.loads(DASHBOARD_FILES["sources"].read_text(encoding="utf-8"))
    rejections = json.loads(DASHBOARD_FILES["rejections"].read_text(encoding="utf-8"))
    company = json.loads(DASHBOARD_FILES["company"].read_text(encoding="utf-8"))
    health = json.loads(DASHBOARD_FILES["health"].read_text(encoding="utf-8"))

    assert overview["title"] == "Job Intel - Executive Intelligence Overview"
    assert sources["title"] == "Job Intel - Source Effectiveness"
    assert rejections["title"] == "Job Intel - Rejection Analytics"
    assert company["title"] == "Job Intel - Company Intelligence"
    assert health["title"] == "Job Intel - System Health"

    assert len(overview["panels"]) >= 5
    assert any("job_intel_vacancies_found_24h" in json.dumps(panel) for panel in overview["panels"])
    assert any("job_intel_source_found_7d" in json.dumps(panel) for panel in sources["panels"])
    assert any("job_intel_rejections_7d" in json.dumps(panel) for panel in rejections["panels"])
    assert any("job_intel_companies_tier_total" in json.dumps(panel) for panel in company["panels"])
    assert any("job_intel_daily_run_success_rate_7d" in json.dumps(panel) for panel in health["panels"])

    for dashboard in (overview, sources, rejections, company, health):
        assert "${DS_PROMETHEUS}" not in json.dumps(dashboard)
        assert any(panel.get("datasource") == "Prometheus" for panel in dashboard["panels"] if isinstance(panel, dict))
