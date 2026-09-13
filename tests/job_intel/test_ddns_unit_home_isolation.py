from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_ddns_unit_binds_only_the_script_into_a_hidden_home() -> None:
    unit = (ROOT / "deploy/systemd/job-intel-linkedin-ddns-watchdog.service").read_text(
        encoding="utf-8"
    )

    assert "WorkingDirectory=/\n" in unit
    assert "ProtectHome=tmpfs\n" in unit
    assert (
        "BindReadOnlyPaths=__JOB_INTEL_REPO_ROOT__/scripts/"
        "job_intel_ddns_watchdog.py\n"
    ) in unit
    assert "CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW" in unit
    assert "CAP_DAC_READ_SEARCH" not in unit
