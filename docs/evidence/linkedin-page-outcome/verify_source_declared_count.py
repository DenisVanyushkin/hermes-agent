"""Verify the source-declared count law over the frozen corpora. Fail-closed.

Two earlier versions of this file could not fail where it mattered. The first
printed its findings and exited zero whatever it found, and never looked at the
capped captures at all. The second gained a pinned corpus and a non-zero exit
but still accepted a regression of the parse itself, a capped capture with no
query header, and an "empty" page recognised only by two absences.

Every check below therefore asserts an expected value rather than reporting an
observed one, and every branch that could pass by omission has a positive
requirement instead.
"""

from __future__ import annotations

import hashlib
import html as html_module
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
CORPUS_SHA256 = "3cc479812ec782b7f71f7209574d0ef06dd6f5b4f0c12f6e6072643d7589f9a7"
OBSERVED_OUTPUT_CAP = 60
# Asserted, not reported. A regression of the parse itself changes these
# numbers while every per-capture rule still passes, which is exactly how the
# previous version accepted `1,000+` being read as an exact count.
EXPECTED_STATS = {
    "captures": 120,
    "exact": 75,
    "lower_bound": 43,
    "absent": 2,
    "unparseable": 0,
    "exact_match": 39,
    "exact_gap": 36,
}

COUNT = re.compile(r'results-context-header__job-count">([^<]*)')
QUERY = re.compile(r'results-context-header__query-search">([^<]*)')
URN = re.compile(r"urn:li:jobPosting:(\d+)")
H1 = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.S | re.I)
# Positive evidence of an empty result surface. The rendered heading, not the
# `no-results` class: that class is present on non-empty authenticated pages
# too, so it distinguishes nothing on its own.
EMPTY_HEADING_PREFIX = "we couldn't find a match for"
EMPTY_MARKER_VERSION = "h1-normalised/2026-09-04"

failures: list[str] = []


def fail(message: str) -> None:
    failures.append(message)


def normalise(text: str) -> str:
    stripped = html_module.unescape(re.sub(r"<[^>]+>", " ", text))
    return " ".join(stripped.replace("’", "'").replace(" ", " ").split()).lower()


def has_empty_marker(html: str) -> bool:
    return any(
        normalise(m.group(1)).startswith(EMPTY_HEADING_PREFIX) for m in H1.finditer(html)
    )


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
    text = raw.replace(",", "").replace(" ", "").strip()
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
    if digest != CORPUS_SHA256:
        fail(f"corpus digest {digest} != pinned {CORPUS_SHA256}")

    stats = {key: 0 for key in EXPECTED_STATS}
    stats["captures"] = len(items)

    for label, path in items:
        where = f"{label}/{path.name}"
        html = path.read_text(errors="replace")
        counts = COUNT.findall(html)
        queries = QUERY.findall(html)
        if len(counts) > 1:
            fail(f"{where}: {len(counts)} count selectors, expected at most 1")
        raw = counts[0].strip() if counts else None
        kind, value = parse_count(raw)
        stats[kind] += 1
        urns = len(set(URN.findall(html)))
        empty = has_empty_marker(html)

        if kind == "absent":
            # Positive evidence, not two absences. A page with no count and no
            # ids could be anything; the rendered empty-state heading is what
            # says the source answered and answered with nothing.
            if not empty:
                fail(f"{where}: no count and no empty-state heading -- surface unidentified")
            if urns != 0:
                fail(f"{where}: empty surface carries {urns} job urns")
            if queries:
                fail(f"{where}: empty surface carries {len(queries)} query headers")
            continue

        # Any parsed count must be bound to exactly one rendered query header.
        # "At most one" let a capped capture lose its binding unnoticed.
        if len(queries) != 1:
            fail(f"{where}: {len(queries)} query selectors for a {kind} count, expected exactly 1")
        if empty:
            fail(f"{where}: empty-state heading on a capture that reports a count")

        if kind == "unparseable":
            fail(f"{where}: count {raw!r} did not parse")
        elif kind == "exact":
            if value == OBSERVED_OUTPUT_CAP:
                fail(f"{where}: exact count equals the cap; completeness and truncation "
                     "are indistinguishable and this case has no rule")
            elif value < OBSERVED_OUTPUT_CAP:
                if urns == value:
                    stats["exact_match"] += 1
                else:
                    fail(f"{where}: exact {value} below cap but {urns} snapshot urns")
            else:
                if urns == OBSERVED_OUTPUT_CAP:
                    stats["exact_gap"] += 1
                else:
                    fail(f"{where}: exact {value} above cap but {urns} snapshot urns")
        elif kind == "lower_bound":
            if urns != OBSERVED_OUTPUT_CAP:
                fail(f"{where}: lower bound {raw} but {urns} snapshot urns")

    for key, expected in EXPECTED_STATS.items():
        if stats[key] != expected:
            fail(f"stat {key}: expected {expected}, observed {stats[key]}")

    print(" ".join(f"{key}={stats[key]}" for key in EXPECTED_STATS))
    print(f"corpus_sha256={digest}")
    print(f"empty_marker={EMPTY_MARKER_VERSION}")

    if failures:
        for line in failures:
            print(f"FAIL {line}", file=sys.stderr)
        return 1
    # Two formulas, stated separately: one of them is censored at the source
    # and cannot be checked against a value nobody published.
    print("OK exact: snapshot_unique_job_urns == min(declared, cap)")
    print("OK lower_bound: snapshot_unique_job_urns == cap")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
