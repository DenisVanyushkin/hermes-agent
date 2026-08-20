from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from job_intel.product_search.gate_b_benchmark_v3 import (
    GateBBenchmarkPolicyV3,
    GateBCallStateV3,
    GateBLaunchIdentityV3,
    GateBPackageManifestV3,
    GateBTerminalKindV3,
    load_gate_b_benchmark_policy_v3,
    transition_allowed,
)


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "config/product_search/gate_b_benchmark.v3.yaml"


def test_v3_call_state_is_closed() -> None:
    assert tuple(item.value for item in GateBCallStateV3) == (
        "pending",
        "reserved",
        "dispatched",
        "success",
        "terminal_failure",
        "terminal_unknown",
    )
    assert tuple(item.value for item in GateBTerminalKindV3) == (
        "terminal_failure",
        "terminal_unknown",
    )


@pytest.mark.parametrize(
    ("source", "target", "allowed"),
    [
        ("pending", "reserved", True),
        ("reserved", "pending", True),
        ("reserved", "dispatched", True),
        ("dispatched", "success", True),
        ("dispatched", "terminal_failure", True),
        ("dispatched", "terminal_unknown", True),
        ("dispatched", "reserved", False),
        ("terminal_unknown", "dispatched", False),
        ("success", "pending", False),
    ],
)
def test_v3_transition_matrix(source: str, target: str, allowed: bool) -> None:
    actor = "owner_recovery" if (source, target) == ("reserved", "pending") else "runner"
    assert transition_allowed(source, target, actor=actor) is allowed


def test_runner_cannot_make_a_reserved_row_retryable() -> None:
    assert transition_allowed("reserved", "pending", actor="runner") is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("unknown_contract_key", "forbidden"),
        ("ordered_call_cap", 47),
        ("per_call_maximum_usd", "0.02"),
        ("aggregate_maximum_usd", "0.47"),
        ("automatic_restart", True),
    ],
)
def test_v3_policy_rejects_unknown_or_non_exact_controls(
    field: str, value: object
) -> None:
    payload = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    mutated = deepcopy(payload)
    mutated[field] = value

    with pytest.raises(ValidationError):
        GateBBenchmarkPolicyV3.model_validate(mutated)


def test_v3_policy_file_parses_as_the_closed_contract() -> None:
    policy = load_gate_b_benchmark_policy_v3()

    assert policy.ordered_call_cap == 48
    assert str(policy.per_call_maximum_usd) == "0.01"
    assert str(policy.aggregate_maximum_usd) == "0.48"
    assert policy.automatic_restart is False


def test_v3_package_manifest_carries_every_input_and_authority_hash() -> None:
    ordered_input_sha256s = tuple(f"{index:064x}" for index in range(48))
    authority_sha256s = ("a" * 64, "b" * 64)
    manifest = GateBPackageManifestV3(
        schema_version="3.0.0",
        package_id="gate-b-v3",
        created_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
        ordered_input_sha256s=ordered_input_sha256s,
        authority_sha256s=authority_sha256s,
    )

    assert manifest.ordered_input_sha256s == ordered_input_sha256s
    assert manifest.authority_sha256s == authority_sha256s
    assert len(manifest.ordered_input_sha256s) == 48
    assert len(manifest.canonical_sha256) == 64


def test_v3_manifest_and_launch_identity_require_utc_timestamps() -> None:
    non_utc = datetime(2026, 8, 20, 18, 0, tzinfo=timezone(timedelta(hours=6)))

    with pytest.raises(ValidationError, match="UTC"):
        GateBPackageManifestV3(
            schema_version="3.0.0",
            package_id="gate-b-v3",
            created_at=non_utc,
            ordered_input_sha256s=tuple(f"{index:064x}" for index in range(48)),
            authority_sha256s=("a" * 64,),
        )
    with pytest.raises(ValidationError, match="UTC"):
        GateBLaunchIdentityV3(
            schema_version="3.0.0",
            run_id="gate-b-v3",
            issued_at=non_utc,
            package_manifest_sha256="a" * 64,
        )
