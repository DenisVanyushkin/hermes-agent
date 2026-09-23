"""Crash-recoverable publisher for one frozen weekly shortlist workbook.

All external writes follow a complete Slack scan for the typed release identity.
An ambiguous scan makes the attempt void; it never triggers a blind retry.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Iterator

from job_intel_shortlist_workbook import read_owner_workbook, projection_sha256, write_workbook


class PublishError(ValueError):
    """The release is ambiguous or cannot safely proceed."""


RELEASE_ID = re.compile(r"shortlist-\d{4}-W\d{2}\Z")
ATTEMPT_ID = "attempt-001"
ANCHOR_TAG = "JI_SHORTLIST_ANCHOR_V1"
FILE_TAG = "JI_SHORTLIST_FILE_V1"
FINAL_STATES = frozenset({"delivered", "imported", "reported", "expired"})


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _artifact_sha(artifact: dict[str, Any]) -> str:
    hashable = {key: value for key, value in artifact.items() if key != "built_at"}
    return _sha(json.dumps(hashable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    payload = _json_bytes(value)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PublishError(f"cannot read {path}") from error
    if not isinstance(value, dict):
        raise PublishError(f"not a JSON object: {path}")
    return value


def load_receipt(attempt_dir: Path) -> dict[str, Any]:
    return _read_json(attempt_dir / "receipt.json")


@contextmanager
def _release_lock(release_dir: Path) -> Iterator[None]:
    release_dir.mkdir(parents=True, exist_ok=True)
    lock_path = release_dir / ".release.lock"
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _source(source_dir: Path) -> tuple[dict[str, Any], str]:
    artifact = _read_json(source_dir / "shortlist.json")
    release_id = artifact.get("release_id")
    if artifact.get("artifact") != "job_intel_weekly_shortlist" or not isinstance(release_id, str) or not RELEASE_ID.fullmatch(release_id):
        raise PublishError("invalid weekly artifact identity")
    source_sha = _artifact_sha(artifact)
    try:
        declared = (source_dir / "SHA256SUMS").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise PublishError("source artifact SHA256SUMS missing") from error
    if declared != f"{source_sha}  canonical":
        raise PublishError("source artifact digest mismatch")
    if not isinstance(artifact.get("items"), list) or not artifact["items"]:
        raise PublishError("empty weekly release needs the separate zero-manifest report path")
    return artifact, source_sha


def _verify_frozen(attempt_dir: Path, receipt: dict[str, Any], *, source_sha: str, channel: str) -> dict[str, Any]:
    manifest_path = attempt_dir / "manifest.json"
    workbook_path = attempt_dir / "review.xlsx"
    try:
        manifest_bytes = manifest_path.read_bytes()
        workbook_bytes = workbook_path.read_bytes()
    except OSError as error:
        raise PublishError("prepared release lacks frozen files") from error
    if _sha(manifest_bytes) != receipt.get("manifest_sha256") or _sha(workbook_bytes) != receipt.get("workbook_sha256"):
        raise PublishError("prepared release file SHA mismatch")
    manifest = _read_json(manifest_path)
    for field, expected in (
        ("release_id", receipt.get("release_id")),
        ("attempt_id", receipt.get("attempt_id")),
        ("projection_sha256", receipt.get("projection_sha256")),
        ("source_artifact_sha256", source_sha),
        ("workbook_sha256", receipt.get("workbook_sha256")),
        ("channel", channel),
    ):
        if manifest.get(field) != expected:
            raise PublishError(f"manifest {field} mismatch")
    if receipt.get("channel") != channel or receipt.get("source_artifact_sha256") != source_sha:
        raise PublishError("existing attempt source or channel changed")
    if receipt.get("release_id") != attempt_dir.parent.name or receipt.get("attempt_id") != attempt_dir.name:
        raise PublishError("receipt path identity mismatch")
    if not isinstance(manifest.get("items"), list) or receipt.get("rows") != len(manifest["items"]):
        raise PublishError("manifest shortlist row count mismatch")
    return manifest


def _prepare_unlocked(artifact: dict[str, Any], source_sha: str, release_dir: Path,
                      channel: str, now: datetime) -> dict[str, Any]:
    # A staging directory has no Slack side effect: no receipt/anchor can exist
    # until the complete directory is renamed to attempt-001. The release lock
    # makes cleanup of a prior crashed writer safe.
    for staging in release_dir.glob(f".{ATTEMPT_ID}.*.preparing"):
        if not staging.is_dir() or staging.is_symlink():
            raise PublishError(f"unsafe preparation path: {staging}")
        shutil.rmtree(staging)
    attempt_dir = release_dir / ATTEMPT_ID
    other_attempts = [path for path in release_dir.iterdir() if path.is_dir() and path.name != ATTEMPT_ID]
    if other_attempts:
        raise PublishError("release has another attempt; operator resolution required")
    if attempt_dir.exists():
        if (attempt_dir / "receipt.json").exists():
            receipt = load_receipt(attempt_dir)
        else:
            manifest_path = attempt_dir / "manifest.json"
            workbook_path = attempt_dir / "review.xlsx"
            manifest = _read_json(manifest_path)
            try:
                workbook_sha = _sha(workbook_path.read_bytes())
                parsed = read_owner_workbook(workbook_path, artifact, ATTEMPT_ID)
            except (OSError, ValueError) as error:
                raise PublishError("cannot recover frozen prepared workbook") from error
            expected_items = [
                {"vacancy_key": item["vacancy_key"], "role_fit_verdict": item["role_fit_verdict"]}
                for item in artifact["items"]
            ]
            expected_sample = [
                {"vacancy_key": item["vacancy_key"], "role_fit_verdict": item["role_fit_verdict"]}
                for item in artifact["rejected_sample"]
            ]
            if (manifest.get("release_id") != artifact["release_id"]
                    or manifest.get("attempt_id") != ATTEMPT_ID
                    or manifest.get("source_artifact_sha256") != source_sha
                    or manifest.get("projection_sha256") != projection_sha256(artifact)
                    or manifest.get("workbook_sha256") != workbook_sha
                    or manifest.get("channel") != channel
                    or manifest.get("items") != expected_items
                    or manifest.get("rejected_sample") != expected_sample
                    or parsed["decisions"]):
                raise PublishError("cannot recover changed prepared release")
            receipt = {
                "release_id": artifact["release_id"], "attempt_id": ATTEMPT_ID,
                "state": "prepared", "channel": channel,
                "prepared_at": manifest["prepared_at"],
                "source_artifact_sha256": source_sha,
                "manifest_sha256": _sha(manifest_path.read_bytes()),
                "workbook_sha256": workbook_sha,
                "projection_sha256": manifest["projection_sha256"],
                "rows": len(expected_items), "rejected_sample_rows": len(expected_sample),
            }
            _atomic_json(attempt_dir / "receipt.json", receipt)
        _verify_frozen(attempt_dir, receipt, source_sha=source_sha, channel=channel)
        return receipt

    staging_dir = release_dir / f".{ATTEMPT_ID}.{os.getpid()}.preparing"
    staging_dir.mkdir()
    workbook_path = staging_dir / "review.xlsx"
    projection = write_workbook(workbook_path, artifact, ATTEMPT_ID)
    workbook_sha = _sha(workbook_path.read_bytes())
    manifest = {
        "release_id": artifact["release_id"], "attempt_id": ATTEMPT_ID,
        "channel": channel,
        "source_artifact_sha256": source_sha,
        "projection_sha256": projection, "workbook_sha256": workbook_sha,
        "prepared_at": now.astimezone(timezone.utc).isoformat(),
        "items": [{"vacancy_key": item["vacancy_key"], "role_fit_verdict": item["role_fit_verdict"]}
                  for item in artifact["items"]],
        "rejected_sample": [{"vacancy_key": item["vacancy_key"], "role_fit_verdict": item["role_fit_verdict"]}
                            for item in artifact["rejected_sample"]],
        "census_sha256": artifact.get("census_sha256"),
        "run_ids": artifact.get("run_ids"), "ruleset_versions": artifact.get("ruleset_versions"),
        "commit": artifact.get("commit"),
    }
    _atomic_json(staging_dir / "manifest.json", manifest)
    receipt = {
        "release_id": artifact["release_id"], "attempt_id": ATTEMPT_ID,
        "state": "prepared", "channel": channel,
        "prepared_at": manifest["prepared_at"],
        "source_artifact_sha256": source_sha,
        "manifest_sha256": _sha((staging_dir / "manifest.json").read_bytes()),
        "workbook_sha256": workbook_sha, "projection_sha256": projection,
        "rows": len(manifest["items"]), "rejected_sample_rows": len(manifest["rejected_sample"]),
    }
    _atomic_json(staging_dir / "receipt.json", receipt)
    os.replace(staging_dir, attempt_dir)
    fd = os.open(release_dir, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return receipt


def prepare_release(source_dir: Path, release_root: Path, channel: str,
                    *, now: datetime | None = None) -> dict[str, Any]:
    artifact, source_sha = _source(source_dir)
    release_dir = release_root / artifact["release_id"]
    now = now or datetime.now(timezone.utc)
    with _release_lock(release_dir):
        return _prepare_unlocked(artifact, source_sha, release_dir, channel, now)


def anchor_marker(receipt: dict[str, Any]) -> str:
    return (f"{ANCHOR_TAG} release_id={receipt['release_id']} "
            f"attempt_id={receipt['attempt_id']} projection_sha256={receipt['projection_sha256']}")


def file_marker(receipt: dict[str, Any]) -> str:
    return (f"{FILE_TAG} release_id={receipt['release_id']} "
            f"attempt_id={receipt['attempt_id']} projection_sha256={receipt['projection_sha256']} "
            f"workbook_sha256={receipt['workbook_sha256']}")


def _pages(method: Any, *, collection: str, **kwargs: Any) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    cursor: str | None = None
    seen: set[str] = set()
    while True:
        arguments = {**kwargs, "limit": 100}
        if cursor:
            arguments["cursor"] = cursor
        result = method(**arguments)
        if not result.get("ok") or not isinstance(result.get(collection), list):
            raise PublishError("Slack scan returned invalid response")
        messages.extend(result[collection])
        metadata = result.get("response_metadata") or {}
        next_cursor = metadata.get("next_cursor") or ""
        if next_cursor and (not isinstance(next_cursor, str) or next_cursor in seen):
            raise PublishError("Slack scan cursor did not advance")
        if not next_cursor:
            if result.get("has_more"):
                raise PublishError("Slack scan has_more without cursor")
            return messages
        seen.add(next_cursor)
        cursor = next_cursor


def _void(attempt_dir: Path, receipt: dict[str, Any], reason: str) -> None:
    _atomic_json(attempt_dir / "receipt.json", {**receipt, "state": "void", "void_reason": reason})
    raise PublishError(reason)


def _bot_uid(client: Any) -> str:
    result = client.auth_test()
    if not result.get("ok") or not result.get("user_id"):
        raise PublishError("Slack auth_test failed")
    return str(result["user_id"])


def _scan_anchors(client: Any, receipt: dict[str, Any], bot_uid: str) -> list[str]:
    oldest = datetime.fromisoformat(receipt["prepared_at"]).timestamp()
    messages = _pages(
        client.conversations_history, collection="messages", channel=receipt["channel"],
        oldest=str(oldest), inclusive=True,
    )
    marker = anchor_marker(receipt)
    return [str(message["ts"]) for message in messages
            if message.get("user") == bot_uid and marker in str(message.get("text") or "").splitlines()
            and message.get("ts")]


def _scan_files(client: Any, receipt: dict[str, Any], bot_uid: str) -> list[str]:
    messages = _pages(
        client.conversations_replies, collection="messages", channel=receipt["channel"],
        ts=receipt["thread_ts"], inclusive=True,
    )
    marker = file_marker(receipt)
    filename = f"{receipt['release_id']}-{receipt['attempt_id']}.xlsx"
    files: list[str] = []
    for message in messages:
        if message.get("user") != bot_uid:
            continue
        for file in message.get("files") or []:
            if (isinstance(file, dict) and file.get("id") and file.get("name") == filename
                    and (marker in str(message.get("text") or "").splitlines() or file.get("title") == marker)):
                files.append(str(file["id"]))
    return files


def publish_release(source_dir: Path, release_root: Path, channel: str, client: Any,
                    *, now: datetime | None = None) -> dict[str, Any]:
    """Deliver once or adopt prior Slack writes after a crash.

    A transport exception after a write leaves the stage prepared/anchored for
    the next invocation to scan. Scan failures and ambiguity make it void.
    """
    artifact, source_sha = _source(source_dir)
    release_dir = release_root / artifact["release_id"]
    now = now or datetime.now(timezone.utc)
    with _release_lock(release_dir):
        receipt = _prepare_unlocked(artifact, source_sha, release_dir, channel, now)
        attempt_dir = release_dir / ATTEMPT_ID
        if receipt["state"] in FINAL_STATES:
            return receipt
        if receipt["state"] == "void":
            raise PublishError("void release requires operator resolution")
        if receipt["state"] not in {"prepared", "anchored"}:
            raise PublishError(f"unexpected release state: {receipt['state']}")
        try:
            bot_uid = _bot_uid(client)
        except Exception as error:
            _void(attempt_dir, receipt, f"Slack auth scan failed: {error}")

        if receipt["state"] == "prepared":
            try:
                anchors = _scan_anchors(client, receipt, bot_uid)
            except Exception as error:
                _void(attempt_dir, receipt, f"Slack anchor scan failed: {error}")
            if len(anchors) > 1:
                _void(attempt_dir, receipt, "ambiguous Slack anchors")
            if anchors:
                thread_ts = anchors[0]
            else:
                response = client.chat_postMessage(
                    channel=channel,
                    text=(f"*Еженедельный обзор вакансий* {receipt['release_id']}\n"
                          f"{receipt['rows']} в shortlist, {receipt['rejected_sample_rows']} в rejected_sample. "
                          "Заполни owner_decision и owner_note, затем ответь в этот тред тем же XLSX.\n"
                          + anchor_marker(receipt)),
                )
                thread_ts = response.get("ts")
                if not thread_ts:
                    raise PublishError("Slack anchor response lacks ts; retry must scan")
            receipt = {**receipt, "state": "anchored", "thread_ts": str(thread_ts),
                       "anchored_at": now.astimezone(timezone.utc).isoformat()}
            _atomic_json(attempt_dir / "receipt.json", receipt)

        try:
            files = _scan_files(client, receipt, bot_uid)
        except Exception as error:
            _void(attempt_dir, receipt, f"Slack file scan failed: {error}")
        if len(files) > 1:
            _void(attempt_dir, receipt, "ambiguous Slack bot files")
        if files:
            file_id = files[0]
        else:
            response = client.files_upload_v2(
                channel=channel, thread_ts=receipt["thread_ts"],
                file=str(attempt_dir / "review.xlsx"),
                filename=f"{receipt['release_id']}-{receipt['attempt_id']}.xlsx",
                title=file_marker(receipt), initial_comment=file_marker(receipt),
            )
            uploaded = response.get("file") or (response.get("files") or [{}])[0]
            file_id = uploaded.get("id")
            if not file_id:
                raise PublishError("Slack upload response lacks file_id; retry must scan")
        receipt = {**receipt, "state": "delivered", "bot_file_id": str(file_id),
                   "delivered_at": now.astimezone(timezone.utc).isoformat()}
        _atomic_json(attempt_dir / "receipt.json", receipt)
        return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--channel", required=True)
    args = parser.parse_args()
    from slack_sdk import WebClient
    from job_intel_shortlist_send import load_token

    receipt = publish_release(args.artifact_dir, args.release_root, args.channel,
                              WebClient(token=load_token()))
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
