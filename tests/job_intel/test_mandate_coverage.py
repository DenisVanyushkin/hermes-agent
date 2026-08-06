"""§7.2 T1+T2 — corpus split and the real-corpus coverage gate. Offline."""
from __future__ import annotations

from job_intel.vacancy_understanding.semantic.runtime.mandate_coverage import (
    MANDATE_FACTS,
    assign_split,
    coverage_report,
    split_corpus,
)

LONG = ("We are looking for a leader to own the P&L for our payments business "
        "line and build the team from scratch across EMEA. ") * 5


def _row(key, title="Head of Product", text=LONG):
    return {"vacancy_key": key, "title": title, "text": text,
            "company": "Acme", "location": "Remote", "source": "ashby"}


# --- T1: deterministic split -------------------------------------------------

def test_split_is_deterministic_for_the_same_key():
    assert assign_split("abc") == assign_split("abc")


def test_split_is_roughly_seventy_thirty():
    keys = [f"vacancy-{i}" for i in range(3000)]
    dev = sum(1 for k in keys if assign_split(k) == "dev")
    ratio = dev / len(keys)
    assert 0.66 <= ratio <= 0.74, ratio


def test_growing_the_corpus_never_reassigns_existing_keys():
    """The DB grows daily; an assignment that drifts would silently leak
    holdout rows into DEV between rounds."""
    before = {k: assign_split(k) for k in (f"v{i}" for i in range(50))}
    # simulate growth: many new keys appear
    for i in range(50, 5000):
        assign_split(f"v{i}")
    after = {k: assign_split(k) for k in before}
    assert before == after


def test_split_corpus_partitions_without_overlap():
    rows = [_row(f"v{i}") for i in range(200)]
    dev, holdout = split_corpus(rows)
    assert len(dev) + len(holdout) == 200
    dev_keys = {r["vacancy_key"] for r in dev}
    hold_keys = {r["vacancy_key"] for r in holdout}
    assert dev_keys.isdisjoint(hold_keys)
    assert holdout, "holdout must not be empty"


# --- T2: coverage metric (the gate) ------------------------------------------

def test_coverage_report_shape_and_counts():
    rows = [_row("v1"), _row("v2")]
    rep = coverage_report(rows)
    assert rep["roles_total"] == 2
    assert set(MANDATE_FACTS) <= set(rep["per_fact"])
    assert 0.0 <= rep["roles_with_any_mandate_rate"] <= 1.0
    # every per-fact entry reports an extracted count
    for fid, n in rep["per_fact"].items():
        assert isinstance(n, int) and n >= 0


def test_empty_corpus_is_not_applicable_not_zero():
    """A rate of 0.0 on an empty corpus would read as 'we extract nothing';
    the honest answer is 'no data'."""
    rep = coverage_report([])
    assert rep["roles_total"] == 0
    assert rep["roles_with_any_mandate_rate"] is None
    assert rep["state"] == "not_applicable"


def test_coverage_detects_an_extraction_improvement():
    """Sanity: a text carrying a phrase the provider DOES recognise scores
    higher than neutral filler — the metric must actually move."""
    recognised = _row("v1", text="Own user acquisition and activation across the region.")
    filler = _row("v2", text="We have great snacks and a modern office. " * 20)
    hi = coverage_report([recognised])["roles_with_any_mandate_rate"]
    lo = coverage_report([filler])["roles_with_any_mandate_rate"]
    assert hi > lo


def test_report_keeps_dev_and_holdout_separate():
    rows = [_row(f"v{i}") for i in range(100)]
    dev, holdout = split_corpus(rows)
    rep = coverage_report(dev, label="dev")
    assert rep["label"] == "dev"
    assert rep["roles_total"] == len(dev)
    assert rep["roles_total"] != len(rows)  # not silently the whole corpus
