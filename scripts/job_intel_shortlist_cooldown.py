"""Derive the rejected-sample cooldown from verified delivered manifests."""

from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import re


class CooldownError(ValueError):
    """A prior sample cannot be identified safely."""


_RELEASE = re.compile(r"shortlist-(\d{4})-W(\d{2})\Z")
_DELIVERED = frozenset({"delivered", "imported", "reported", "expired"})


def sample_cooldown_keys(release_root: Path, current_week_start: date,
                         *, weeks: int = 8) -> frozenset[str]:
    """Keys sampled in the preceding `weeks` ISO weeks, excluding the current one."""
    if current_week_start.weekday() != 0 or weeks < 1 or not release_root.is_dir():
        raise CooldownError("invalid cooldown window or release root")
    keys: set[str] = set()
    for receipt_path in sorted(release_root.rglob("receipt.json")):
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CooldownError(f"cannot read release receipt: {receipt_path}") from error
        if not isinstance(receipt, dict):
            raise CooldownError(f"invalid release receipt: {receipt_path}")
        release_id = receipt.get("release_id")
        match = _RELEASE.fullmatch(release_id) if isinstance(release_id, str) else None
        if match is None:
            # Older CSV and canary releases have no rejected_sample sheet.
            if isinstance(release_id, str) and (release_id.startswith("canary-") or "attempt_id" not in receipt):
                continue
            raise CooldownError(f"invalid release id: {release_id}")
        try:
            prior_week = date.fromisocalendar(int(match[1]), int(match[2]), 1)
        except ValueError as error:
            raise CooldownError(f"invalid ISO release week: {release_id}") from error
        difference = (current_week_start - prior_week).days
        if not 0 < difference <= 7 * weeks:
            continue
        attempt_id = receipt.get("attempt_id")
        if (not isinstance(attempt_id, str) or receipt_path.parent.name != attempt_id
                or receipt_path.parent.parent.name != release_id):
            raise CooldownError(f"invalid release attempt path: {receipt_path}")
        state = receipt.get("state")
        if state == "prepared":
            continue
        if state not in _DELIVERED:
            raise CooldownError(f"unresolved prior sample release: {release_id}")
        expected_sha = receipt.get("manifest_sha256")
        if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
            raise CooldownError(f"manifest SHA missing: {release_id}")
        try:
            payload = (receipt_path.parent / "manifest.json").read_bytes()
            manifest = json.loads(payload)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CooldownError(f"cannot read prior sample manifest: {release_id}") from error
        if hashlib.sha256(payload).hexdigest() != expected_sha:
            raise CooldownError(f"manifest SHA mismatch: {release_id}")
        if (not isinstance(manifest, dict) or manifest.get("release_id") != release_id
                or manifest.get("attempt_id") != attempt_id
                or manifest.get("projection_sha256") != receipt.get("projection_sha256")
                or not isinstance(manifest.get("rejected_sample"), list)):
            raise CooldownError(f"invalid prior sample manifest: {release_id}")
        seen: set[str] = set()
        for row in manifest["rejected_sample"]:
            if (not isinstance(row, dict) or not isinstance(row.get("vacancy_key"), str)
                    or not row["vacancy_key"] or row["vacancy_key"] in seen
                    or row.get("role_fit_verdict") not in {"reject", "blocked"}):
                raise CooldownError(f"invalid prior sample row: {release_id}")
            seen.add(row["vacancy_key"])
        keys.update(seen)
    return frozenset(keys)
