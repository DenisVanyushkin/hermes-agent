"""Weekly role-fit calibration: put the decisions that are cheapest to dispute in front of the owner.

The rule set is only ever as good as the classes the owner has ruled on. Run
509 matched all twenty labels and still accepted twenty roles the policy
disqualifies, because no label covered product design or product marketing.
This report exists to keep finding those blind spots: it surfaces what would
be delivered, what a single rule decided on its own, and what the rules left
unresolved, so a week of collection turns into a week of sharper criteria.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

DEFAULT_DB = Path("/var/lib/job-intel/state/job_intel.sqlite3")
DEFAULT_OUT = Path.home() / ".hermes" / "job_intel" / "calibration"
SINGLE_RULE_SAMPLE = 12
BLOCKED_SAMPLE = 8


def connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def latest_run_id(connection: sqlite3.Connection) -> int | None:
    row = connection.execute(
        "SELECT run_id FROM vacancy_observability ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    return int(row[0]) if row else None


def _payload(rules_json: str | None) -> dict[str, Any]:
    if not rules_json:
        return {}
    try:
        parsed = json.loads(rules_json)
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed, list):
        return {"rule_ids": [str(item) for item in parsed]}
    if isinstance(parsed, dict):
        return parsed
    return {}


def build_report(connection: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT vacancy_key, company, title, location, source, canonical_url,
               role_fit_verdict, role_fit_rules_json, accepted
        FROM vacancy_observability
        WHERE run_id = ?
        ORDER BY company, title
        """,
        (run_id,),
    ).fetchall()

    verdicts: Counter[str] = Counter()
    rule_counts: Counter[str] = Counter()
    ruleset_versions: Counter[str] = Counter()
    accepts: list[dict[str, Any]] = []
    single_rule_rejects: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    disagreements: list[dict[str, Any]] = []

    for key, company, title, location, source, url, verdict, rules_json, legacy_accepted in rows:
        payload = _payload(rules_json)
        rule_ids = [str(item) for item in payload.get("rule_ids", [])]
        verdicts[verdict or "not_evaluated"] += 1
        rule_counts.update(rule_ids)
        if payload.get("ruleset_version"):
            ruleset_versions[str(payload["ruleset_version"])] += 1

        item = {
            "vacancy_key": key,
            "company": company,
            "title": title,
            "location": location,
            "source": source,
            "url": url,
            "rule_ids": rule_ids,
        }
        if verdict == "accept":
            accepts.append(item)
        elif verdict == "reject" and len(rule_ids) == 1:
            # One rule decided alone: the cheapest decision for the owner to
            # overturn, and the one where a single bad pattern does most damage.
            single_rule_rejects.append(item)
        elif verdict == "blocked":
            blocked.append(item)
        if bool(legacy_accepted) != (verdict == "accept"):
            disagreements.append({**item, "verdict": verdict, "legacy_accepted": bool(legacy_accepted)})

    return {
        "report": "role_fit_calibration",
        "version": "v1",
        "run_id": run_id,
        "produced_at": datetime.now(timezone.utc).isoformat(),
        "ruleset_versions": dict(ruleset_versions),
        "counts": {
            "vacancies": len(rows),
            "verdicts": dict(verdicts),
            "disagreements_with_legacy": len(disagreements),
        },
        "rule_counts": dict(rule_counts.most_common()),
        "would_deliver": accepts,
        "single_rule_rejects": single_rule_rejects[:SINGLE_RULE_SAMPLE],
        "single_rule_rejects_total": len(single_rule_rejects),
        "blocked_unresolved": blocked[:BLOCKED_SAMPLE],
        "blocked_unresolved_total": len(blocked),
    }


def render_text(report: dict[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        f"ROLE_FIT_CALIBRATION run_id={report['run_id']} vacancies={counts['vacancies']}",
        f"ruleset={','.join(report['ruleset_versions']) or 'unknown'}",
        "VERDICTS " + " ".join(f"{k}={v}" for k, v in sorted(counts["verdicts"].items())),
        "",
        f"WOULD DELIVER ({len(report['would_deliver'])})",
    ]
    for item in report["would_deliver"]:
        lines.append(f"   {item['company']} | {item['title']} | {item['location']}")
    lines += ["", f"SINGLE-RULE REJECTS (showing {len(report['single_rule_rejects'])} of {report['single_rule_rejects_total']})"]
    for item in report["single_rule_rejects"]:
        lines.append(f"   [{','.join(item['rule_ids'])}] {item['company']} | {item['title']}")
    lines += ["", f"BLOCKED, UNRESOLVED (showing {len(report['blocked_unresolved'])} of {report['blocked_unresolved_total']})"]
    for item in report["blocked_unresolved"]:
        lines.append(f"   [{','.join(item['rule_ids'])}] {item['company']} | {item['title']} | {item['location']}")
    lines += [
        "",
        "TOP RULES",
    ]
    for rule_id, count in list(report["rule_counts"].items())[:12]:
        lines.append(f"   {rule_id} {count}")
    lines += [
        "",
        "Every line above is a criterion you can overturn. A reply naming a role and",
        "the reason it is wrong becomes a label; the rules are changed from labels,",
        "never from a guess about what you would have wanted.",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--run-id", type=int, default=None)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--print-only", action="store_true")
    args = parser.parse_args()

    connection = connect_read_only(args.db)
    run_id = args.run_id if args.run_id is not None else latest_run_id(connection)
    if run_id is None:
        print("no observability rows found; nothing to calibrate")
        return 1

    report = build_report(connection, run_id)
    text = render_text(report)
    print(text)
    if args.print_only:
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = args.out_dir / f"{stamp}-run{run_id}"
    target.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    (target / "calibration.json").write_text(payload, encoding="utf-8")
    (target / "calibration.txt").write_text(text, encoding="utf-8")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    (target / "SHA256SUMS").write_text(f"{digest}  calibration.json\n", encoding="utf-8")
    print(f"\nartifact: {target}\nsha256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
