from __future__ import annotations

import json

import pytest

from trading_autopilot.manifest import (
    DEFAULT_MANIFEST,
    MANIFEST_SCHEMA_VERSION,
    ModuleContract,
    RuntimeManifest,
    RuntimeManifestError,
    StateDomain,
)
from trading_autopilot.runtime import boot_runtime, format_boot_report


def test_default_manifest_has_unique_state_ownership() -> None:
    manifest = DEFAULT_MANIFEST

    manifest.validate()

    ownership_map = manifest.ownership_map()
    assert len(ownership_map) == len({domain.value for domain in StateDomain})
    assert ownership_map[StateDomain.RAW_MARKET_EVENTS.value] == "market_ingest"
    assert ownership_map[StateDomain.IMMUTABLE_JOURNAL.value] == "journal_store"
    assert ownership_map[StateDomain.CONTROL_COMMANDS.value] == "control_plane"


def test_default_manifest_round_trips_through_json() -> None:
    manifest = DEFAULT_MANIFEST

    payload = json.loads(json.dumps(manifest.to_dict()))
    restored = RuntimeManifest.from_dict(payload)

    assert restored == manifest
    assert restored.schema_version == MANIFEST_SCHEMA_VERSION


def test_boot_report_lists_modules_and_versions() -> None:
    report = boot_runtime()
    lines = format_boot_report(report)

    assert report.validated is True
    assert lines[0] == "runtime=trading-autopilot"
    assert any("market_ingest [v1] (async)" in line for line in lines)
    assert any("risk_engine [v1] (sync)" in line for line in lines)
    assert any("journal_store [v1] (async)" in line for line in lines)


def test_duplicate_state_ownership_is_rejected() -> None:
    with pytest.raises(RuntimeManifestError, match="owned by both"):
        RuntimeManifest(
            schema_version="1.0.0",
            modules=(
                ModuleContract(
                    name="module_a",
                    contract_version="v1",
                    owns_state=(StateDomain.RAW_MARKET_EVENTS,),
                ),
                ModuleContract(
                    name="module_b",
                    contract_version="v1",
                    owns_state=(StateDomain.RAW_MARKET_EVENTS,),
                ),
            ),
        ).validate()


def test_module_cannot_read_state_it_owns() -> None:
    with pytest.raises(RuntimeManifestError, match="reads state it owns"):
        RuntimeManifest(
            schema_version="1.0.0",
            modules=(
                ModuleContract(
                    name="module_a",
                    contract_version="v1",
                    owns_state=(StateDomain.RAW_MARKET_EVENTS,),
                    reads_state=(StateDomain.RAW_MARKET_EVENTS,),
                ),
            ),
        ).validate()
