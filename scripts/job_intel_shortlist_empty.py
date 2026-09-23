"""Publish and reconcile an empty weekly census without a review workbook."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any

from job_intel_shortlist_publish import (
    ATTEMPT_ID, PublishError, RELEASE_ID, _artifact_sha, _atomic_json, _bot_uid,
    _read_json, _release_lock, _scan_anchors, _void, anchor_marker, load_receipt,
)
from job_intel_shortlist_workbook import projection_sha256


def _source_empty(source_dir: Path) -> tuple[dict[str, Any], str]:
    artifact = _read_json(source_dir / "shortlist.json")
    release_id = artifact.get("release_id")
    if (artifact.get("artifact") != "job_intel_weekly_shortlist"
            or not isinstance(release_id, str) or not RELEASE_ID.fullmatch(release_id)
            or artifact.get("items") != [] or artifact.get("rejected_sample") != []):
        raise PublishError("not an empty weekly artifact")
    source_sha = _artifact_sha(artifact)
    try:
        declared = (source_dir / "SHA256SUMS").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise PublishError("source artifact SHA256SUMS missing") from error
    if declared != f"{source_sha}  canonical":
        raise PublishError("source artifact digest mismatch")
    return artifact, source_sha


def publish_empty_release(source_dir: Path, release_root: Path, channel: str, client: Any,
                          *, now: datetime | None = None) -> dict[str, Any]:
    """Report zero reviewable rows once; retry adopts an already posted anchor."""
    artifact, source_sha = _source_empty(source_dir)
    now = now or datetime.now(timezone.utc)
    release_dir = release_root / artifact["release_id"]
    with _release_lock(release_dir):
        attempt_dir = release_dir / ATTEMPT_ID
        if any(path.is_dir() and path.name != ATTEMPT_ID and not path.name.startswith(".")
               for path in release_dir.iterdir()):
            raise PublishError("release has another attempt; operator resolution required")
        for staging in release_dir.glob(f".{ATTEMPT_ID}.*.preparing"):
            if not staging.is_dir() or staging.is_symlink():
                raise PublishError("unsafe empty-release staging directory")
            shutil.rmtree(staging)
        if attempt_dir.exists():
            receipt = load_receipt(attempt_dir)
            manifest_bytes = (attempt_dir / "manifest.json").read_bytes()
            if (receipt.get("empty") is not True or receipt.get("channel") != channel
                    or receipt.get("source_artifact_sha256") != source_sha
                    or receipt.get("manifest_sha256") != hashlib.sha256(manifest_bytes).hexdigest()
                    or receipt.get("release_id") != artifact["release_id"]
                    or receipt.get("attempt_id") != ATTEMPT_ID):
                raise PublishError("frozen empty release changed")
            manifest = json.loads(manifest_bytes)
            if (manifest.get("source_artifact_sha256") != source_sha
                    or manifest.get("projection_sha256") != receipt.get("projection_sha256")
                    or manifest.get("items") != [] or manifest.get("rejected_sample") != []):
                raise PublishError("frozen empty manifest changed")
        else:
            staging = release_dir / f".{ATTEMPT_ID}.{os.getpid()}.preparing"
            staging.mkdir()
            projection = projection_sha256(artifact)
            manifest = {
                "release_id": artifact["release_id"], "attempt_id": ATTEMPT_ID,
                "source_artifact_sha256": source_sha, "projection_sha256": projection,
                "items": [], "rejected_sample": [],
                "census_count": artifact.get("census_count"),
                "census_sha256": artifact.get("census_sha256"),
                "run_ids": artifact.get("run_ids"),
                "suppressed_counts": artifact.get("suppressed_counts"),
                "excluded_counts": artifact.get("excluded_counts"),
            }
            _atomic_json(staging / "manifest.json", manifest)
            receipt = {
                "release_id": artifact["release_id"], "attempt_id": ATTEMPT_ID,
                "state": "prepared", "empty": True, "channel": channel,
                "prepared_at": now.astimezone(timezone.utc).isoformat(),
                "source_artifact_sha256": source_sha,
                "manifest_sha256": hashlib.sha256((staging / "manifest.json").read_bytes()).hexdigest(),
                "projection_sha256": projection, "rows": 0, "rejected_sample_rows": 0,
            }
            _atomic_json(staging / "receipt.json", receipt)
            os.replace(staging, attempt_dir)
            directory_fd = os.open(release_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        if receipt["state"] == "reported":
            return receipt
        if receipt["state"] != "prepared":
            raise PublishError(f"empty release state requires operator resolution: {receipt['state']}")
        try:
            bot_uid = _bot_uid(client)
            anchors = _scan_anchors(client, receipt, bot_uid)
        except Exception as error:
            _void(attempt_dir, receipt, f"Slack empty-release scan failed: {error}")
        if len(anchors) > 1:
            _void(attempt_dir, receipt, "ambiguous empty-release anchors")
        if anchors:
            thread_ts = anchors[0]
        else:
            text = (f"*Новых ролей нет* — {artifact['release_id']}\n"
                    f"Проверено: {manifest['census_count']}; прогонов: {len(manifest['run_ids'] or [])}; "
                    f"подавлено: {manifest['suppressed_counts']}; исключено: {manifest['excluded_counts']}.\n"
                    + anchor_marker(receipt))
            response = client.chat_postMessage(channel=channel, text=text)
            thread_ts = response.get("ts")
            if not thread_ts:
                raise PublishError("Slack empty-release post lacks ts; retry must scan")
        reported = {**receipt, "state": "reported", "thread_ts": str(thread_ts),
                    "report_ts": str(thread_ts), "reported_at": now.astimezone(timezone.utc).isoformat()}
        _atomic_json(attempt_dir / "receipt.json", reported)
        return reported


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--channel", required=True)
    args = parser.parse_args()
    from slack_sdk import WebClient
    from job_intel_shortlist_send import load_token

    receipt = publish_empty_release(args.artifact_dir, args.release_root, args.channel,
                                    WebClient(token=load_token()))
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
