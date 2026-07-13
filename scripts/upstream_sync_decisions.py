#!/usr/bin/env python3
"""Deterministic decision-memory helper for the upstream-sync skill.

Pure functions (no ambient clock, no network, no git) plus a thin CLI so the
upstream-sync SKILL can partition preflight conflicts against remembered
operator decisions and record new ones. See
docs/superpowers/specs/2026-07-13-upstream-sync-decision-memory-design.md.
"""
import argparse
import dataclasses
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

MEMORY_SCHEMA = "upstream-sync-decisions/v1"

# Single-sourced from scripts/preflight-local-customizations-update.sh — keep identical.
SECURITY_RE = re.compile(
    r"(security|auth|secret|pairing|allowlist|file-safety|control-plane|HMAC|hmac|insecure)",
    re.IGNORECASE,
)

VALID_DECISIONS = ("keep-local", "take-upstream", "merge-both")


@dataclass(frozen=True)
class Feature:
    files: tuple[str, ...]
    subjects: tuple[str, ...]
    fingerprint: str
    decision: str | None = None
    source: str | None = None


def feature_fingerprint(files: Iterable[str], subjects: Iterable[str]) -> str:
    files_part = "\n".join(sorted(set(files)))
    subj_part = "\n".join(sorted(set(subjects)))
    raw = files_part + "\x00" + subj_part
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _skeleton() -> dict:
    return {"schema": MEMORY_SCHEMA, "updated_at": None, "entries": []}


def load_memory(path) -> dict:
    p = Path(path)
    if not p.exists():
        return _skeleton()
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return _skeleton()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed decision memory at {p}: {exc}") from exc
    if data.get("schema") != MEMORY_SCHEMA:
        raise ValueError(f"unexpected memory schema: {data.get('schema')!r}")
    data.setdefault("entries", [])
    data.setdefault("updated_at", None)
    return data


def save_memory(path, memory: dict) -> None:
    Path(path).write_text(
        json.dumps(memory, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def group_features(conflicts: list[dict]) -> list[Feature]:
    buckets: dict[tuple[str, ...], dict] = {}
    for entry in conflicts:
        file = entry.get("file")
        if not file:
            continue
        subs = tuple(sorted({
            (c.get("subject") or "").strip()
            for c in entry.get("local_commits", [])
            if (c.get("subject") or "").strip()
        }))
        bucket = buckets.setdefault(subs, {"files": set(), "subjects": set(subs)})
        bucket["files"].add(file)
    features: list[Feature] = []
    for subs, bucket in buckets.items():
        files = tuple(sorted(bucket["files"]))
        subjects = tuple(sorted(bucket["subjects"]))
        features.append(Feature(
            files=files,
            subjects=subjects,
            fingerprint=feature_fingerprint(files, subjects),
        ))
    features.sort(key=lambda ft: ft.files)
    return features


def _best_match(feature: Feature, entries: list[dict]) -> dict | None:
    ft_subjects = set(feature.subjects)
    ft_files = set(feature.files)
    candidates: list[tuple[int, dict]] = []
    for entry in entries:
        if set(entry.get("local_subjects", [])) != ft_subjects:
            continue
        entry_files = set(entry.get("files", []))
        if not ft_files.issubset(entry_files):
            continue
        # All candidates that reach here are supersets of feature.files, so their
        # overlap score is constant (== len feature.files); the tie-break below
        # therefore considers every qualifying entry and defers to the operator
        # (returns None) whenever any of them disagree on the decision.
        candidates.append((len(ft_files & entry_files), entry))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    top_score = candidates[0][0]
    top = [entry for score, entry in candidates if score == top_score]
    if len({entry.get("decision") for entry in top}) > 1:
        return None  # ambiguous — ask the operator
    return top[0]


def partition(features: list[Feature], memory: dict, *, security_re=SECURITY_RE) -> dict:
    entries = memory.get("entries", [])
    remembered: list[Feature] = []
    new: list[Feature] = []
    for feature in features:
        if not feature.subjects:
            new.append(feature)
            continue
        if any(security_re.search(path) for path in feature.files):
            new.append(feature)
            continue
        match = _best_match(feature, entries)
        if match is None:
            new.append(feature)
        else:
            remembered.append(dataclasses.replace(
                feature, decision=match["decision"], source="memory"))
    return {"remembered": remembered, "new": new}


def record_decisions(memory: dict, decided_features: list[Feature], *, now: str) -> dict:
    for feature in decided_features:
        if feature.decision not in VALID_DECISIONS:
            raise ValueError(f"invalid decision {feature.decision!r} for {feature.fingerprint}")
    entries = memory.setdefault("entries", [])
    by_fingerprint = {entry["fingerprint"]: entry for entry in entries}
    for feature in decided_features:
        entry = by_fingerprint.get(feature.fingerprint)
        if entry is None:
            entry = {
                "fingerprint": feature.fingerprint,
                "files": list(feature.files),
                "local_subjects": list(feature.subjects),
                "decision": feature.decision,
                "created_at": now,
                "last_applied_at": now,
                "apply_count": 1,
            }
            entries.append(entry)
            by_fingerprint[feature.fingerprint] = entry
        else:
            entry["decision"] = feature.decision
            if entry.get("last_applied_at") != now:
                entry["last_applied_at"] = now
                entry["apply_count"] = int(entry.get("apply_count", 0)) + 1
    memory["schema"] = MEMORY_SCHEMA
    memory["updated_at"] = now
    return memory
