"""HH must no longer advertise or require any browser runtime."""
from job_intel import browser_worker, runtime


def test_browser_worker_no_longer_advertises_a_headhunter_target():
    assert "headhunter" not in browser_worker._CDP_TARGETS


def test_runtime_contract_no_longer_requires_the_hh_profile():
    required = runtime.build_runtime_contract()["required_browser_profile_paths"]
    assert "linkedin" in required
    assert "hh" not in required
    assert "headhunter" not in required
