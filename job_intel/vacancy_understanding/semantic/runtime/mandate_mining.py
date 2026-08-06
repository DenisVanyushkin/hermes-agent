"""§7.2 T3 — mine real responsibility phrasings from the DEV corpus slice.

The existing rules were written from the contract's own control phrases,
which is why they match controls and not reality. This miner does the
opposite: it reads how live vacancies ACTUALLY phrase duties, so rules can be
written against real language.

Hard constraint: DEV only. A holdout row reaching the miner raises — the
split is worthless if the mining stage can peek at the acceptance set.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from job_intel.vacancy_understanding.semantic.runtime.mandate_coverage import (
    assign_split,
)


class HoldoutAccessError(RuntimeError):
    """Raised when mining is handed a row belonging to the holdout slice."""


# Verbs that mark a DUTY of the role, as opposed to company description.
# This is the same distinction the owner enforced when rejecting
# company-boilerplate evidence during the 5B-5 review.
_DUTY = (
    r"own(?:ing|s)?|lead(?:ing|s)?|drive(?:s|n)?|driving|build(?:ing|s)?|"
    r"define(?:s|d)?|defining|manage(?:s|d)?|managing|deliver(?:s|ing)?|"
    r"responsible for|accountable for|report(?:ing|s)? to|oversee(?:s|ing)?|"
    r"set(?:ting|s)? the|shape(?:s|ing)?|scale(?:s|ing)?|launch(?:es|ing)?|"
    r"grow(?:s|ing)?|establish(?:es|ing)?|partner(?:s|ing)? with"
)
_DUTY_RE = re.compile(rf"\b(?:{_DUTY})\b", re.I)

# A duty verb alone is NOT enough: "we are a fast growing company" contains
# "growing" but describes the COMPANY, not the role. That is the exact
# mandate-from-company-evidence error the owner rejected in review, so the
# miner must not ingest such sentences in the first place.
# Round-1 defect: the top revenue_proximity candidate was marketing prose
# about the company's CUSTOMERS ("The median Ramp customer ... grows revenue
# 16%"). A duty sentence's subject must be the candidate, not the company,
# its customers, or its product.
_NON_CANDIDATE_SUBJECT = re.compile(
    r"^\s*(?:the\s+)?(?:median\s+|average\s+|typical\s+)?"
    r"(?:[A-Z][\w&.-]*\s+)?"
    r"(?:customer|client|merchant|user|partner|company|team|platform|product|"
    r"business|organisation|organization|firm)s?\b"
    r"(?!\s+(?:you|we)\b)", re.I)
_COMPANY_DESC = re.compile(
    r"^\s*(?:we(?:'re| are)\b|our\b|the company\b|founded\b|headquarter|"
    r"[a-z]+ is (?:a|the) (?:leading|global|fast)\b)", re.I)
# The verb must be addressed to the candidate: second person, imperative at
# sentence start, an infinitive, or an explicit responsibility phrase.
_DUTY_CONTEXT = re.compile(
    rf"(?:^\s*|\byou(?:'ll| will)?\s+(?:\w+\s+){{0,2}}|\bto\s+|\band\s+)"
    rf"(?:{_DUTY})\b|\b(?:responsible|accountable) for\b", re.I)

# Seed vocabulary per target fact. Seeds locate candidate sentences; they are
# NOT the rules themselves — the mined phrasing is what a rule gets written
# against.
FACT_SEEDS: dict[str, tuple[str, ...]] = {
    "mandate.scope_breadth": ("business line", "portfolio", "product area",
                              "end to end", "end-to-end", "entire", "across the"),
    "mandate.revenue_proximity": ("revenue", "p&l", "monetisation", "monetization",
                                  "arr", "bookings", "top line", "commercial"),
    "mandate.expansion_mandate": ("new market", "expansion", "expand into",
                                  "launch in", "go-to-market", "new geograph"),
    "mandate.monetization_core": ("monetisation", "monetization", "packaging",
                                  "business model"),
    "mandate.pricing_core": ("pricing", "price", "rate card"),
    "mandate.acquiring_core": ("acquiring", "merchant", "payment acceptance",
                               "card acceptance"),
    "mandate.strategy_ownership": ("strategy", "strategic direction", "vision",
                                   "roadmap", "long-term direction"),
    "mandate.org_design_mandate": ("org design", "organisational", "organizational",
                                   "structure of the team", "reorganis", "reorganiz"),
    "mandate.team_build_mandate": ("hire", "hiring", "build the team", "grow the team",
                                   "recruit", "team of", "manage a team"),
    "mandate.executive_exposure": ("executive team", "leadership team", "c-level",
                                   "exco", "senior leadership", "cxo"),
    "mandate.board_exposure": ("board", "board of directors", "investors"),
    "mandate.pnl_ownership": ("p&l", "profit and loss", "budget ownership",
                              "own the budget"),
    "mandate.growth_mandate": ("growth", "acquisition", "activation", "retention",
                               "conversion", "funnel"),
}

# Corpus text is cleaned and frequently concatenated without terminal
# punctuation, so splitting on [.!?;] alone yields blobs that mix salary
# lines, company blurbs and duties into one "sentence" (round-1 defect).
# Also break on list glyphs and on a lowercase->Capitalised-word boundary,
# which is where the scraper joined separate blocks.
_SENT_SPLIT = re.compile(
    r"(?<=[.!?;])\s+"
    r"|\n+"
    r"|\s*[•·▪●‣◦*]\s*"
    r"|(?<=[a-z,)])\s+(?=(?:About|What|Who|Why|Requirements|Responsibilities|"
    r"Qualifications|Benefits|The role|Your role)\b)"
    r"|(?<=[a-z]{3})\s+(?=[A-Z][a-z]+\s+(?:is|are|was|has|have)\b)")
_WS = re.compile(r"\s+")

# Mining scope only — NOT a decision rule. The eligible corpus is mostly
# sales/support/engineering vacancies whose duty language would dominate the
# frequency ranking and is irrelevant to executive product mandate.
_TARGET_TITLE = re.compile(
    r"\b(?:chief product|cpo|head of product|vp,? product|vice president,? product|"
    r"director,? product|product director|group product manager|"
    r"director of product|head of.{0,20}product|product lead|"
    r"senior director.{0,20}product|gm\b|general manager)\b", re.I)
_NON_TARGET_TITLE = re.compile(
    r"\b(?:account executive|sales|customer success|support engineer|"
    r"software engineer|data scientist|recruiter|designer|marketing manager)\b",
    re.I)


def is_target_role(title: str) -> bool:
    """Whether this vacancy belongs to the population §7.2 cares about."""
    t = (title or "").strip()
    if not t or _NON_TARGET_TITLE.search(t):
        return False
    return bool(_TARGET_TITLE.search(t))


def responsibility_sentences(text: str) -> list[str]:
    """Sentences that state a duty of the role. Company description, perks and
    location prose are dropped — mining them would reproduce exactly the
    mandate-from-company-evidence error the owner rejected."""
    out = []
    for raw in _SENT_SPLIT.split(text or ""):
        s = _WS.sub(" ", raw).strip()
        if not (25 <= len(s) <= 400):
            continue
        if _COMPANY_DESC.search(s) or _NON_CANDIDATE_SUBJECT.search(s):
            continue
        if _DUTY_CONTEXT.search(s):
            out.append(s)
    return out


def _normalise(sentence: str) -> str:
    """Collapse a sentence to a comparable phrasing skeleton so the same
    construction across different companies counts as one candidate."""
    s = sentence.lower()
    s = re.sub(r"\b\d[\d,.]*\b", "<num>", s)
    s = re.sub(r"[^a-z&<>+ ]", " ", s)
    return _WS.sub(" ", s).strip()


def mine_candidates(rows: list[dict[str, Any]], *, top_n: int = 25
                    ) -> dict[str, list[dict[str, Any]]]:
    """DEV-only. Returns fact_id -> ranked candidate phrasings with counts and
    a real example, ready for a human to turn into rules."""
    per_fact: dict[str, Counter] = {fid: Counter() for fid in FACT_SEEDS}
    examples: dict[str, dict[str, str]] = {fid: {} for fid in FACT_SEEDS}

    for row in rows:
        key = row.get("vacancy_key") or ""
        if assign_split(key) != "dev":
            raise HoldoutAccessError(
                f"mining refused: {key!r} belongs to the holdout slice")
        if not is_target_role(row.get("title") or ""):
            continue
        text = row.get("text") or row.get("description") or ""
        for sent in responsibility_sentences(text):
            low = sent.lower()
            for fid, seeds in FACT_SEEDS.items():
                if not any(seed in low for seed in seeds):
                    continue
                skeleton = _normalise(sent)
                # key the candidate on a short window around the duty verb so
                # long unique sentences still cluster
                m = _DUTY_RE.search(skeleton)
                start = max(0, (m.start() if m else 0) - 10)
                phrase = skeleton[start:start + 90].strip()
                if len(phrase) < 15:
                    continue
                per_fact[fid][phrase] += 1
                examples[fid].setdefault(phrase, sent)

    out: dict[str, list[dict[str, Any]]] = {}
    for fid, counter in per_fact.items():
        ranked = [
            {"phrase": p, "count": c, "example": examples[fid].get(p, "")}
            for p, c in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
        ]
        if ranked:
            out[fid] = ranked
    return out
