from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import random
import re
import sqlite3
from typing import Any

from job_intel.product_search.role_fit import KNOWN_LANGUAGE_CODES, RoleFitDecision, evaluate_role_fit


DEFAULT_LABELS = Path("/home/hermes/.hermes/job_intel/manual-shortlist/labels/owner-labels-2026-09.json")
DEFAULT_DB = Path("/var/lib/job-intel/state/job_intel.sqlite3")
_LABEL_TO_VERDICT = {
    "yes": "accept",
    "no": "reject",
    "yes_blocked_language": "blocked",
    "accept": "accept",
    "reject": "reject",
    "blocked": "blocked",
}
_ENGINEERING_LIKE_TITLE = re.compile(
    r"\b(?:software|engineering|backend|frontend|full[- ]stack|data|devops|qa|quality)\s+(?:engineer|lead|manager|developer)|\b(?:software engineer|developer|data scientist|devops engineer|qa engineer)\b",
    re.IGNORECASE,
)
_INDEPENDENT_LANGUAGE_MARKERS = {
    "fi": frozenset(
        "etsimme tehtävä tehtävässä suomalaisen sujuvoittaa työskentelet tarjoamme odotamme palkkahaarukka yhteydessä työvoiman".split()
    ),
    "da": frozenset(
        "virksomhed vores hjælper søger rollen kunder arbejde produkt udvikling stilling ledelse".split()
    ),
    "es": frozenset(
        "somos empresa contamos nuestro objetivo buscamos desafíos responsabilidades requisitos beneficios liderazgo".split()
    ),
    "nl": frozenset(
        "bedrijf onze klanten zoeken vacature ervaring werken product ontwikkeling functie leiding".split()
    ),
}
_INDEPENDENT_LANGUAGE_DISTINCTIVE = {
    "fi": frozenset("äöå"),
    "da": frozenset("æøå"),
    "es": frozenset("áéíóúñü"),
    "nl": frozenset(),
}
_STRICT_LANGUAGE_CONFIRMATION_KEYS = (
    "cc61d78e87248b87337e26a25f29cda9eda82a619b77c858a833087878fd208c",
    "f6bc71075ac9e73410b967aa34ca7dec669cdeef6ea6c75f1724ac778d1c12d7",
)


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


def _evaluate(
    row: dict[str, Any],
    *,
    supported_languages: tuple[str, ...] | None = None,
) -> RoleFitDecision:
    kwargs = {} if supported_languages is None else {"supported_languages": supported_languages}
    return evaluate_role_fit(
        row.get("title") or "",
        row.get("company") or "",
        row.get("location") or "",
        row.get("description") or "",
        **kwargs,
    )


