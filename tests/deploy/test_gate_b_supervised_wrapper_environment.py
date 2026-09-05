from pathlib import Path


def test_supervised_wrapper_injects_root_owned_provider_environment_file() -> None:
    wrapper = Path(__file__).parents[2] / "scripts/job_intel_gate_b_supervised.sh"
    text = wrapper.read_text(encoding="utf-8")
    assert "--property=EnvironmentFile=/etc/job-intel/gate-b-provider.env" in text
    assert "OPENROUTER_API_KEY=" not in text
