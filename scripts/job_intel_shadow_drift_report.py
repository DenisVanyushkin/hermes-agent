#!/usr/bin/env python3
"""Phase III B1 — shadow-vs-production drift report entrypoint.

Run at the 2-week checkpoint (or any time) to see how the observe-only
semantic shadow diverges from production, and — the decision-relevant part —
how each aligns with the owner's actual reactions. Reads only.

Usage: job_intel_shadow_drift_report.py [--lookback-days 14] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _resolve_repo() -> Path:
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    repo = Path(home) / "hermes-agent"
    if not (repo / "job_intel" / "__main__.py").exists():
        repo = Path(__file__).resolve().parents[1]
    return repo


def _fmt(report: dict) -> str:
    L = []
    L.append(f"# Shadow-vs-Prod Drift Report ({report['lookback_days']}d window)")
    L.append(f"vacancies compared: {report['vacancies_compared']}")
    L.append(f"coarse agreement rate: {report['coarse_agreement_rate']}")
    L.append("")
    L.append("shadow band dist: " + json.dumps(report["shadow_band_distribution"]))
    L.append("prod   band dist: " + json.dumps(report["prod_band_distribution"]))
    L.append("")
    ra = report["reaction_alignment"]
    L.append(f"reactions compared: {report['reactions_compared']}")
    L.append("  provider alignment vs YOUR reactions (higher aligned / lower "
             "false_positive+missed is better):")
    for prov in ("shadow", "prod"):
        L.append(f"    {prov:6}: {json.dumps(ra[prov])}")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback-days", type=int, default=14)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    repo = _resolve_repo()
    sys.path.insert(0, str(repo))
    from job_intel.runtime import resolve_db_path
    from job_intel.shadow_drift import build_drift_report
    from job_intel.store import JobIntelStore

    store = JobIntelStore(resolve_db_path())
    report = build_drift_report(store, lookback_days=args.lookback_days)
    print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else _fmt(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
