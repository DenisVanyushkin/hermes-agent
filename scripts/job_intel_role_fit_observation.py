from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
from typing import Any


DEFAULT_DB = Path("/var/lib/job-intel/state/job_intel.sqlite3")


def _connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _rule_ids(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        payload: Any = json.loads(value)
    except json.JSONDecodeError:
        return []
    if isinstance(payload, list):
        return [str(item) for item in payload if item]
    if isinstance(payload, dict):
        rule_ids = payload.get("rule_ids")
        if isinstance(rule_ids, list):
            return [str(item) for item in rule_ids if item]
    return []


def render_report(connection: sqlite3.Connection, run_id: int) -> str:
    rows = connection.execute(
        """
        SELECT accepted, role_fit_verdict, role_fit_rules_json
        FROM vacancy_observability
        WHERE run_id = ?
        ORDER BY rowid
        """,
        (run_id,),
    ).fetchall()
    verdicts: Counter[str] = Counter()
    rules: Counter[str] = Counter()
    accepted_rejected = 0
    rejected_accepted = 0
    for accepted, verdict, rules_json in rows:
        normalized_verdict = str(verdict) if verdict else "not_evaluated"
        verdicts[normalized_verdict] += 1
        rules.update(_rule_ids(rules_json))
        if accepted and normalized_verdict == "reject":
            accepted_rejected += 1
        if not accepted and normalized_verdict == "accept":
            rejected_accepted += 1

    verdict_order = ("accept", "reject", "blocked", "error", "not_evaluated")
    verdict_text = " ".join(f"{key}={verdicts[key]}" for key in verdict_order)
    lines = [
        f"ROLE_FIT_OBSERVATION run_id={run_id} vacancies={len(rows)}",
        f"VERDICTS {verdict_text}",
        "TOP_RULES",
    ]
    if rules:
        lines.extend(f"{rule} {count}" for rule, count in rules.most_common())
    else:
        lines.append("none 0")
    lines.extend(
        [
            "DISAGREEMENTS",
            f"accepted_pipeline_rejected_by_role_fit={accepted_rejected}",
            f"rejected_pipeline_accepted_by_role_fit={rejected_accepted}",
        ]
    )
    return "\n".join(lines) + "\n"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only role-fit observation report")
    parser.add_argument("run_id", type=int)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    connection = _connect_read_only(args.db)
    try:
        print(render_report(connection, args.run_id), end="")
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
