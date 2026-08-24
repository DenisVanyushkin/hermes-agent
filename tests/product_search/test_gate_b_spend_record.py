from __future__ import annotations

from pathlib import Path

import pytest

from job_intel.product_search.gate_b_spend_record_v1 import (
    SpendRecordError,
    SpendRecordStore,
)


def test_provision_is_create_once_and_open_does_not_create(tmp_path: Path) -> None:
    root = tmp_path / "spend"
    manifest_sha256 = "a" * 64

    record = SpendRecordStore.provision(
        root=root,
        manifest_sha256=manifest_sha256,
        aggregate_maximum_cents=48,
    )

    assert record.committed_budget_cents == 0
    assert SpendRecordStore.open(root=root, manifest_sha256=manifest_sha256).remaining_cents == 48
    with pytest.raises(SpendRecordError, match="spend_record_exists"):
        SpendRecordStore.provision(
            root=root,
            manifest_sha256=manifest_sha256,
            aggregate_maximum_cents=48,
        )
    with pytest.raises(SpendRecordError, match="spend_record_missing"):
        SpendRecordStore.open(root=root, manifest_sha256="b" * 64)


def test_reserve_is_monotonic_and_refuses_over_budget(tmp_path: Path) -> None:
    root = tmp_path / "spend"
    manifest_sha256 = "a" * 64
    SpendRecordStore.provision(
        root=root,
        manifest_sha256=manifest_sha256,
        aggregate_maximum_cents=3,
    )
    store = SpendRecordStore.open(root=root, manifest_sha256=manifest_sha256)

    assert store.reserve(2) == 2
    assert store.remaining_cents == 1
    with pytest.raises(SpendRecordError, match="committed_budget_exhausted"):
        store.reserve(2)
