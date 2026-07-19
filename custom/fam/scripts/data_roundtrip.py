#!/usr/bin/env python3
"""Operator CLI for the xlsx data-seeding round-trip (Task 6).

Thin argparse wiring only -- every actual rule lives in fam.seed /
fam.seed_xlsx (export_rows, make_snapshot, diff, format_report,
apply_diff, verify_roundtrip) and fam.maint (backup_db). See
custom/fam/docs/seeding.md for the operator runbook.

Subcommands:
  export --out data.xlsx [--db PATH] [--snapshot-dir DIR]
  diff   --file data.xlsx --snapshot SNAP [--db PATH]
  apply  --file data.xlsx --snapshot SNAP [--yes] [--db PATH]
  verify --file data.xlsx [--db PATH]

Exit codes: 0 = ok/no conflicts (apply without --yes also 0, nothing
applied); 2 = conflicts (diff/apply) or bad input; 3 = apply succeeded
but the post-apply verify_roundtrip failed (data IS already written --
see seeding.md for what to do).
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fam import db as famdb, gate, maint, seed, seed_xlsx  # noqa: E402

DEFAULT_SNAPSHOT_DIR = Path(os.path.expanduser("~/.hermes/private/amina/seeding/"))


def _resolve_db(args):
    """--db arg first, env FAM_DB / host-sandbox fallback second (via
    famdb.resolve_db_path, which itself checks FAM_DB)."""
    return args.db if args.db else famdb.resolve_db_path()


def _connect_ro(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _load_snapshot(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def cmd_export(args):
    db_path = _resolve_db(args)
    conn = _connect_ro(db_path)
    try:
        rows = seed.export_rows(conn)
    finally:
        conn.close()

    snap = seed.make_snapshot(rows)
    snapshot_dir = Path(args.snapshot_dir) if args.snapshot_dir else DEFAULT_SNAPSHOT_DIR
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    name = f"export-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.json"
    snap_path = snapshot_dir / name
    snap_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")

    seed_xlsx.write_workbook(rows, args.out)

    print(f"exported: {args.out}")
    print(f"snapshot: {name}")
    return 0


def cmd_diff(args):
    db_path = _resolve_db(args)
    conn = famdb.connect(db_path)
    try:
        file_rows = seed_xlsx.read_workbook(args.file)
        snap = _load_snapshot(args.snapshot)
        d = seed.diff(conn, file_rows, snap)
    finally:
        conn.close()

    print(seed.format_report(d))
    return 2 if d.has_conflicts else 0


def _backup_both(cfg, db_path, now):
    targets = [db_path]
    state = cfg.get("state_db_path")
    if state and Path(state).exists():
        targets.append(state)
    for src in targets:
        dest = maint.backup_db(src, cfg["backup_dir"], cfg["backup_keep"], now=now)
        print(f"backup: {dest}")


def cmd_apply(args):
    db_path = _resolve_db(args)
    conn = famdb.connect(db_path)
    try:
        file_rows = seed_xlsx.read_workbook(args.file)
        snap = _load_snapshot(args.snapshot)
        d = seed.diff(conn, file_rows, snap)
        report = seed.format_report(d)

        if d.has_conflicts:
            print(report)
            return 2

        if not args.yes:
            print(report)
            return 0

        cfg = gate.load_config()
        now = datetime.now(timezone.utc)
        _backup_both(cfg, db_path, now)

        conn.isolation_level = None  # manual transaction control below
        conn.execute("BEGIN IMMEDIATE")
        try:
            seed.apply_diff(conn, d, now_utc=now)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

        print(report)
        # После COMMIT данные уже в БД: любая неожиданная ошибка самой
        # проверки -- это предупреждение и exit 3 (как несовпавший verify),
        # а не exit 2, чтобы оператор не принял применённый apply за
        # неприменённый.
        try:
            ok = seed.verify_roundtrip(conn, file_rows)
        except Exception as e:
            print("ПРЕДУПРЕЖДЕНИЕ: verify_roundtrip после apply упал с ошибкой "
                  f"({e}) -- изменения УЖЕ применены к БД, расхождение нужно "
                  "разобрать вручную (см. custom/fam/docs/seeding.md).",
                  file=sys.stderr)
            return 3
        if not ok:
            print("ПРЕДУПРЕЖДЕНИЕ: verify_roundtrip после apply не совпал -- "
                  "изменения УЖЕ применены к БД, расхождение нужно разобрать вручную "
                  "(см. custom/fam/docs/seeding.md).", file=sys.stderr)
            return 3

        print("applied ok")
        return 0
    finally:
        conn.close()


def cmd_verify(args):
    db_path = _resolve_db(args)
    conn = famdb.connect(db_path)
    try:
        file_rows = seed_xlsx.read_workbook(args.file)
        ok = seed.verify_roundtrip(conn, file_rows)
    finally:
        conn.close()

    print("ok" if ok else "MISMATCH")
    return 0 if ok else 3


def build_parser():
    p = argparse.ArgumentParser(prog="data_roundtrip", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    spe = sub.add_parser("export", help="live DB -> xlsx + snapshot")
    spe.add_argument("--out", required=True, help="path to write the .xlsx")
    spe.add_argument("--db", help="explicit DB path (overrides FAM_DB / auto-resolve)")
    spe.add_argument("--snapshot-dir", help=f"default {DEFAULT_SNAPSHOT_DIR}")
    spe.set_defaults(func=cmd_export)

    spd = sub.add_parser("diff", help="xlsx vs snapshot vs live DB -> report")
    spd.add_argument("--file", required=True, help="edited .xlsx")
    spd.add_argument("--snapshot", required=True, help="snapshot json from export")
    spd.add_argument("--db", help="explicit DB path (overrides FAM_DB / auto-resolve)")
    spd.set_defaults(func=cmd_diff)

    spa = sub.add_parser("apply", help="re-diff, backup, apply, verify")
    spa.add_argument("--file", required=True, help="edited .xlsx")
    spa.add_argument("--snapshot", required=True, help="snapshot json from export")
    spa.add_argument("--yes", action="store_true", help="required to actually apply")
    spa.add_argument("--db", help="explicit DB path (overrides FAM_DB / auto-resolve)")
    spa.set_defaults(func=cmd_apply)

    spv = sub.add_parser("verify", help="check xlsx matches live DB exactly")
    spv.add_argument("--file", required=True, help="xlsx to verify against live DB")
    spv.add_argument("--db", help="explicit DB path (overrides FAM_DB / auto-resolve)")
    spv.set_defaults(func=cmd_verify)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    except sqlite3.Error as e:
        print(f"db error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        # Любая другая неожиданная ошибка ДО коммита: cmd_apply уже сделал
        # ROLLBACK и перебросил её сюда -- ничего не записано, exit 2.
        # (Ошибки ПОСЛЕ коммита cmd_apply ловит сам и возвращает 3.)
        print(f"unexpected error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
