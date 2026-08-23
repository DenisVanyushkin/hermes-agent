#!/usr/bin/env python3
"""Generate the deterministic Gate B fragment classifier artifact."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml

from job_intel.product_search.gate_b_benchmark_policy_v3 import (
    load_gate_b_benchmark_policy_v3,
)
from job_intel.product_search.gate_b_evidence_runner_v1 import (
    load_gate_b_corpus_rows,
)
from job_intel.product_search.gate_b_evidence_v3 import (
    generate_reviewed_fragment_allowlist_v3,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--gate-a-root", type=Path, required=True)
    parser.add_argument("--run-manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--corpus-sha256", required=True)
    parser.add_argument("--classified-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rows = load_gate_b_corpus_rows(
        package_root=args.package_root,
        gate_a_root=args.gate_a_root,
        run_manifest_path=args.run_manifest,
        expected_sha256=args.manifest_sha256,
        expected_corpus_sha256=args.corpus_sha256,
    )
    allowlist = generate_reviewed_fragment_allowlist_v3(
        tuple((row.record, row.raw) for row in rows),
        corpus_sha256=args.corpus_sha256,
        gate_a_run_id="gate-a-20260816T141344Z",
        classified_at=datetime.fromisoformat(args.classified_at.replace("Z", "+00:00")),
        policy=load_gate_b_benchmark_policy_v3(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        yaml.safe_dump(
            allowlist.model_dump(mode="json"),
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
