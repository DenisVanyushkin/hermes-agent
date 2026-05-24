from __future__ import annotations

from pathlib import Path

from job_intel import runtime


def test_capture_runtime_provenance_includes_expected_topology(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JOB_INTEL_RUNTIME_USER", "pn")
    monkeypatch.setenv("JOB_INTEL_WORKDIR", "/workspace/live-hermes")
    monkeypatch.setenv("JOB_INTEL_DB_PATH", str(tmp_path / "state" / "job_intel.sqlite3"))
    monkeypatch.setenv("JOB_INTEL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("JOB_INTEL_BROWSER_PROFILE_DIR", str(tmp_path / "profiles"))
    monkeypatch.setenv("JOB_INTEL_BROWSER_PROFILE_DIR_LINKEDIN", str(tmp_path / "profiles" / "linkedin"))
    monkeypatch.setenv("JOB_INTEL_BROWSER_PROFILE_DIR_HH", str(tmp_path / "profiles" / "hh"))
    monkeypatch.setenv("JOB_INTEL_EXPECTED_GIT_COMMIT", "abc123")
    monkeypatch.setenv("JOB_INTEL_CUSTOM", "visible-value")
    monkeypatch.setenv("JOB_INTEL_API_TOKEN", "super-secret")
    (tmp_path / "state").mkdir(parents=True)
    (tmp_path / "profiles" / "linkedin").mkdir(parents=True)
    (tmp_path / "profiles" / "hh").mkdir(parents=True)
    monkeypatch.setattr(runtime.socket, "gethostname", lambda: "host-1")
    monkeypatch.setattr(runtime, "_git_commit_hash", lambda *args: "abc123")
    monkeypatch.setattr(runtime, "_module_origin", lambda name: f"/workspace/live-hermes/{name.replace('.', '/')}.py" if name in {"job_intel.runtime", "job_intel.store", "job_intel.browser_sourcing", "job_intel.cli", "requests"} else None)

    provenance = runtime.capture_runtime_provenance()

    assert provenance["whoami"] == "pn"
    assert provenance["hostname"] == "host-1"
    assert provenance["pwd"] == str(tmp_path)
    assert provenance["effective_workdir"] == "/workspace/live-hermes"
    assert provenance["db_path"] == str(tmp_path / "state" / "job_intel.sqlite3")
    assert provenance["state_dir"] == str(tmp_path / "state")
    assert provenance["browser_profile_dir"] == str(tmp_path / "profiles")
    assert provenance["browser_profile_paths"]["linkedin"] == str(tmp_path / "profiles" / "linkedin")
    assert provenance["browser_profile_paths"]["headhunter"] == str(tmp_path / "profiles" / "hh")
    assert provenance["env_overrides"]["JOB_INTEL_CUSTOM"] == "visible-value"
    assert provenance["env_overrides"]["JOB_INTEL_API_TOKEN"] == "[REDACTED]"
    assert provenance["git_commit_hash"] == "abc123"
    assert provenance["imported_module_locations"]["job_intel.runtime"] == "/workspace/live-hermes/job_intel/runtime.py"
    assert provenance["imported_module_locations"]["requests"] == "/workspace/live-hermes/requests.py"
    assert isinstance(provenance["sys_path"], list)
    assert provenance["runtime_mirror_paths"]["repo_scripts_dir"].endswith("/scripts")
    assert provenance["runtime_contract"]["expected_git_commit"] == "abc123"
    assert provenance["runtime_contract"]["issues"] == []
