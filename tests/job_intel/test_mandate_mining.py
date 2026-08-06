"""§7.2 T3 — mining real responsibility phrasings from the DEV slice. Offline."""
from __future__ import annotations

import pytest

from job_intel.vacancy_understanding.semantic.runtime.mandate_mining import (
    FACT_SEEDS,
    HoldoutAccessError,
    mine_candidates,
    responsibility_sentences,
)


def _dev_key(seed):
    """Pick a key that lands in DEV — the miner refuses holdout rows by design."""
    from job_intel.vacancy_understanding.semantic.runtime.mandate_coverage import (
        assign_split,
    )
    i = 0
    while True:
        k = f"{seed}-{i}"
        if assign_split(k) == "dev":
            return k
        i += 1


def _row(key, text):
    return {"vacancy_key": _dev_key(key), "title": "Head of Product", "text": text,
            "company": "Acme", "location": "Remote", "source": "ashby"}


def test_responsibility_sentences_keeps_only_duty_language():
    text = ("We are a fast growing company with great snacks. "
            "You will own the P&L for the payments business line. "
            "Our office is in Berlin.")
    sents = responsibility_sentences(text)
    assert any("own the P&L" in s for s in sents)
    assert not any("snacks" in s for s in sents)
    assert not any("office is in Berlin" in s for s in sents)


def test_mining_is_deterministic():
    rows = [_row("v1", "You will own the P&L for the business line."),
            _row("v2", "You will own the P&L for the business line.")]
    a = mine_candidates(rows)
    b = mine_candidates(rows)
    assert a == b


def test_mining_finds_a_planted_phrasing_and_ranks_by_frequency():
    common = "You will define the product strategy for the region. "
    rare = "You will chair the board review. "
    rows = [_row(f"v{i}", common) for i in range(5)]
    rows.append(_row("vr", rare))
    out = mine_candidates(rows)
    strat = out.get("mandate.strategy_ownership") or []
    assert strat, "strategy phrasings must be mined"
    assert strat[0]["count"] >= 5
    assert "strategy" in strat[0]["phrase"].lower()


def test_mining_refuses_holdout_rows():
    """Mining must never see holdout text — that is the whole point of the
    split. A holdout row reaching the miner is a hard error, not a warning."""
    from job_intel.vacancy_understanding.semantic.runtime.mandate_coverage import (
        assign_split,
    )
    holdout_key = next(f"v{i}" for i in range(10000)
                       if assign_split(f"v{i}") == "holdout")
    # built inline, NOT via _row(), which would remap the key into DEV
    row = {"vacancy_key": holdout_key, "title": "Head of Product",
           "text": "You will own the P&L.", "company": "Acme",
           "location": "Remote", "source": "ashby"}
    with pytest.raises(HoldoutAccessError):
        mine_candidates([row])


def test_every_target_fact_has_seeds():
    from job_intel.vacancy_understanding.semantic.runtime.mandate_coverage import (
        MANDATE_FACTS,
    )
    for fid in MANDATE_FACTS:
        assert FACT_SEEDS.get(fid), f"no mining seeds for {fid}"
