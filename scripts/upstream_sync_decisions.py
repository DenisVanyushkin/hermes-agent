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
