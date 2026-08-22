"""Versioned structural-finding fingerprints and receipt helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any

SCHEMA = "upstream-sync-invariant-receipt/v1"
RULE_VERSION = "definition-state-policy/v1"


def finding_key(finding: dict[str, Any]) -> str:
    return ":".join([str(finding.get("kind") or ""), str(finding.get("path") or ""), str(finding.get("symbol") or "")])


def fingerprint(*, path: str, kind: str, symbol: str | None, policy: str, base: dict[str, str], ours: dict[str, str], theirs: dict[str, str], result: dict[str, str]) -> dict[str, Any]:
    payload = {
        "schema": SCHEMA, "rule_version": RULE_VERSION, "path": path,
        "kind": kind, "symbol": symbol, "policy": policy,
        "base": base, "ours": ours, "theirs": theirs, "result": result,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    return {"id": f"INV-{digest[:12]}", "sha256": digest, "payload": payload}


def receipt_matches(receipt: dict[str, Any], finding: dict[str, Any]) -> bool:
    current = finding.get("fingerprint") or {}
    return receipt.get("finding_id") == finding.get("finding_id") and receipt.get("fingerprint_sha256") == current.get("sha256")
