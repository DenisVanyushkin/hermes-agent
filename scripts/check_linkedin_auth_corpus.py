#!/usr/bin/env python3
"""Check LinkedIn session classification against saved host diagnostics."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def _diagnostic_rows(root: Path) -> list[tuple[str, Path, dict[str, object]]]:
    rows: list[tuple[str, Path, dict[str, object]]] = []
    for json_path in sorted(root.glob("202609*-cell-*-page-*.json")):
        data = json.loads(json_path.read_text(encoding="utf-8"))
        profile = str(data.get("requested_profile") or "")
        if not str(data.get("html_ref") or "").strip():
            continue
        if not str(data.get("page_url") or "").strip():
            continue
        if "linkedin-public-v1" in profile:
            group = "public"
        elif "/profiles/linkedin" in profile:
            group = "authenticated"
        else:
            continue
        rows.append((group, json_path, data))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--diagnostics-root",
        type=Path,
        default=Path("/var/lib/job-intel/state/browser-diagnostics"),
    )
    parser.add_argument(
        "--challenge-fixture",
        type=Path,
        default=Path(__file__).parents[1]
        / "tests/job_intel/fixtures/linkedin-rendered-challenge.html",
    )
    args = parser.parse_args(argv)

    if not args.diagnostics_root.exists():
        print(f"SKIP: diagnostics root is absent: {args.diagnostics_root}")
        return 0
    rows = _diagnostic_rows(args.diagnostics_root)
    if not rows:
        print("SKIP: no dated LinkedIn cell diagnostics were found")
        return 0

    from job_intel.browser_sourcing import (
        classify_linkedin_page,
        classify_linkedin_public_search_session,
    )
    from job_intel.linkedin_session import SESSION_MISSING, SESSION_OK

    counts: Counter[tuple[str, str, str]] = Counter()
    failures: list[str] = []
    for group, json_path, data in rows:
        html_ref = str(data.get("html_ref") or "")
        html_path = json_path.parent / html_ref
        if not html_path.is_file():
            failures.append(f"missing HTML for {json_path}")
            continue
        url = str(data.get("page_url") or "")
        html = html_path.read_text(encoding="utf-8", errors="replace")
        page_class = classify_linkedin_page(final_url=url, html=html)
        verdict = classify_linkedin_public_search_session(url=url, html=html)
        counts[(group, page_class, verdict)] += 1
        if group == "public" and page_class in {
            "usable_result_surface",
            "terminal_empty_surface",
        }:
            if verdict != SESSION_MISSING:
                failures.append(
                    f"public {json_path.name}: {page_class} -> {verdict}"
                )
        elif group == "authenticated" and verdict != SESSION_OK:
            failures.append(f"authenticated {json_path.name}: {verdict}")

    if args.challenge_fixture.exists():
        challenge_url = "https://www.linkedin.com/jobs/search/?keywords=product"
        challenge_html = args.challenge_fixture.read_text(
            encoding="utf-8", errors="replace"
        )
        challenge_verdict = classify_linkedin_public_search_session(
            url=challenge_url, html=challenge_html
        )
        print(f"challenge_fixture_verdict={challenge_verdict}")
        if challenge_verdict in {SESSION_MISSING, "unknown_public_search_session"}:
            failures.append(f"challenge fixture was not fail-closed: {challenge_verdict}")
    else:
        failures.append(f"missing challenge fixture: {args.challenge_fixture}")

    for (group, page_class, verdict), count in sorted(counts.items()):
        print(
            f"{group} page_class={page_class} verdict={verdict} count={count}"
        )
    print(f"diagnostic_rows={len(rows)}")
    print(f"failures={len(failures)}")
    if failures:
        for failure in failures[:20]:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
