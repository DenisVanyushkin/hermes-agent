"""Build the delivery batch from one run's accepted roles, and fix it with a digest.

The batch the owner reviews and the batch that is delivered must be the same
object, not two runs of the same query: the database moves twice a day, so
re-selecting at delivery time would send something the owner never saw. This
builder therefore reads one pinned run, writes a canonical serialisation, and
records its SHA-256. Delivery re-computes that digest and refuses to send on
any mismatch.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
from typing import Any

DEFAULT_DB = Path("/var/lib/job-intel/state/job_intel.sqlite3")
DEFAULT_OUT = Path.home() / ".hermes" / "job_intel" / "shortlist"
# Both numbers are the owner's, answered explicitly on 2026-09-22: a wider
# batch loses fewer good roles to the cap, and three per employer keeps one
# company with seven openings from taking a quarter of the release while
# still showing genuinely distinct mandates.
BATCH_CAP = 25
MAX_PER_COMPANY = 3

_NORMALISE_SPLIT = re.compile(r"[^a-z0-9]+")
_TRAILING_REQUISITION = re.compile(r"\b\d{3,}\b")


def connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def normalise_title(title: str) -> str:
    """Collapse a title to its identity, so one role in two cities is one role."""
    without_requisition = _TRAILING_REQUISITION.sub(" ", title.lower())
    return " ".join(part for part in _NORMALISE_SPLIT.split(without_requisition) if part)


def dedup_key(company: str, title: str) -> tuple[str, str]:
    return (" ".join(company.lower().split()), normalise_title(title))


def select_batch(rows: list[dict[str, Any]], cap: int = BATCH_CAP,
                 max_per_company: int = MAX_PER_COMPANY,
                 issued_keys: frozenset[str] = frozenset(),
                 ) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Newest first, capped per company, hard cap, no filling with weaker roles.

    Ordering is total: first_seen_at descending, then company, title and
    vacancy_key, so the same run always produces the same batch in the same
    order. A shortage is delivered as a shortage; there is nothing in this
    function that can reach past the accepted set to reach the cap.

    Returns the batch and an audit of everything held back, grouped by the
    reason it was held back. A diversity policy that works by a bare
    `continue` is a policy nobody can review: a collapsed title and a role
    that was never a candidate look identical from the outside, and a false
    collapse becomes a loss the owner cannot notice. Naming the suppressed
    rows is what keeps that from being silent.

    `issued_keys` are vacancy keys already delivered in an earlier release.
    They are dropped before the cap is applied, not after: filtering
    afterwards would let roles the owner has already seen consume the cap and
    silently shrink the release. It arrives as a set rather than as a reader,
    so this function stays free of any knowledge of where releases are
    recorded, and its tests need no manifests on disk.
    """
    ordered = sorted(
        rows,
        key=lambda row: (
            _descending(row.get("first_seen_at") or ""),
            row.get("company") or "",
            row.get("title") or "",
            row.get("vacancy_key") or "",
        ),
    )
    seen: dict[tuple[str, str], str] = {}
    per_company: dict[str, int] = {}
    batch: list[dict[str, Any]] = []
    suppressed: dict[str, list[dict[str, Any]]] = {
        "already_issued": [],
        "title_collapsed": [],
        "company_capped": [],
        "over_cap": [],
    }

    def _record(reason: str, row: dict[str, Any], **extra: Any) -> None:
        suppressed[reason].append(
            {
                "vacancy_key": row.get("vacancy_key"),
                "company": row.get("company"),
                "title": row.get("title"),
                "location": row.get("location"),
                "url": row.get("url"),
                **extra,
            }
        )

    for row in ordered:
        company = " ".join((row.get("company") or "").lower().split())
        key = dedup_key(row.get("company") or "", row.get("title") or "")
        # The order of these branches is the definition of the groups: each row
        # leaves by the first one that matches, so the audit groups below are
        # disjoint by construction rather than by arithmetic. Changing the
        # order changes the partition.
        if row.get("vacancy_key") in issued_keys:
            _record("already_issued", row)
            continue
        if len(batch) >= cap:
            _record("over_cap", row)
            continue
        if key in seen:
            _record("title_collapsed", row, collapsed_into=seen[key])
            continue
        if per_company.get(company, 0) >= max_per_company:
            _record("company_capped", row, company_limit=max_per_company)
            continue
        seen[key] = str(row.get("vacancy_key") or "")
        per_company[company] = per_company.get(company, 0) + 1
        batch.append(row)
    return batch, suppressed


def _descending(value: str) -> tuple[int, str]:
    """Sort a timestamp string descending inside an otherwise ascending key."""
    return (0, "") if not value else (-1, _invert(value))


def _invert(value: str) -> str:
    return "".join(chr(0x10FFFD - ord(char)) if ord(char) < 0x10FFFD else char for char in value)


