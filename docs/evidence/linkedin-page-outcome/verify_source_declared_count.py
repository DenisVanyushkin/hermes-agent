"""Verify the source-declared count law over the frozen corpora. Fail-closed.

The first version of this script printed its findings and exited zero whatever
it found, skipped a missing corpus in silence, and never checked the capped
`1,000+` captures at all -- while the note beside it claimed those were
covered. A verdict command that cannot fail is not a verdict, so every
assertion below exits non-zero on violation and the corpus itself is pinned by
hash rather than discovered by glob.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import sys

EXPECTED = {
    "gate-a-20260829": (
        pathlib.Path(
            "/home/hermes/.hermes/job_intel/experiments/gate-a"
            "/0740165165a517fad40ea9ac443da094e178d06a/logs"
        ),
        96,
    ),
    "d4-20260903": (
        pathlib.Path(
            "/home/hermes/.hermes/job_intel/experiments/gate-a"
            "/ce070a9e0f85bdfa2bcd1444bfe8e53cfaaffce1/logs"
        ),
        24,
    ),
}
# Aggregate over sorted (name, sha256) of every capture. One line pins the
# whole corpus: a changed, added or removed capture moves it.
CORPUS_SHA256 = "3cc479812ec782b7f71f7209574d0ef06dd6f5b4f0c12f6e6072643d7589f9a7"
OBSERVED_OUTPUT_CAP = 60
# The only captures allowed to carry no count. Both are genuine empty result
# surfaces; naming them keeps "absent" from becoming a silent skip.
EXPECTED_COUNTLESS = 2

COUNT = re.compile(r'results-context-header__job-count">([^<]*)')
QUERY = re.compile(r'results-context-header__query-search">([^<]*)')
URN = re.compile(r"urn:li:jobPosting:(\d+)")

failures: list[str] = []


def fail(message: str) -> None:
    failures.append(message)


def captures() -> list[tuple[str, pathlib.Path]]:
    found: list[tuple[str, pathlib.Path]] = []
    for label, (root, expected) in EXPECTED.items():
        if not root.is_dir():
            fail(f"corpus missing: {label} at {root}")
            continue
        files = sorted(f for f in root.glob("*.html") if "auth-validate" not in f.name)
        if len(files) != expected:
            fail(f"corpus {label}: expected {expected} captures, found {len(files)}")
        found.extend((label, f) for f in files)
    return found


def corpus_digest(items: list[tuple[str, pathlib.Path]]) -> str:
    material = "\n".join(
        f"{label}/{path.name}\t{hashlib.sha256(path.read_bytes()).hexdigest()}"
        for label, path in sorted(items, key=lambda pair: (pair[0], pair[1].name))
    )
    return hashlib.sha256(material.encode()).hexdigest()


def parse_count(raw: str | None) -> tuple[str, int | None]:
    if raw is None:
        return ("absent", None)
    text = raw.replace(",", "").replace(" ", "").strip()
    if text.endswith("+"):
        head = text[:-1]
        return ("lower_bound", int(head)) if head.isdigit() else ("unparseable", None)
    return ("exact", int(text)) if text.isdigit() else ("unparseable", None)


def main() -> int:
    items = captures()
    if failures:
        for line in failures:
            print(f"FAIL {line}", file=sys.stderr)
        return 1

    digest = corpus_digest(items)
    if CORPUS_SHA256 != "__PINNED__" and digest != CORPUS_SHA256:
        fail(f"corpus digest {digest} != pinned {CORPUS_SHA256}")

    kinds = {"exact": 0, "lower_bound": 0, "absent": 0, "unparseable": 0}
    exact_match = exact_gap = 0
    ambiguous: list[str] = []

    for label, path in items:
        html = path.read_text(errors="replace")
        counts = COUNT.findall(html)
        queries = QUERY.findall(html)
        if len(counts) > 1:
            fail(f"{label}/{path.name}: {len(counts)} count selectors, expected at most 1")
        if len(queries) > 1:
            fail(f"{label}/{path.name}: {len(queries)} query selectors, expected at most 1")

        raw = counts[0].strip() if counts else None
        kind, value = parse_count(raw)
        kinds[kind] += 1
        # The identity unit is named on purpose: unique job URNs over the
        # serialised snapshot. It is a snapshot surrogate, not the product's
        # collected result identities, and the note says so.
        urns = len(set(URN.findall(html)))

        if kind == "unparseable":
            fail(f"{label}/{path.name}: count {raw!r} did not parse")
        elif kind == "absent":
            if urns != 0 or queries:
                fail(
                    f"{label}/{path.name}: no count, but {urns} urns and "
                    f"{len(queries)} query headers -- not an empty surface"
                )
        elif kind == "exact":
            if not queries:
                fail(f"{label}/{path.name}: exact count without a query header")
            if value == OBSERVED_OUTPUT_CAP:
                ambiguous.append(f"{label}/{path.name}")
            elif value < OBSERVED_OUTPUT_CAP:
                if urns == value:
                    exact_match += 1
                else:
                    fail(f"{label}/{path.name}: exact {value} below cap but {urns} urns")
            else:
                if urns == OBSERVED_OUTPUT_CAP:
                    exact_gap += 1
                else:
                    fail(f"{label}/{path.name}: exact {value} above cap but {urns} urns")
        elif kind == "lower_bound":
            # Never checked by the first version of this script, while the note
            # claimed it was.
            if urns != OBSERVED_OUTPUT_CAP:
                fail(f"{label}/{path.name}: lower bound {raw} but {urns} urns")

    if kinds["absent"] != EXPECTED_COUNTLESS:
        fail(f"captures without a count: {kinds['absent']}, expected {EXPECTED_COUNTLESS}")
    if ambiguous:
        fail(f"exact count equal to the cap is unhandled: {ambiguous}")

    print(
        f"captures={len(items)} exact={kinds['exact']} lower_bound={kinds['lower_bound']} "
        f"absent={kinds['absent']} exact_match={exact_match} exact_gap={exact_gap}"
    )
    print(f"corpus_sha256={digest}")

    if failures:
        for line in failures:
            print(f"FAIL {line}", file=sys.stderr)
        return 1
    print("OK collected == min(declared, cap) holds for every capture")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
