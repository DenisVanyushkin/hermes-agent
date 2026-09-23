import hashlib
import json
from datetime import date
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from job_intel_shortlist_cooldown import CooldownError, sample_cooldown_keys


def _release(root: Path, week: str, sample: list[str], *, state: str = "delivered") -> Path:
    attempt = root / f"shortlist-{week}" / "attempt-001"
    attempt.mkdir(parents=True)
    manifest = {
        "release_id": f"shortlist-{week}", "attempt_id": "attempt-001",
        "projection_sha256": "projection", "items": [],
        "rejected_sample": [{"vacancy_key": key, "role_fit_verdict": "reject"} for key in sample],
    }
    payload = json.dumps(manifest, sort_keys=True).encode()
    (attempt / "manifest.json").write_bytes(payload)
    (attempt / "receipt.json").write_text(json.dumps({
        "release_id": manifest["release_id"], "attempt_id": "attempt-001", "state": state,
        "manifest_sha256": hashlib.sha256(payload).hexdigest(),
        "projection_sha256": "projection", "bot_file_id": "F1",
    }))
    return attempt


def test_previous_eight_weeks_only(tmp_path: Path) -> None:
    root = tmp_path / "releases"
    _release(root, "2026-W30", ["too-old"])
    _release(root, "2026-W31", ["boundary"])
    _release(root, "2026-W38", ["recent"])
    _release(root, "2026-W39", ["current"])
    assert sample_cooldown_keys(root, date(2026, 9, 21)) == {"boundary", "recent"}


def test_prepared_sample_does_not_count(tmp_path: Path) -> None:
    root = tmp_path / "releases"
    _release(root, "2026-W38", ["pending"], state="prepared")
    assert sample_cooldown_keys(root, date(2026, 9, 21)) == set()


def test_changed_delivered_manifest_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "releases"
    attempt = _release(root, "2026-W38", ["recent"])
    manifest = json.loads((attempt / "manifest.json").read_text())
    manifest["rejected_sample"].append({"vacancy_key": "tampered", "role_fit_verdict": "reject"})
    (attempt / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(CooldownError, match="SHA"):
        sample_cooldown_keys(root, date(2026, 9, 21))