def _short(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _independent_language_signal(text: str) -> tuple[str, int] | None:
    body = text.split("Job criteria:", 1)[0].lower()
    tokens = set(re.findall(r"[^\W\d_]+", body, flags=re.UNICODE))
    letters = [char for char in body if char.isalpha()]
    for language, markers in _INDEPENDENT_LANGUAGE_MARKERS.items():
        marker_count = len(tokens & markers)
        distinctive_count = sum(char in _INDEPENDENT_LANGUAGE_DISTINCTIVE[language] for char in body)
        if marker_count >= 3 or (letters and distinctive_count * 100 >= len(letters)):
            return language, marker_count
    return None


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
        note = "insufficient_input_language_evidence" if expected == "blocked" and not decision.rule_ids else ""
        print(
            f"{outcome} expected={expected:<7} predicted={decision.verdict:<7} "
            f"key={label['vacancy_key']} title={_short(row['title'])} "
            f"company={_short(row['company'])} rules={rules} note={note or '-'}"
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
    legacy_decisions = [
        (row, _evaluate(row, supported_languages=KNOWN_LANGUAGE_CODES))
        for row in rows
    ]
    decisions_by_key = {row["vacancy_key"]: decision for row, decision in decisions}
    by_verdict: dict[str, list[dict[str, Any]]] = defaultdict(list)
    legacy_by_verdict: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rule_counts: Counter[str] = Counter()
    for row, decision in decisions:
        by_verdict[decision.verdict].append(row)
        rule_counts.update(decision.rule_ids)
    for row, decision in legacy_decisions:
        legacy_by_verdict[decision.verdict].append(row)

    print("POOL DISTRIBUTION AFTER LANGUAGE GATE (first_seen_at >= datetime('now','-7 day'), length(description) > 800)")
    print(f"total={len(rows)}")
    for verdict in ("accept", "reject", "blocked"):
        print(f"{verdict}={len(by_verdict[verdict])}")
    print("POOL DISTRIBUTION BEFORE LANGUAGE GATE")
    print(f"total={len(rows)}")
    for verdict in ("accept", "reject", "blocked"):
        print(f"{verdict}={len(legacy_by_verdict[verdict])}")
    legacy_blocked = [row for row, decision in legacy_decisions if decision.verdict == "blocked"]
    legacy_blocked_now = [(row, decisions_by_key[row["vacancy_key"]]) for row in legacy_blocked]
    print(
        "PREVIOUS_BLOCKED_FATE "
        f"baseline={len(legacy_blocked)} "
        "now_reject_new_language_rule="
        f"{sum('description_language_not_supported' in decision.rule_ids for _, decision in legacy_blocked_now)} "
        f"now_blocked={sum(decision.verdict == 'blocked' for _, decision in legacy_blocked_now)} "
        f"now_other={sum(decision.verdict not in {'reject', 'blocked'} for _, decision in legacy_blocked_now)}"
    )
    if legacy_blocked:
        example_row, example_decision = next(
            (row, decision)
            for row, decision in legacy_decisions
            if decision.verdict == "blocked"
        )
        print(
            "PREVIOUS_BLOCKED_EXAMPLE "
            f"key={example_row['vacancy_key']} title={_short(example_row['title'])} "
            f"company={_short(example_row['company'])} "
            f"legacy_rules={','.join(example_decision.rule_ids) or '-'}"
        )
    print("RULE MATCH COUNTS")
    for rule_id, count in sorted(rule_counts.items()):
        print(f"{rule_id}={count}")
    new_rule_rows = [
        row for row, decision in decisions
        if "description_language_not_supported" in decision.rule_ids
    ]
    print("NEW LANGUAGE RULE EXAMPLES (seed={}, count={})".format(seed, example_limit))
    for row in _sample_rows(new_rule_rows, seed, example_limit):
        decision = _evaluate(row)
        print(
            f"key={row['vacancy_key']} title={_short(row['title'])} "
            f"company={_short(row['company'])} location={_short(row['location'])} "
            f"url={_short(row['url'])} rules={','.join(decision.rule_ids) or '-'}"
        )

    independent_before = []
    independent_after = []
    for row, decision in decisions:
        signal = _independent_language_signal(row.get("description") or "")
        if signal is None:
            continue
        legacy_decision = next(
            legacy for legacy_row, legacy in legacy_decisions if legacy_row["vacancy_key"] == row["vacancy_key"]
        )
        if legacy_decision.verdict == "accept":
            independent_before.append((row, signal))
        if decision.verdict == "accept":
            independent_after.append((row, signal))
    print(
        "INDEPENDENT_LANGUAGE_AUDIT method=marker_words_and_distinctive_letters "
        f"flagged_accept_before={len(independent_before)} flagged_accept_after={len(independent_after)}"
    )
    for label, candidates in (("BEFORE", independent_before), ("AFTER", independent_after)):
        for row, signal in candidates:
            print(
                f"INDEPENDENT_LANGUAGE_CANDIDATE phase={label} key={row['vacancy_key']} "
                f"title={_short(row['title'])} company={_short(row['company'])} signal={signal}"
            )
    for key in _STRICT_LANGUAGE_CONFIRMATION_KEYS:
        row = next((candidate for candidate in rows if candidate["vacancy_key"] == key), None)
        if row is None:
            print(f"STRICT_LANGUAGE_CONFIRMATION key={key} missing=true")
            continue
        decision = decisions_by_key[key]
        print(
            f"STRICT_LANGUAGE_CONFIRMATION key={key} verdict={decision.verdict} "
            f"rules={','.join(decision.rule_ids) or '-'}"
        )

    suspicious = [
        (row, decision)
        for row, decision in decisions
        if decision.verdict == "accept" and _ENGINEERING_LIKE_TITLE.search(row.get("title") or "")
    ]
    print("SUSPICIOUS ACCEPTS (engineering-like title check)")
    print(f"count={len(suspicious)}")
    for row, decision in suspicious[:10]:
        print(
            f"title={_short(row['title'])} company={_short(row['company'])} "
            f"rules={','.join(decision.rule_ids) or '-'}"
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
    decisions = [(row, _evaluate(row)) for row in pool]
    verdict_counts = Counter(decision.verdict for _, decision in decisions)
    rule_counts: Counter[str] = Counter()
    for _, decision in decisions:
        rule_counts.update(decision.rule_ids)
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
    for label in labels:
        row = _fetch_one(connection, label["vacancy_key"])
        if row is None:
            continue
        expected = _LABEL_TO_VERDICT[label["verdict"]]
        decision = _evaluate(row)
        if expected != decision.verdict:
            print(
                "OWNER_ERROR_KEY "
                f"key={label['vacancy_key']} expected={expected} predicted={decision.verdict} "
                f"rules={','.join(decision.rule_ids) or '-'}"
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
