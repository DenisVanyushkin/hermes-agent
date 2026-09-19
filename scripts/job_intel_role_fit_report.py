from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import random
import sqlite3
from typing import Any

from job_intel.product_search.role_fit import RoleFitDecision, evaluate_role_fit


DEFAULT_LABELS = Path("/home/hermes/.hermes/job_intel/manual-shortlist/labels/owner-labels-2026-09.json")
DEFAULT_DB = Path("/var/lib/job-intel/state/job_intel.sqlite3")
_LABEL_TO_VERDICT = {"yes": "accept", "no": "reject", "yes_blocked_language": "blocked"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only role-fit audit for Job Intel")
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--seed", type=int, default=20260919)
    parser.add_argument("--examples", type=int, default=10)
    parser.add_argument("--summary-only", action="store_true")
    return parser.parse_args()


def _load_labels(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload["labels"] if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("owner labels must contain a list")
    return rows


def _connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _row_from_tuple(row: tuple[Any, ...]) -> dict[str, Any]:
    keys = ("vacancy_key", "title", "company", "location", "url", "description")
    return dict(zip(keys, row, strict=True))


def _fetch_one(connection: sqlite3.Connection, vacancy_key: str) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT vacancy_key, title, company, location, url, description
        FROM vacancies
        WHERE vacancy_key = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (vacancy_key,),
    ).fetchone()
    return _row_from_tuple(row) if row else None


def _fetch_pool(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT vacancy_key, title, company, location, url, description
        FROM vacancies
        WHERE first_seen_at >= datetime('now', '-7 day')
          AND length(description) > 800
        ORDER BY vacancy_key, id
        """
    ).fetchall()
    return [_row_from_tuple(row) for row in rows]


def _evaluate(row: dict[str, Any]) -> RoleFitDecision:
    return evaluate_role_fit(
        row.get("title") or "",
        row.get("company") or "",
        row.get("location") or "",
        row.get("description") or "",
    )


def _short(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _print_owner_matrix(connection: sqlite3.Connection, labels: list[dict[str, Any]]) -> None:
    print("OWNER LABEL MATRIX (13 examples)")
    counts: Counter[tuple[str, str]] = Counter()
    missing: list[str] = []
    for label in labels:
        expected = _LABEL_TO_VERDICT[label["verdict"]]
        row = _fetch_one(connection, label["vacancy_key"])
        if row is None:
            missing.append(label["vacancy_key"])
            print(f"MISSING {label['vacancy_key']} expected={expected}")
            continue
        decision = _evaluate(row)
        counts[(expected, decision.verdict)] += 1
        outcome = "OK" if expected == decision.verdict else "ERROR"
        rules = ",".join(decision.rule_ids) or "-"
        print(
            f"{outcome} expected={expected:<7} predicted={decision.verdict:<7} "
            f"key={label['vacancy_key']} title={_short(row['title'])} "
            f"company={_short(row['company'])} rules={rules}"
        )
    print("CONFUSION expected\\predicted accept reject blocked")
    for expected in ("accept", "reject", "blocked"):
        print(expected, *(counts[(expected, predicted)] for predicted in ("accept", "reject", "blocked")))
    if missing:
        print("MISSING_KEYS", ",".join(missing))


def _sample_rows(rows: list[dict[str, Any]], seed: int, limit: int) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: row["vacancy_key"])
    if len(ordered) <= limit:
        return ordered
    return random.Random(seed).sample(ordered, limit)


def _print_pool_report(rows: list[dict[str, Any]], seed: int, example_limit: int) -> None:
    decisions = [(row, _evaluate(row)) for row in rows]
    by_verdict: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rule_counts: Counter[str] = Counter()
    for row, decision in decisions:
        by_verdict[decision.verdict].append(row)
        rule_counts.update(decision.rule_ids)

    print("POOL DISTRIBUTION (first_seen_at >= datetime('now','-7 day'), length(description) > 800)")
    print(f"total={len(rows)}")
    for verdict in ("accept", "reject", "blocked"):
        print(f"{verdict}={len(by_verdict[verdict])}")
    print("RULE MATCH COUNTS")
    for rule_id, count in sorted(rule_counts.items()):
        print(f"{rule_id}={count}")
    print("RANDOM EXAMPLES (seed={})".format(seed))
    for verdict in ("accept", "reject", "blocked"):
        print(f"[{verdict}]")
        for row in _sample_rows(by_verdict[verdict], seed, example_limit):
            decision = _evaluate(row)
            print(
                f"key={row['vacancy_key']} title={_short(row['title'])} "
                f"company={_short(row['company'])} location={_short(row['location'])} "
                f"url={_short(row['url'])} rules={','.join(decision.rule_ids) or '-'}"
            )

    low_support = sorted(rule_id for rule_id, count in rule_counts.items() if count <= 3)
    print("OVERFITTING REVIEW")
    if low_support:
        print(
            "candidate_low_support="
            + ",".join(low_support)
            + "; low support is a review flag, not proof of overfitting"
        )
    else:
        print("candidate_low_support=none")
    print(
        "No company names or sample-specific phrases are encoded in the rules; "
        "the candidate list is based only on low support in this seven-day pool."
    )


def _print_summary(connection: sqlite3.Connection, labels: list[dict[str, Any]]) -> None:
    owner_counts: Counter[tuple[str, str]] = Counter()
    owner_errors: Counter[tuple[str, str, tuple[str, ...]]] = Counter()
    missing = 0
    for label in labels:
        row = _fetch_one(connection, label["vacancy_key"])
        if row is None:
            missing += 1
            continue
        expected = _LABEL_TO_VERDICT[label["verdict"]]
        decision = _evaluate(row)
        owner_counts[(expected, decision.verdict)] += 1
        if expected != decision.verdict:
            owner_errors[(expected, decision.verdict, decision.rule_ids)] += 1
    pool = _fetch_pool(connection)
    verdict_counts = Counter(_evaluate(row).verdict for row in pool)
    rule_counts: Counter[str] = Counter()
    for row in pool:
        rule_counts.update(_evaluate(row).rule_ids)
    print("OWNER_SUMMARY total={} missing={}".format(len(labels), missing))
    print(
        "OWNER_CONFUSION "
        + " ".join(
            f"{expected}/{predicted}={owner_counts[(expected, predicted)]}"
            for expected in ("accept", "reject", "blocked")
            for predicted in ("accept", "reject", "blocked")
        )
    )
    for (expected, predicted, rules), count in sorted(owner_errors.items()):
        print(
            "OWNER_ERROR_SIGNATURE "
            f"count={count} expected={expected} predicted={predicted} rules={','.join(rules) or '-'}"
        )
    print(f"POOL_SUMMARY total={len(pool)} " + " ".join(f"{v}={verdict_counts[v]}" for v in ("accept", "reject", "blocked")))
    print("POOL_RULE_COUNTS " + " ".join(f"{rule}={count}" for rule, count in sorted(rule_counts.items())))


def main() -> None:
    args = _parse_args()
    labels = _load_labels(args.labels)
    with _connect_read_only(args.db) as connection:
        if args.summary_only:
            _print_summary(connection, labels)
        else:
            _print_owner_matrix(connection, labels)
            print()
            _print_pool_report(_fetch_pool(connection), args.seed, args.examples)


if __name__ == "__main__":
    main()
