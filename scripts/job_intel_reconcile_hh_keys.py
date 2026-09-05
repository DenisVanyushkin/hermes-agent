#!/usr/bin/env python3
"""Reconcile legacy HeadHunter vacancy keys to canonical URL keys.

The command is intentionally dry-run by default. Use ``--apply`` only after
reviewing the report on a database copy and backing up the source database.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import sqlite3
from pathlib import Path
import tempfile
from typing import Iterable
from urllib.parse import quote

from job_intel.dedup import canonical_vacancy_key
from job_intel.models import Vacancy


KEY_TABLE_UNIQUES: dict[str, tuple[str, ...]] = {
    "user_feedback_opportunities": ("vacancy_key",),
    "vacancy_observability": ("run_id", "url"),
    "vacancy_rejection_summary": ("run_id",),
    "vacancy_scoring_shadow": ("run_id",),
    "semantic_shadow_evaluation": ("run_id",),
}
ID_TABLES_WITH_UNIQUE: dict[str, tuple[str, ...]] = {
    "vacancy_feedback_state": ("user_id", "feedback_type"),
}
_VACANCY_MERGE_COLUMNS = (
    "company",
    "title",
    "location",
    "description",
    "posted_at",
    "scraped_at",
    "salary",
    "company_url",
    "first_seen_at",
    "last_seen_at",
    "text_backfill_state",
    "text_backfill_at",
)
_BACKFILL_STATE_RANK = {"unavailable": 1, "failed": 2, "ok": 3}


@dataclass
class ReconcileReport:
    scanned: int = 0
    already_canonical: int = 0
    rekeyed: int = 0
    merged: int = 0
    child_rows_moved: int = 0
    # Vacancy-level count: one refusal per vacancy that could not be moved.
    collisions_refused: int = 0
    # Detail count: how many child rows caused those vacancy-level refusals.
    collision_child_rows_refused: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "scanned": self.scanned,
            "already_canonical": self.already_canonical,
            "rekeyed": self.rekeyed,
            "merged": self.merged,
            "child_rows_moved": self.child_rows_moved,
            "collisions_refused": self.collisions_refused,
            "collision_child_rows_refused": self.collision_child_rows_refused,
        }


def _record_collision_refusal(report: ReconcileReport, child_rows: int) -> None:
    report.collisions_refused += 1
    report.collision_child_rows_refused += int(child_rows)


def _vacancy_key(row: sqlite3.Row) -> str:
    vacancy = Vacancy(
        source=str(row["source"] or ""),
        source_id=str(row["source_id"] or ""),
        company=str(row["company"] or "Unknown"),
        title=str(row["title"] or "Vacancy"),
        location=str(row["location"] or "Unknown"),
        url=str(row["url"] or ""),
        description=str(row["description"] or ""),
    )
    return canonical_vacancy_key(vacancy)


def _tables_with_column(conn: sqlite3.Connection, column: str) -> list[str]:
    tables = []
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ):
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({name})")}
        if column in columns:
            tables.append(str(name))
    return tables


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _key_collision_count(conn: sqlite3.Connection, old_key: str, new_key: str) -> int:
    count = 0
    for table in _tables_with_column(conn, "vacancy_key"):
        if table == "vacancies":
            continue
        unique_columns = KEY_TABLE_UNIQUES.get(table)
        if not unique_columns:
            continue
        rows = conn.execute(
            f"SELECT {', '.join(unique_columns)} FROM {table} WHERE vacancy_key = ?",
            (old_key,),
        ).fetchall()
        for row in rows:
            predicates = " AND ".join(f"{column} IS ?" for column in unique_columns)
            values = [row[column] for column in unique_columns]
            if conn.execute(
                f"SELECT 1 FROM {table} WHERE vacancy_key = ? AND {predicates} LIMIT 1",
                [new_key, *values],
            ).fetchone():
                count += 1
    return count


def _id_collision_count(conn: sqlite3.Connection, loser_id: int, survivor_id: int) -> int:
    count = 0
    for table in _tables_with_column(conn, "vacancy_id"):
        if table == "vacancies":
            continue
        unique_columns = ID_TABLES_WITH_UNIQUE.get(table)
        if not unique_columns:
            continue
        rows = conn.execute(
            f"SELECT {', '.join(unique_columns)} FROM {table} WHERE vacancy_id = ?",
            (loser_id,),
        ).fetchall()
        for row in rows:
            predicates = " AND ".join(f"{column} IS ?" for column in unique_columns)
            values = [row[column] for column in unique_columns]
            if conn.execute(
                f"SELECT 1 FROM {table} WHERE vacancy_id = ? AND {predicates} LIMIT 1",
                [survivor_id, *values],
            ).fetchone():
                count += 1
    return count


def _move_key_rows(
    conn: sqlite3.Connection,
    old_key: str,
    new_key: str,
    report: ReconcileReport,
) -> None:
    for table in _tables_with_column(conn, "vacancy_key"):
        if table in {"vacancies"}:
            continue
        cur = conn.execute(
            f"UPDATE {table} SET vacancy_key = ? WHERE vacancy_key = ?",
            (new_key, old_key),
        )
        report.child_rows_moved += int(cur.rowcount or 0)

    if "duplicate_links" in _table_names(conn):
        conn.execute(
            "UPDATE duplicate_links SET canonical_vacancy_key = ? WHERE canonical_vacancy_key = ?",
            (new_key, old_key),
        )
        conn.execute(
            "UPDATE duplicate_links SET duplicate_vacancy_key = ? WHERE duplicate_vacancy_key = ?",
            (new_key, old_key),
        )
        conn.execute(
            "DELETE FROM duplicate_links WHERE canonical_vacancy_key = duplicate_vacancy_key"
        )


def _move_id_rows(
    conn: sqlite3.Connection,
    loser_id: int,
    survivor_id: int,
    report: ReconcileReport,
) -> None:
    for table in _tables_with_column(conn, "vacancy_id"):
        if table == "vacancies":
            continue
        cur = conn.execute(
            f"UPDATE {table} SET vacancy_id = ? WHERE vacancy_id = ?",
            (survivor_id, loser_id),
        )
        report.child_rows_moved += int(cur.rowcount or 0)


def _merge_vacancy_rows(
    conn: sqlite3.Connection,
    loser: sqlite3.Row,
    survivor: sqlite3.Row,
    report: ReconcileReport,
) -> bool:
    loser_id = int(loser["id"])
    survivor_id = int(survivor["id"])
    collisions = _key_collision_count(
        conn, str(loser["vacancy_key"]), str(survivor["vacancy_key"])
    ) + _id_collision_count(conn, loser_id, survivor_id)
    if collisions:
        _record_collision_refusal(report, collisions)
        return False

    _move_key_rows(conn, str(loser["vacancy_key"]), str(survivor["vacancy_key"]), report)
    _move_id_rows(conn, loser_id, survivor_id, report)

    columns = set(loser.keys()) & set(survivor.keys())
    updates: dict[str, object] = {}
    for column in _VACANCY_MERGE_COLUMNS:
        if column not in columns or column in {"text_backfill_state", "text_backfill_at"}:
            continue
        if not str(survivor[column] or "").strip() and str(loser[column] or "").strip():
            updates[column] = loser[column]

    if "text_backfill_state" in columns:
        survivor_state = str(survivor["text_backfill_state"] or "").strip().lower()
        loser_state = str(loser["text_backfill_state"] or "").strip().lower()
        replace_state = False
        if survivor_state == "unavailable" and not loser_state:
            # An unattempted row is still eligible; do not retain a terminal
            # verdict just because it was on the surviving duplicate.
            replace_state = True
        elif loser_state == "unavailable" and survivor_state in {"", "failed"}:
            # Never turn an eligible row into a terminal one.
            replace_state = False
        elif _BACKFILL_STATE_RANK.get(loser_state, 0) > _BACKFILL_STATE_RANK.get(survivor_state, 0):
            replace_state = True
        elif not survivor_state and loser_state:
            replace_state = True

        if replace_state:
            updates["text_backfill_state"] = loser["text_backfill_state"]
            if "text_backfill_at" in columns:
                updates["text_backfill_at"] = loser["text_backfill_at"]

    if "metadata_json" in columns:
        try:
            survivor_metadata = json.loads(survivor["metadata_json"] or "{}")
        except (TypeError, ValueError):
            survivor_metadata = {}
        try:
            loser_metadata = json.loads(loser["metadata_json"] or "{}")
        except (TypeError, ValueError):
            loser_metadata = {}
        if isinstance(survivor_metadata, dict) and isinstance(loser_metadata, dict):
            merged_metadata = dict(loser_metadata)
            merged_metadata.update(survivor_metadata)
            if merged_metadata != survivor_metadata:
                updates["metadata_json"] = json.dumps(merged_metadata, ensure_ascii=False)

    if updates:
        assignments = ", ".join(f"{column} = ?" for column in updates)
        conn.execute(
            f"UPDATE vacancies SET {assignments} WHERE id = ?",
            [*updates.values(), survivor_id],
        )
    conn.execute("DELETE FROM vacancies WHERE id = ?", (loser_id,))
    report.merged += 1
    return True


def _reconcile_connection_mutating(conn: sqlite3.Connection) -> ReconcileReport:
    report = ReconcileReport()
    rows = conn.execute(
        "SELECT * FROM vacancies WHERE lower(source) = 'headhunter' ORDER BY id"
    ).fetchall()
    report.scanned = len(rows)
    for row in rows:
        current = str(row["vacancy_key"])
        desired = _vacancy_key(row)
        if current == desired:
            report.already_canonical += 1
            continue
        survivor = conn.execute(
            "SELECT * FROM vacancies WHERE vacancy_key = ?",
            (desired,),
        ).fetchone()
        if survivor is not None and int(survivor["id"]) != int(row["id"]):
            _merge_vacancy_rows(conn, row, survivor, report)
            continue
        collisions = _key_collision_count(conn, current, desired)
        if collisions:
            _record_collision_refusal(report, collisions)
            continue
        _move_key_rows(conn, current, desired, report)
        conn.execute(
            "UPDATE vacancies SET vacancy_key = ? WHERE id = ?",
            (desired, int(row["id"])),
        )
        report.rekeyed += 1
    return report


def reconcile_connection(conn: sqlite3.Connection, *, apply: bool) -> ReconcileReport:
    conn.row_factory = sqlite3.Row
    if apply:
        owns_transaction = not conn.in_transaction
        if owns_transaction:
            conn.execute("BEGIN")
        conn.execute("PRAGMA defer_foreign_keys=ON")
        try:
            report = _reconcile_connection_mutating(conn)
            if owns_transaction:
                conn.commit()
            return report
        except Exception:
            if owns_transaction:
                conn.rollback()
            raise
    conn.execute("SAVEPOINT reconcile_hh_keys_dry_run")
    try:
        conn.execute("PRAGMA defer_foreign_keys=ON")
        report = _reconcile_connection_mutating(conn)
    finally:
        conn.execute("ROLLBACK TO reconcile_hh_keys_dry_run")
        conn.execute("RELEASE reconcile_hh_keys_dry_run")
    return report


def reconcile_database(path: str | Path, *, apply: bool = False) -> ReconcileReport:
    temp_dir = None
    if apply:
        connection = sqlite3.connect(str(path), isolation_level=None)
    else:
        source_uri = f"file:{quote(str(Path(path).expanduser().resolve()))}?mode=ro"
        source = sqlite3.connect(source_uri, uri=True)
        temp_dir = tempfile.TemporaryDirectory(prefix="job-intel-reconcile-")
        snapshot_path = Path(temp_dir.name) / "snapshot.sqlite3"
        connection = sqlite3.connect(str(snapshot_path), isolation_level=None)
        try:
            source.backup(connection)
        finally:
            source.close()
        connection.close()
        connection = sqlite3.connect(str(snapshot_path), isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        report = reconcile_connection(connection, apply=apply)
        if apply:
            connection.commit()
        else:
            connection.rollback()
        return report
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
        if temp_dir is not None:
            temp_dir.cleanup()


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="persist changes; without this flag the command is dry-run",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = reconcile_database(args.db, apply=args.apply)
    print(json.dumps(report.as_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
