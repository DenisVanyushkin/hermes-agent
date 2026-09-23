"""Import the first valid owner workbook returned in a weekly Slack thread.

One release lock covers scan, intent, label import and receipt transition. The
intent is durable before the shared label write, so a crash can replay it by
operation key without selecting a different Slack reply.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable
from urllib.parse import urlsplit
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from job_intel_shortlist_labels import apply_decisions
from job_intel_shortlist_publish import (
    _atomic_json, _pages, _release_lock, _source, _verify_frozen,
    load_receipt, publish_release,
)
from job_intel_shortlist_workbook import WorkbookContractError, read_owner_workbook


class PollError(ValueError):
    """A release or Slack response cannot be imported safely."""


class FileRejected(PollError):
    """A permanent property of this attachment makes it an invalid answer."""


MAX_WORKBOOK_BYTES = 50 * 1024 * 1024
DEADLINE_DAYS = 21
_MISSING_FILE_ERRORS = frozenset({"file_not_found", "file_deleted"})


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request: Request, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


def download_slack_file(client: Any, file_id: str) -> bytes:
    """Download only a Slack-hosted file with the bot token and a hard size cap."""
    try:
        response = client.files_info(file=file_id)
    except Exception as error:
        api_response = getattr(error, "response", None)
        code = api_response.get("error") if hasattr(api_response, "get") else None
        if code in _MISSING_FILE_ERRORS:
            raise FileRejected(str(code)) from error
        raise
    if not response.get("ok") or not isinstance(response.get("file"), dict):
        if response.get("error") in _MISSING_FILE_ERRORS:
            raise FileRejected(str(response["error"]))
        raise PollError(f"files.info failed for {file_id}")
    info = response["file"]
    if info.get("id") != file_id:
        raise PollError(f"file identity mismatch: {file_id}")
    size = info.get("size")
    if not isinstance(size, int) or size < 0:
        raise PollError(f"file size unavailable: {file_id}")
    if size > MAX_WORKBOOK_BYTES:
        raise FileRejected("file_too_large")
    if info.get("is_external") or (info.get("filetype") and info["filetype"] != "xlsx"):
        raise FileRejected("not_slack_xlsx")
    url = info.get("url_private_download")
    try:
        parts = urlsplit(url) if isinstance(url, str) else None
        safe_url = (parts is not None and parts.scheme == "https" and parts.hostname == "files.slack.com"
                    and not parts.username and not parts.password and not parts.port)
    except ValueError:
        safe_url = False
    if not safe_url:
        raise FileRejected("not_slack_download_url")
    token = getattr(client, "token", None)
    if not isinstance(token, str) or not token:
        raise PollError("Slack client has no download token")
    request = Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with build_opener(_NoRedirect).open(request, timeout=30) as stream:
            data = stream.read(MAX_WORKBOOK_BYTES + 1)
    except HTTPError as error:
        if error.code in {404, 410}:
            raise FileRejected("file_unavailable") from error
        raise
    if len(data) > MAX_WORKBOOK_BYTES:
        raise FileRejected("file_too_large")
    return data


def _timestamp(value: Any) -> Decimal:
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError) as error:
        raise PollError(f"invalid Slack timestamp: {value!r}") from error
    if not result.is_finite() or result <= 0:
        raise PollError(f"invalid Slack timestamp: {value!r}")
    return result


def _datetime(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise PollError(f"invalid release timestamp: {value!r}") from error
    if parsed.tzinfo is None:
        raise PollError(f"release timestamp has no timezone: {value!r}")
    return parsed


def _reply_files(client: Any, receipt: dict[str, Any], operator_uid: str,
                 deadline: Decimal) -> tuple[list[tuple[Decimal, str, str]], list[dict[str, str]]]:
    messages = _pages(client.conversations_replies, collection="messages",
                      channel=receipt["channel"], ts=receipt["thread_ts"], inclusive=True)
    candidates: list[tuple[Decimal, str, str]] = []
    rejected: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for message in messages:
        files = message.get("files") or []
        if not isinstance(files, list):
            raise PollError("Slack reply files field is invalid")
        for attached in files:
            if not isinstance(attached, dict) or not isinstance(attached.get("id"), str):
                raise PollError("Slack reply has invalid file attachment")
            file_id = attached["id"]
            if file_id == receipt["bot_file_id"]:
                continue
            if message.get("user") != operator_uid:
                rejected.append({"file_id": file_id, "reason": "wrong_author"})
                continue
            timestamp = _timestamp(message.get("ts"))
            if timestamp > deadline:
                rejected.append({"file_id": file_id, "reason": "after_deadline"})
                continue
            identity = (str(timestamp), file_id)
            if identity not in seen:
                seen.add(identity)
                candidates.append((timestamp, file_id, str(message["ts"])))
    candidates.sort(key=lambda row: (row[0], row[1]))
    return candidates, rejected


def _parse_file(data: bytes, attempt_dir: Path, artifact: dict[str, Any],
                attempt_id: str) -> dict[str, Any]:
    if len(data) > MAX_WORKBOOK_BYTES:
        raise WorkbookContractError("owner workbook too large")
    with tempfile.NamedTemporaryFile(prefix=".owner-reply-", suffix=".xlsx", dir=attempt_dir,
                                     delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(data)
    try:
        return read_owner_workbook(temporary, artifact, attempt_id)
    finally:
        temporary.unlink(missing_ok=True)


def _apply_intent(labels_path: Path, artifact: dict[str, Any], receipt: dict[str, Any],
                  operator_uid: str, deadline: Decimal, now: datetime) -> dict[str, Any]:
    intent = receipt.get("import_intent")
    if not isinstance(intent, dict):
        raise PollError("missing import intent")
    for field, expected in (
        ("release_id", receipt["release_id"]),
        ("attempt_id", receipt["attempt_id"]),
        ("projection_sha256", receipt["projection_sha256"]),
        ("response_uid", operator_uid),
    ):
        if intent.get(field) != expected:
            raise PollError(f"import intent {field} mismatch")
    if (not isinstance(intent.get("file_id"), str) or not intent["file_id"]
            or intent["file_id"] == receipt["bot_file_id"]
            or not isinstance(intent.get("file_hash"), str)
            or len(intent["file_hash"]) != 64
            or _timestamp(intent.get("response_ts")) > deadline
            or not isinstance(intent.get("decisions"), dict)):
        raise PollError("import intent identity invalid")
    delta = apply_decisions(
        labels_path, artifact, release_id=receipt["release_id"],
        attempt_id=receipt["attempt_id"], file_hash=intent["file_hash"],
        response_ts=intent["response_ts"], decisions=intent["decisions"], now=now,
    )
    imported = {
        **receipt, "state": "imported", "owner_file_id": intent["file_id"],
        "owner_file_sha256": intent["file_hash"], "response_uid": operator_uid,
        "response_ts": intent["response_ts"], "label_delta": delta,
        "decision_count": len(intent["decisions"]),
        "imported_at": now.astimezone(timezone.utc).isoformat(),
    }
    return imported


def poll_release(source_dir: Path, release_root: Path, release_id: str, labels_path: Path,
                 operator_uid: str, client: Any, *,
                 fetch_file: Callable[[Any, str], bytes] = download_slack_file,
                 now: datetime | None = None) -> dict[str, Any]:
    """Process one existing attempt; never creates a new release on its own."""
    if not operator_uid:
        raise PollError("operator UID is required")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise PollError("now must be timezone-aware")
    attempt_dir = release_root / release_id / "attempt-001"
    if not attempt_dir.is_dir():
        raise PollError("release attempt does not exist")
    initial = load_receipt(attempt_dir)
    if initial["state"] in {"prepared", "anchored"}:
        publish_release(source_dir, release_root, initial["channel"], client, now=now)
    artifact, source_sha = _source(source_dir)
    if artifact["release_id"] != release_id:
        raise PollError("source release_id mismatch")
    with _release_lock(attempt_dir.parent):
        receipt = load_receipt(attempt_dir)
        _verify_frozen(attempt_dir, receipt, source_sha=source_sha, channel=receipt["channel"])
        if receipt["state"] in {"imported", "reported", "expired"}:
            return receipt
        if receipt["state"] != "delivered":
            raise PollError(f"release not delivered: {receipt['state']}")
        deadline = Decimal(str((_datetime(receipt["delivered_at"]) + timedelta(days=DEADLINE_DAYS)).timestamp()))
        if receipt.get("import_intent") is not None:
            imported = _apply_intent(labels_path, artifact, receipt, operator_uid, deadline, now)
            _atomic_json(attempt_dir / "receipt.json", imported)
            return imported

        candidates, rejected = _reply_files(client, receipt, operator_uid, deadline)
        for _reply_ts, file_id, response_ts in candidates:
            try:
                data = fetch_file(client, file_id)
            except FileRejected as error:
                rejected.append({"file_id": file_id, "reason": str(error)})
                continue
            if not isinstance(data, bytes):
                raise PollError(f"download was not bytes: {file_id}")
            try:
                parsed = _parse_file(data, attempt_dir, artifact, receipt["attempt_id"])
            except WorkbookContractError as error:
                rejected.append({"file_id": file_id, "reason": str(error)})
                continue
            if parsed["projection_sha256"] != receipt["projection_sha256"]:
                raise PollError("frozen receipt projection mismatch")
            intent = {
                "release_id": release_id, "attempt_id": receipt["attempt_id"],
                "projection_sha256": receipt["projection_sha256"],
                "file_id": file_id, "file_hash": hashlib.sha256(data).hexdigest(),
                "response_uid": operator_uid, "response_ts": response_ts,
                "decisions": parsed["decisions"],
            }
            receipt = {**receipt, "import_intent": intent, "rejected_files": rejected}
            _atomic_json(attempt_dir / "receipt.json", receipt)
            imported = _apply_intent(labels_path, artifact, receipt, operator_uid, deadline, now)
            _atomic_json(attempt_dir / "receipt.json", imported)
            return imported

        if now >= _datetime(receipt["delivered_at"]) + timedelta(days=DEADLINE_DAYS):
            expired = {**receipt, "state": "expired", "expired_at": now.astimezone(timezone.utc).isoformat(),
                       "rejected_files": rejected}
            _atomic_json(attempt_dir / "receipt.json", expired)
            return expired
        if rejected != receipt.get("rejected_files"):
            receipt = {**receipt, "rejected_files": rejected}
            _atomic_json(attempt_dir / "receipt.json", receipt)
        return receipt


def load_operator_uid() -> str:
    value = os.environ.get("HERMES_OPERATOR_SLACK_UID", "").strip()
    if value:
        return value
    env_path = Path.home() / ".hermes" / ".env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise PollError("HERMES_OPERATOR_SLACK_UID not found") from error
    for line in lines:
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        key, raw = line.split("=", 1)
        if key.strip() == "HERMES_OPERATOR_SLACK_UID":
            value = raw.strip().strip("'").strip('"')
            if value:
                return value
    raise PollError("HERMES_OPERATOR_SLACK_UID not found")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--labels-path", type=Path, required=True)
    parser.add_argument("--operator-uid")
    args = parser.parse_args()
    from slack_sdk import WebClient
    from job_intel_shortlist_send import load_token

    receipt = poll_release(args.artifact_dir, args.release_root, args.release_id,
                           args.labels_path, args.operator_uid or load_operator_uid(),
                           WebClient(token=load_token()))
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