def fetch_accepted(connection: sqlite3.Connection, run_id: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT o.vacancy_key, o.company, o.title, o.location, o.source,
               COALESCE(o.canonical_url, o.url) AS url,
               o.role_fit_rules_json, v.first_seen_at, v.last_seen_at, v.posted_at
        FROM vacancy_observability AS o
        LEFT JOIN vacancies AS v ON v.vacancy_key = o.vacancy_key
        WHERE o.run_id = ? AND o.role_fit_verdict = 'accept'
        GROUP BY o.vacancy_key
        """,
        (run_id,),
    ).fetchall()
    columns = [
        "vacancy_key", "company", "title", "location", "source", "url",
        "role_fit_rules_json", "first_seen_at", "last_seen_at", "posted_at",
    ]
    return [dict(zip(columns, row)) for row in rows]


def ruleset_versions(rows: list[dict[str, Any]]) -> list[str]:
    versions: set[str] = set()
    for row in rows:
        try:
            payload = json.loads(row.get("role_fit_rules_json") or "{}")
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("ruleset_version"):
            versions.add(str(payload["ruleset_version"]))
    return sorted(versions)


def build(connection: sqlite3.Connection, run_id: int, commit: str,
          issued_keys: frozenset[str] = frozenset()) -> dict[str, Any]:
    accepted = fetch_accepted(connection, run_id)
    batch, suppressed = select_batch(accepted, issued_keys=issued_keys)
    return {
        "artifact": "job_intel_shortlist",
        "version": "v1",
        "run_id": run_id,
        "commit": commit,
        "ruleset_versions": ruleset_versions(accepted),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "cap": BATCH_CAP,
        "max_per_company": MAX_PER_COMPANY,
        "accepted_total": len(accepted),
        "issued_keys_supplied": len(issued_keys),
        "suppressed_counts": {reason: len(rows) for reason, rows in sorted(suppressed.items())},
        "suppressed": {reason: rows for reason, rows in sorted(suppressed.items())},
        "delivered_count": len(batch),
        "short_of_cap": len(batch) < BATCH_CAP,
        "items": [
            {
                "position": index + 1,
                "vacancy_key": row["vacancy_key"],
                "company": row["company"],
                "title": row["title"],
                "location": row["location"],
                "source": row["source"],
                "url": row["url"],
                "first_seen_at": row["first_seen_at"],
                "posted_at": row["posted_at"],
            }
            for index, row in enumerate(batch)
        ],
    }


def canonical_bytes(artifact: dict[str, Any]) -> bytes:
    """Serialise for hashing: the digest must not depend on dict order or build time."""
    hashable = {key: value for key, value in artifact.items() if key != "built_at"}
    return json.dumps(hashable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(artifact: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(artifact)).hexdigest()


def _head_commit(repo: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def render_text(artifact: dict[str, Any], sha: str) -> str:
    lines = [
        f"SHORTLIST run_id={artifact['run_id']} delivered={artifact['delivered_count']} of cap {artifact['cap']}",
        f"accepted_total={artifact['accepted_total']} ruleset={','.join(artifact['ruleset_versions']) or 'unknown'}",
        f"commit={artifact['commit']}",
        f"sha256={sha}",
        "",
    ]
    for item in artifact["items"]:
        lines.append(f"{item['position']}. {item['company']} — {item['title']}")
        lines.append(f"   {item['location']} | {item['source']} | first seen {item['first_seen_at']}")
        lines.append(f"   {item['url']}")
    held = artifact.get("suppressed_counts") or {}
    if any(held.values()):
        lines += ["", "HELD BACK " + " ".join(f"{k}={v}" for k, v in sorted(held.items()) if v)]
        for reason, rows in sorted((artifact.get("suppressed") or {}).items()):
            for entry in rows[:5]:
                lines.append(f"   [{reason}] {entry['company']} — {entry['title']} | {entry['location']}")
    if artifact["short_of_cap"]:
        lines += ["", "Fewer than the cap: the accepted set held no further distinct companies.",
                  "Nothing weaker was added to reach seven."]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--repo", type=Path, default=Path.home() / ".hermes" / "hermes-agent")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument(
        "--issued-keys",
        type=Path,
        default=None,
        help="JSON array of vacancy keys already delivered; they are dropped before the cap",
    )
    args = parser.parse_args()

    issued_keys: frozenset[str] = frozenset()
    if args.issued_keys is not None:
        payload = json.loads(args.issued_keys.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise SystemExit("--issued-keys must contain a JSON array of vacancy keys")
        issued_keys = frozenset(str(item) for item in payload)

    connection = connect_read_only(args.db)
    artifact = build(connection, args.run_id, _head_commit(args.repo), issued_keys=issued_keys)
    sha = digest(artifact)
    print(render_text(artifact, sha))
    if args.print_only:
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = args.out_dir / f"{stamp}-run{args.run_id}"
    target.mkdir(parents=True, exist_ok=True)
    (target / "shortlist.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    (target / "shortlist.txt").write_text(render_text(artifact, sha), encoding="utf-8")
    (target / "SHA256SUMS").write_text(f"{sha}  canonical\n", encoding="utf-8")
    print(f"\nartifact: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
