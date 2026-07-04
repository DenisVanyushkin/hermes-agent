"""Anchor policy for company-universe discovery (MVP-0).

Behavioral anchors come from confirmed positive Slack feedback; editorial
anchors are a human-owned bootstrap file (NOT final until the Wave 2 seed set
is approved); negative anchors suppress non-product roles as evidence.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_DATA = Path(__file__).parent / "data"

# Behavioral: confirmed positive Slack reactions (Wise Product Director APAC,
# Airwallex Head of Product GPN — recovered 2026-07-04).
BEHAVIORAL_ANCHORS: list[str] = ["wise", "airwallex"]

# Roles that must NOT count as senior-product evidence (negative anchors).
NEGATIVE_ROLE_BUCKETS: frozenset[str] = frozenset({"design", "marketing", "sales", "analyst"})
NEGATIVE_TITLE_RE = re.compile(
    r"product\s+design|product\s+marketing|\bPMM\b|designer|agency|recruit(er|ment)\s+(agency|partner)",
    re.IGNORECASE,
)

EXPLORATION_QUOTA = 0.30  # >=30% of reported candidates must be non-anchor-sourced


def load_editorial_anchors(path: Path | None = None) -> list[str]:
    p = path or (_DATA / "editorial_anchors.json")
    return [str(x).strip().lower() for x in json.loads(p.read_text())]


def load_anchor_similar(path: Path | None = None) -> dict[str, list[str]]:
    p = path or (_DATA / "anchor_similar.json")
    raw = json.loads(p.read_text())
    return {str(k).lower(): [str(v) for v in vals] for k, vals in raw.items()}
