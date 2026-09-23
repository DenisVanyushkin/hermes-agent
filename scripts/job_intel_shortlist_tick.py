"""No-agent periodic tick for weekly shortlist reply import and discrepancy report."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from job_intel_shortlist_empty import publish_empty_release
from job_intel_shortlist_poll import load_operator_uid, poll_release
from job_intel_shortlist_publish import ATTEMPT_ID, RELEASE_ID, load_receipt
from job_intel_shortlist_report import DEFAULT_DB, publish_report


DEFAULT_SOURCE_ROOT = Path.home() / ".hermes" / "job_intel" / "shortlist"
DEFAULT_RELEASE_ROOT = Path.home() / ".hermes" / "job_intel" / "shortlist-releases"
DEFAULT_LABELS = Path.home() / ".hermes" / "job_intel" / "manual-shortlist" / "labels" / "owner-labels-2026-09.json"


def tick_pending(source_root: Path, release_root: Path, labels_path: Path,
                 operator_uid: str, client: Any, *, db_path: Path = DEFAULT_DB,
                 now: datetime | None = None) -> list[dict[str, str]]:
    """Advance each current-format release; one failure does not hide later work."""
    if not operator_uid or not release_root.is_dir():
        raise ValueError("operator UID or release root missing")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("tick time must be timezone aware")
    outcomes: list[dict[str, str]] = []
    for release_dir in sorted(release_root.iterdir()):
        release_id = release_dir.name
        if not release_dir.is_dir() or not RELEASE_ID.fullmatch(release_id):
            continue
        receipt_path = release_dir / ATTEMPT_ID / "receipt.json"
        if not (release_dir / ATTEMPT_ID).exists():
            continue
        try:
            receipt = load_receipt(receipt_path.parent)
            if receipt.get("release_id") != release_id or receipt.get("attempt_id") != ATTEMPT_ID:
                raise ValueError("release receipt path identity mismatch")
            state = receipt.get("state")
            if state in {"reported", "expired"}:
                continue
            source_dir = source_root / release_id
            if state == "prepared" and receipt.get("empty") is True:
                receipt = publish_empty_release(source_dir, release_root, receipt["channel"],
                                                client, now=now)
                state = receipt["state"]
            elif state in {"prepared", "anchored", "delivered"} and not receipt.get("empty"):
                receipt = poll_release(source_dir, release_root, release_id, labels_path,
                                       operator_uid, client, now=now)
                state = receipt["state"]
            if state == "imported":
                receipt = publish_report(source_dir, release_root, release_id, client,
                                         now=now, db_path=db_path)
                state = receipt["state"]
            if state == "void":
                outcomes.append({"release_id": release_id, "state": "void",
                                 "error": "operator resolution required"})
            elif state in {"prepared", "anchored", "delivered", "reported", "expired"}:
                outcomes.append({"release_id": release_id, "state": str(state)})
            else:
                raise ValueError(f"unknown release state: {state}")
        except Exception as error:
            outcomes.append({"release_id": release_id, "error": str(error)})
    return outcomes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--release-root", type=Path, default=DEFAULT_RELEASE_ROOT)
    parser.add_argument("--labels-path", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--operator-uid")
    args = parser.parse_args()
    from slack_sdk import WebClient
    from job_intel_shortlist_send import load_token

    result = tick_pending(args.source_root, args.release_root, args.labels_path,
                          args.operator_uid or load_operator_uid(), WebClient(token=load_token()),
                          db_path=args.db)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if any("error" in item for item in result) else 0


if __name__ == "__main__":
    raise SystemExit(main())
