#!/usr/bin/env python3
"""The one place the upstream-sync decision policy lives.

Operator decision of 2026-08-15, after 39 of 40 recorded answers were
``merge-both``: non-security paths are merged both ways without asking;
paths matching the security regex are ALWAYS asked — the 2026-07-13 invariant
that memory never auto-applies a security path stands, and ``partition``
enforces it (a remembered security feature still comes back as ``new``). Everything that turns "a conflict" into
"a decision or a question" goes through :func:`decide_features`, so the cron
script, ``prepare --auto-policy`` and the finalizer cannot drift apart.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from upstream_sync_decisions import (  # noqa: E402
    SECURITY_RE,
    Feature,
    partition,
)

DEFAULT_DECISION = "merge-both"
SOURCE_POLICY = "policy"
SOURCE_MEMORY = "memory"
ASK = "ask"


def is_security_path(path: str) -> bool:
    return bool(SECURITY_RE.search(path or ""))


def decide_features(features: list[Feature], memory: dict) -> list[dict]:
    """Return one dict per feature: files, local_subjects, decision, source, status.

    Order is preserved. ``decision`` is ``None`` and ``status`` is
    ``awaiting_decision`` for the ones the operator must answer.
    """
    part = partition(features, memory)
    remembered = {f.fingerprint: f for f in part["remembered"]}
    out: list[dict] = []
    for f in features:
        rem = remembered.get(f.fingerprint)
        if any(is_security_path(p) for p in f.files):
            # partition() never remembers a security path; keep the invariant
            # explicit here too so a change there cannot silently widen this.
            out.append({"files": list(f.files), "local_subjects": list(f.subjects),
                        "decision": None, "source": None, "status": "awaiting_decision",
                        "why": "security-sensitive path — operator decides"})
        elif rem is not None:
            out.append(_decided(f, rem.decision, SOURCE_MEMORY))
        else:
            out.append(_decided(f, DEFAULT_DECISION, SOURCE_POLICY))
    return out


def decide_paths(paths: list[str], memory: dict, subjects_by_path: dict | None = None) -> list[dict]:
    """Same policy for bare paths (e.g. conflicts that appeared after the gate).

    Each path becomes its own feature; ``subjects_by_path`` feeds the memory
    fingerprint when known.
    """
    from upstream_sync_decisions import feature_fingerprint

    subjects_by_path = subjects_by_path or {}
    feats = []
    for p in paths:
        subs = tuple(sorted(set(subjects_by_path.get(p, []))))
        feats.append(Feature(files=(p,), subjects=subs, fingerprint=feature_fingerprint((p,), subs)))
    return decide_features(feats, memory)


def _decided(f: Feature, decision: str, source: str) -> dict:
    why = ("remembered operator decision" if source == SOURCE_MEMORY
           else "policy: merge-both by default for non-security paths")
    return {"files": list(f.files), "local_subjects": list(f.subjects),
            "decision": decision, "source": source, "status": "decided", "why": why}


def number_features(decided: list[dict], start: int = 1) -> list[dict]:
    """Assign F<n> ids in order — the ids the operator replies with."""
    out = []
    for i, d in enumerate(decided, start=start):
        item = dict(d)
        item["id"] = f"F{i}"
        out.append(item)
    return out


def needs_operator(decided: list[dict]) -> bool:
    return any(d.get("status") == "awaiting_decision" for d in decided)
