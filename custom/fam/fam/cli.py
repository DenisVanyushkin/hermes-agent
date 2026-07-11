"""fam CLI router. Subcommands register via build_parser()."""
import argparse, json, sys
from fam import db as famdb

def cmd_init(args):
    conn = famdb.connect()
    famdb.init_db(conn)
    out = {"ok": True, "db": famdb.resolve_db_path()}
    print(json.dumps(out, ensure_ascii=False) if args.json else f"initialized {out['db']}")
    return 0

def build_parser():
    p = argparse.ArgumentParser(prog="fam")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("init"); sp.set_defaults(func=cmd_init)
    return p

def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except famdb.sqlite3.Error as e:
        print(f"db error: {e}", file=sys.stderr); return 2
