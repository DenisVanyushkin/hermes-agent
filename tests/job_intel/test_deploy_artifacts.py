from __future__ import annotations

from pathlib import Path

from job_intel import runtime


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"


def test_deploy_bundle_contains_required_artifacts() -> None:
    expected = [
        DEPLOY / "install_job_intel_host_runtime.sh",
        DEPLOY / "verify_job_intel_host_runtime.sh",
        DEPLOY / "env" / "job-intel.env.example",
        DEPLOY / "systemd" / "job-intel-daily.service",
        DEPLOY / "systemd" / "job-intel-daily.timer",
        DEPLOY / "systemd" / "job-intel-alert.service",
        DEPLOY / "systemd" / "job-intel-alert.timer",
        DEPLOY / "systemd" / "job-intel-health.service",
        DEPLOY / "systemd" / "job-intel-health.timer",
        DEPLOY / "systemd" / "job-intel-enrichment.service",
        DEPLOY / "systemd" / "job-intel-enrichment.timer",
        DEPLOY / "systemd" / "job-intel-market.service",
        DEPLOY / "systemd" / "job-intel-market.timer",
        DEPLOY / "systemd" / "job-intel-strategic.service",
        DEPLOY / "systemd" / "job-intel-strategic.timer",
    ]

    for path in expected:
        assert path.exists(), path


def test_install_script_enforces_secure_env_and_fail_closed_cleanup() -> None:
    script = (DEPLOY / "install_job_intel_host_runtime.sh").read_text(encoding="utf-8")
    assert "install -D -m 0600" in script
    assert "disable_timers_on_failure" in script
    assert "verify_installed_contract" in script
    assert "JOB_INTEL_DISABLE_ON_VERIFY_FAILURE" in script
    assert "job-intel-daily.timer" in script
    assert "job-intel-strategic.timer" in script
    assert "company-career" in script
    assert "would ensure directories:" in script
    assert "require_safe_path" in script
    assert "require_safe_name" in script
    assert script.index("[[ -d \"$repo_root\" ]] || fail \"repo root not found: $repo_root\"") < script.rindex("if (( dry_run )); then"), script
    assert script.rindex("if (( dry_run )); then") < script.index("[[ $EUID -eq 0 ]]"), script


def test_verifier_checks_env_mode_and_required_contract_fields() -> None:
    script = (DEPLOY / "verify_job_intel_host_runtime.sh").read_text(encoding="utf-8")
    assert "stat -c '%a'" in script
    assert "env file mode must be 0600" in script
    assert "JOB_INTEL_EXPECTED_GIT_COMMIT" in script
    assert "JOB_INTEL_BROWSER_PROFILE_DIR_COMPANY_CAREER" in script
    assert "cleanup_on_failure" in script
    assert "User=$service_user" in script
    assert "EnvironmentFile=$env_file" in script
    assert "ExecStart=/usr/bin/env bash $repo_root/scripts/job_intel_host_wrapper.sh $command_name" in script
    assert "job-intel-alert:alert" in script
    assert "job-intel-strategic:strategic" in script
    assert "fail()" in script and "cleanup_on_failure" in script
    assert 'source "$env_file"' not in script


def test_daily_timer_schedule_is_not_doubly_prefixed() -> None:
    timer = (DEPLOY / "systemd" / "job-intel-daily.timer").read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 09,17:00/5" in timer
    assert "OnCalendar=OnCalendar" not in timer


def test_service_template_uses_install_time_env_file_placeholder() -> None:
    service = (DEPLOY / "systemd" / "job-intel-daily.service").read_text(encoding="utf-8")
    assert "EnvironmentFile=__JOB_INTEL_ENV_FILE__" in service
    assert "ExecStart=/usr/bin/env bash __JOB_INTEL_REPO_ROOT__/scripts/job_intel_host_wrapper.sh daily" in service


def test_runtime_provenance_reports_company_career_profile_path(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JOB_INTEL_SERVICE_USER", raising=False)
    monkeypatch.setenv("JOB_INTEL_RUNTIME_USER", "hermes")
    monkeypatch.setenv("JOB_INTEL_WORKDIR", "/workspace/live-hermes")
    monkeypatch.setenv("JOB_INTEL_DB_PATH", str(tmp_path / "state" / "job_intel.sqlite3"))
    monkeypatch.setenv("JOB_INTEL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("JOB_INTEL_BROWSER_PROFILE_DIR", str(tmp_path / "profiles"))
    monkeypatch.setenv("JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN", str(tmp_path / "profiles" / "linkedin"))
    monkeypatch.setenv("JOB_INTEL_BROWSER_PROFILE_DIR_HH", str(tmp_path / "profiles" / "hh"))
    monkeypatch.setenv("JOB_INTEL_EXPECTED_GIT_COMMIT", "abc123")
    (tmp_path / "state").mkdir(parents=True)
    (tmp_path / "profiles" / "linkedin").mkdir(parents=True)
    (tmp_path / "profiles" / "hh").mkdir(parents=True)
    monkeypatch.setattr(runtime.pwd, "getpwnam", lambda name: type("U", (), {"pw_dir": str(tmp_path / "home" / name), "pw_uid": 1000, "pw_gid": 1000})())
    (tmp_path / "home" / "hermes").mkdir(parents=True)
    monkeypatch.setattr(runtime, "_git_commit_hash", lambda *args: "abc123")
    monkeypatch.setattr(runtime, "_module_origin", lambda name: f"/workspace/live-hermes/{name.replace('.', '/')}.py" if name in {"job_intel.runtime", "job_intel.store", "job_intel.browser_sourcing", "job_intel.cli"} else None)

    provenance = runtime.capture_runtime_provenance()

    assert provenance["runtime_contract"]["optional_browser_profile_paths"] == {
        "company_career": str(tmp_path / "profiles"),
    }
