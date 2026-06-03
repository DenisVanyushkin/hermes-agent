from __future__ import annotations

from job_intel import cli


def test_cli_subcommands_include_new_hardening_commands() -> None:
    parser = cli.build_parser()
    subparser_action = next(action for action in parser._actions if getattr(action, "choices", None))
    assert {"doctor", "browser-health", "send-test", "retire-stale", "daily", "alert", "enrichment", "market", "strategic", "health", "metrics-exporter"}.issubset(set(subparser_action.choices))


def test_search_technical_report_includes_browser_profile_and_auth_details() -> None:
    report = cli._search_technical_report(
        {
            "linkedin": {
                "status": "ok",
                "acquisition": "browser-native",
                "session_health": {
                    "browser_profile": "/var/lib/browser-desktop/profiles/linkedin",
                    "auth_attempted": True,
                    "email_challenge_attempted": False,
                    "email_challenge_resolved": False,
                    "pages_fetched": 3,
                    "login_walls": 1,
                    "auth_redirects": 0,
                    "status": "healthy",
                },
            },
            "headhunter": {
                "status": "ok",
                "acquisition": "browser-native",
                "session_health": {
                    "browser_profile": "/var/lib/browser-desktop/profiles/hh",
                    "auth_attempted": True,
                    "email_challenge_attempted": True,
                    "email_challenge_resolved": True,
                    "pages_fetched": 2,
                    "login_walls": 0,
                    "auth_redirects": 0,
                    "status": "healthy",
                },
            },
        },
        channel="C0B3ZV4BUKC",
    )

    assert "Technical search report" in report
    assert "Slack channel: C0B3ZV4BUKC" in report
    assert "/var/lib/browser-desktop/profiles/linkedin" in report
    assert "login=yes" in report
    assert "email_challenge=no" in report
    assert "/var/lib/browser-desktop/profiles/hh" in report
    assert "email_challenge=yes (resolved=yes)" in report


def test_runtime_provenance_summary_exposes_runtime_topology() -> None:
    summary = cli._runtime_provenance_summary(
        {
            "provenance_json": '{"whoami": "pn", "hostname": "host-1", "pwd": "/cwd", "effective_workdir": "/workspace/live-hermes", "git_commit_hash": "abc123", "python_executable": "/usr/bin/python3", "db_path": "/tmp/job_intel.sqlite3", "state_dir": "/tmp/state", "browser_profile_paths": {"linkedin": "/profiles/linkedin"}, "runtime_mirror_paths": {"resolved_scripts_dir": "/root/.hermes/scripts"}, "env_overrides": {"JOB_INTEL_DB_PATH": "/tmp/job_intel.sqlite3"}, "imported_module_locations": {"job_intel.runtime": "/workspace/live-hermes/job_intel/runtime.py"}}'
        }
    )

    assert summary is not None
    assert summary["whoami"] == "pn"
    assert summary["hostname"] == "host-1"
    assert summary["effective_workdir"] == "/workspace/live-hermes"
    assert summary["browser_profile_paths"]["linkedin"] == "/profiles/linkedin"
    assert summary["runtime_mirror_paths"]["resolved_scripts_dir"] == "/root/.hermes/scripts"
    assert summary["env_overrides_count"] == 1
    assert "job_intel.runtime" in summary["imported_modules"]
