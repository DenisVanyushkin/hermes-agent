"""Publish a frozen shortlist artifact to Slack as a CSV the owner can fill in.

Mechanics are the canary's, proven on 2026-09-22: the anchor is posted first so
the thread root exists before the file, and the receipt records what the
transport actually did rather than what we remember it doing.

What is new here is the payload: real rows from a frozen build artifact, plus a
lead excerpt of the stored description so a verdict does not have to be guessed
from a title alone. The excerpt is raw text, not a model summary - calling it a
summary would overstate what it is.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

DEFAULT_CHANNEL = "C0B4MM6D52A"
DEFAULT_OUT = Path.home() / ".hermes" / "job_intel" / "shortlist-releases"
DB = "/var/lib/job-intel/state/job_intel.sqlite3"
ENV_FILES = ("/etc/job-intel/job-intel.env", str(Path.home() / ".hermes" / ".env"))
SCHEMA_VERSION = 1
EXCERPT_CHARS = 600

COLUMNS = [
    "kind",
    "vacancy_key",
    "company",
    "title",
    "location",
    "source",
    "url",
    "role_fit_verdict",
    "excerpt",
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
]
OWNER_COLUMNS = ["owner_decision", "owner_note"]


def load_token() -> str:
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
    raise SystemExit("SLACK_BOT_TOKEN not found")


def excerpt_for(conn: sqlite3.Connection, url: str) -> str:
    row = conn.execute(
        "select description from vacancies where url = ? and description is not null "
        "order by length(description) desc limit 1",
        (url,),
    ).fetchone()
    if not row or not row[0]:
        return ""
    text = re.sub(r"\s+", " ", row[0]).strip()
    return text[:EXCERPT_CHARS]


def canonical_projection(rows: list[dict[str, str]]) -> str:
    projection = [[row.get(column, "") for column in FROZEN_COLUMNS] for row in rows]
    return json.dumps(projection, ensure_ascii=False, separators=(",", ":"))


def projection_digest(rows: list[dict[str, str]]) -> str:
    return hashlib.sha256(canonical_projection(rows).encode("utf-8")).hexdigest()


def write_csv(path: Path, rows: list[dict[str, str]], meta: dict) -> None:
    """Carry the release metadata as a real row, not as a leading comment line.

    A ``#`` line full of commas and quotes is not CSV: a spreadsheet reads the
    first line to infer the shape of the file, chokes on it, and imports the
    whole thing as a single column. Numbers did exactly that on the first
    release. The metadata therefore travels as an ordinary row whose ``kind``
    is ``release``, so every line in the file obeys the same grammar.
    """
    release_row = {
        "kind": "release",
        "vacancy_key": meta["release_id"],
        "role_fit_verdict": meta["projection_sha256"],
        "excerpt": json.dumps(meta, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    }
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerow({column: release_row.get(column, "") for column in COLUMNS})
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in COLUMNS})


def build_rows(artifact: dict, conn: sqlite3.Connection) -> list[dict[str, str]]:
    rows = []
    for item in artifact["items"]:
        rows.append(
            {
                "kind": "shortlist",
                "vacancy_key": item["vacancy_key"],
                "company": item.get("company", ""),
                "title": item.get("title", ""),
                "location": item.get("location", "") or "",
                "source": item.get("source", "") or "",
                "url": item.get("url", ""),
                "role_fit_verdict": "accept",
                "excerpt": excerpt_for(conn, item.get("url", "")),
                "owner_decision": "",
                "owner_note": "",
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--channel", default=DEFAULT_CHANNEL)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--release-id", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    artifact = json.loads((args.artifact / "shortlist.json").read_text(encoding="utf-8"))
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = build_rows(artifact, conn)
    missing = sum(1 for row in rows if not row["excerpt"])

    release_id = args.release_id or datetime.now(timezone.utc).strftime("shortlist-%Y%m%dT%H%M%SZ")
    release_dir = args.out_dir / release_id
    release_dir.mkdir(parents=True, exist_ok=True)

    digest = projection_digest(rows)
    meta = {
        "release_id": release_id,
        "schema_version": SCHEMA_VERSION,
        "projection_sha256": digest,
        "editable_columns": OWNER_COLUMNS,
        "source_artifact": artifact["artifact"],
        "source_artifact_sha256": artifact.get("sha256", ""),
        "run_id": artifact.get("run_id"),
        "ruleset": artifact.get("ruleset_versions"),
        "commit": artifact.get("commit"),
    }
    csv_path = release_dir / f"{release_id}.csv"
    write_csv(csv_path, rows, meta)

    summary = {
        "release_id": release_id,
        "rows": len(rows),
        "rows_without_excerpt": missing,
        "projection_sha256": digest,
        "csv": str(csv_path),
        "channel": args.channel,
    }
    if args.dry_run:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0

    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError

    client = WebClient(token=load_token())
    anchor_text = (
        f"*Выдача ролей на оценку* `{release_id}`\n"
        f"{len(rows)} позиций, прогон {artifact.get('run_id')}, "
        f"projection_sha256 `{digest[:12]}`\n"
        "В файле заполни колонку `owner_decision` значениями `yes` или `no`, "
        "в `owner_note` — одной фразой почему, если `no`. "
        "Остальные колонки не меняй. Ответь в этот тред тем же файлом."
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
        "source_artifact": artifact["artifact"],
        "rows": len(rows),
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
            title=f"shortlist {release_id}",
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
