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


# --- round 2: the three defects found on real DEV output ---------------------

def test_segmentation_splits_concatenated_corpus_text():
    """Stored corpus text is cleaned and often lacks terminal punctuation, so
    one 'sentence' became a blob mixing salary, company blurb and duties."""
    text = ("Own the payments roadmap end to end "
            "About Datadog: Datadog is a global company "
            "• Lead the pricing strategy for the region")
    sents = responsibility_sentences(text)
    assert any("Own the payments roadmap" in s for s in sents)
    # the About-blurb must not be glued onto the duty sentence
    assert not any("About Datadog" in s and "Own the payments roadmap" in s
                   for s in sents)
    assert any("Lead the pricing strategy" in s for s in sents)


def test_company_and_customer_subjects_are_rejected():
    """The round-1 top candidate for revenue_proximity was marketing prose
    about the company's CUSTOMERS, not a duty of the role."""
    text = ("The median Ramp customer saves 5% and grows revenue 16% in their "
            "first year. Our platform drives revenue for thousands of merchants. "
            "You will own the revenue plan for the business line.")
    sents = responsibility_sentences(text)
    assert not any("median Ramp customer" in s for s in sents)
    assert not any("platform drives revenue" in s for s in sents)
    assert any("own the revenue plan" in s.lower() for s in sents)


def test_mining_scope_is_limited_to_target_roles():
    """The eligible corpus is mostly sales/support/engineering; their duty
    language would dominate frequency ranking and is irrelevant here."""
    from job_intel.vacancy_understanding.semantic.runtime.mandate_mining import (
        is_target_role,
    )
    assert is_target_role("Head of Product, Payments")
    assert is_target_role("VP Product")
    assert is_target_role("Director of Product Management")
    assert not is_target_role("Account Executive")
    assert not is_target_role("Senior Software Engineer")
    assert not is_target_role("Customer Success Manager")


def test_mine_candidates_skips_non_target_roles():
    rows = [
        {"vacancy_key": _dev_key("t"), "title": "Head of Product",
         "text": "You will own the pricing strategy for the platform.",
         "company": "A", "location": "R", "source": "s"},
        {"vacancy_key": _dev_key("n"), "title": "Account Executive",
         "text": "You will own the pricing strategy for the platform.",
         "company": "A", "location": "R", "source": "s"},
    ]
    out = mine_candidates(rows)
    total = sum(c["count"] for cands in out.values() for c in cands)
    only_target = mine_candidates([rows[0]])
    total_target = sum(c["count"] for cands in only_target.values() for c in cands)
    assert total == total_target, "non-target role must contribute nothing"
