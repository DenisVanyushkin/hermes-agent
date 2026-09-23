"""Prove the Slack round trip before anything is built on top of it.

A file can be visibly uploaded and still leave no thread root that a reader
can find again: the upload's own timestamp is not the thread's, and without a
root every later slice has nothing to poll. So the anchor is posted first and
its ``ts`` is what the release is keyed by; the file goes into that known
thread afterwards.

The receipt written here is the whole point. It records what the transport
actually did - channel, thread root, both file ids, the digest of what was
sent - so that "the canary worked" is a document rather than a memory.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

DEFAULT_CHANNEL = "C0B3ZV4BUKC"
DEFAULT_OUT = Path.home() / ".hermes" / "job_intel" / "shortlist-releases"
ENV_FILES = ("/etc/job-intel/job-intel.env", str(Path.home() / ".hermes" / ".env"))
SCHEMA_VERSION = 1

COLUMNS = [
    "kind",
    "vacancy_key",
    "company",
    "title",
    "url",
    "role_fit_verdict",
    "rules",
    "summary",
    "owner_decision",
    "owner_note",
]
FROZEN_COLUMNS = [
    "kind",
    "vacancy_key",
    "company",
    "title",
    "url",
    "role_fit_verdict",
    "rules",
]
OWNER_COLUMNS = ["owner_decision", "owner_note"]


def load_token() -> str:
    """Read the bot token the way the delivery path does, not from the ambient env.

    A host process does not inherit what the gateway loaded: the token lives in
    a file, and reading it here is why this runs at all. The sandbox learned
    this the expensive way - two months of silence because the writer could not
    see a token the reader could.
    """
    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if token:
        return token
    for path in ENV_FILES:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    if key.strip() == "SLACK_BOT_TOKEN":
                        return value.strip().strip("'").strip('"')
        except OSError:
            continue
    raise SystemExit("SLACK_BOT_TOKEN not found in env or " + ", ".join(ENV_FILES))


def canonical_projection(rows: list[dict[str, str]]) -> str:
    """Serialise exactly what the owner may not change, in order.

    The digest cannot cover the file: the file changes the moment a decision is
    typed into it. It covers the ordered rows and the frozen columns, so an
    edited decision verifies while a deleted row does not.
    """
    projection = [[row.get(column, "") for column in FROZEN_COLUMNS] for row in rows]
    return json.dumps(projection, ensure_ascii=False, sort_keys=False, separators=(",", ":"))


def projection_digest(rows: list[dict[str, str]]) -> str:
    return hashlib.sha256(canonical_projection(rows).encode("utf-8")).hexdigest()


def write_csv(path: Path, rows: list[dict[str, str]], meta: dict[str, str]) -> None:
    meta_line = "# " + json.dumps(meta, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(meta_line + "\n")
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in COLUMNS})


def canary_rows() -> list[dict[str, str]]:
    return [
        {
            "kind": "shortlist",
            "vacancy_key": "canary-0001",
            "company": "Canary Industries",
            "title": "Head of Product (transport check)",
            "url": "https://example.invalid/canary-0001",
            "role_fit_verdict": "accept",
            "rules": "canary",
            "summary": "Строка существует только чтобы проверить путь файла туда и обратно.",
            "owner_decision": "",
            "owner_note": "",
        }
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", default=DEFAULT_CHANNEL)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--release-id", default=None)
    args = parser.parse_args()

    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError

    release_id = args.release_id or datetime.now(timezone.utc).strftime("canary-%Y%m%dT%H%M%SZ")
    release_dir = args.out_dir / release_id
    release_dir.mkdir(parents=True, exist_ok=True)

    rows = canary_rows()
    digest = projection_digest(rows)
    meta = {
        "release_id": release_id,
        "schema_version": SCHEMA_VERSION,
        "projection_sha256": digest,
        "editable_columns": OWNER_COLUMNS,
    }
    csv_path = release_dir / f"{release_id}.csv"
    write_csv(csv_path, rows, meta)

    client = WebClient(token=load_token())

    # The anchor carries the identity in text, so recovery can find this exact
    # release in the channel without trusting a local file that may not have
    # been written yet.
    anchor_text = (
        f"*Проверка транспорта* `{release_id}`\n"
        f"projection_sha256 `{digest[:12]}`\n"
        "Это не выпуск ролей. Одна строка, чтобы проверить путь файла туда и обратно.\n"
        "Ответь в этот тред тем же файлом, заполнив колонку `owner_decision` "
        "значением `yes` или `no`."
    )
    try:
        anchor = client.chat_postMessage(channel=args.channel, text=anchor_text)
    except SlackApiError as error:
        print(json.dumps({"stage": "anchor", "error": error.response.get("error")}, ensure_ascii=False))
        return 1
    thread_ts = anchor["ts"]

    receipt = {
        "release_id": release_id,
        "state": "anchored",
        "channel": args.channel,
        "thread_ts": thread_ts,
        "projection_sha256": digest,
        "anchored_at": datetime.now(timezone.utc).isoformat(),
    }
    (release_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    try:
        upload = client.files_upload_v2(
            channel=args.channel,
            thread_ts=thread_ts,
            file=str(csv_path),
            filename=csv_path.name,
            title=f"shortlist canary {release_id}",
            initial_comment=None,
        )
    except SlackApiError as error:
        print(json.dumps({"stage": "upload", "error": error.response.get("error"), "thread_ts": thread_ts}, ensure_ascii=False))
        return 1

    uploaded = upload.get("file") or (upload.get("files") or [{}])[0]
    receipt.update(
        {
            "state": "delivered",
            "bot_file_id": uploaded.get("id"),
            "artifact_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
            "delivered_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    (release_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
