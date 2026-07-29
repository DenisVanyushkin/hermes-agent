"""The `audience` field marks who reads a cron job's delivery. `end_user`
means a non-technical recipient (e.g. Amina's WhatsApp): technical failures
must never be delivered there. The key is persisted only when explicitly
set, so existing jobs.json records round-trip byte-identically."""

from __future__ import annotations

import pytest

from cron.jobs import normalize_audience


def test_operator_and_end_user_are_accepted() -> None:
    assert normalize_audience("operator") == "operator"
    assert normalize_audience("end_user") == "end_user"


def test_case_and_whitespace_are_normalized() -> None:
    assert normalize_audience("  End_User  ") == "end_user"


def test_unset_stays_unset() -> None:
    assert normalize_audience(None) is None
    assert normalize_audience("") is None


def test_unknown_value_is_rejected() -> None:
    with pytest.raises(ValueError, match="audience"):
        normalize_audience("amina")
