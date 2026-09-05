from __future__ import annotations

import argparse
from collections.abc import Mapping
from contextlib import ExitStack
import json
from pathlib import Path
import socket
import sys
from typing import Any
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = REPO_ROOT / "scripts/gate_b_readiness_corpus.v1.json"
EXPECTED_ROW_COUNT = 48

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from job_intel.product_search.gate_b_evidence_v3 import (  # noqa: E402
    load_company_evidence_catalog_v3,
    resolve_company_authority_v3,
)


class NetworkAttemptDetected(RuntimeError):
    """Raised when the offline preflight reaches a network boundary."""


class _OfflineNetworkGuard:
    def __init__(self) -> None:
        self.attempts = 0

    def _blocked(self, operation: str) -> None:
        self.attempts += 1
        raise NetworkAttemptDetected(
            f"offline_preflight_network_attempt:{operation}"
        )

    def installed(self) -> ExitStack:
        stack = ExitStack()

        def blocked_connect(sock: socket.socket, address: object) -> None:
            del sock, address
            self._blocked("socket.connect")

        def blocked_connect_ex(sock: socket.socket, address: object) -> int:
            del sock, address
            self._blocked("socket.connect_ex")
            return -1

        def blocked_create_connection(*args: object, **kwargs: object) -> object:
            del args, kwargs
            self._blocked("socket.create_connection")
            return None

        def blocked_getaddrinfo(*args: object, **kwargs: object) -> object:
            del args, kwargs
            self._blocked("socket.getaddrinfo")
            return None

        stack.enter_context(patch.object(socket.socket, "connect", blocked_connect))
        stack.enter_context(
            patch.object(socket.socket, "connect_ex", blocked_connect_ex)
        )
        stack.enter_context(patch.object(socket, "create_connection", blocked_create_connection))
        stack.enter_context(patch.object(socket, "getaddrinfo", blocked_getaddrinfo))
        return stack


def _load_rows(path: Path) -> list[Mapping[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"corpus_unavailable:{path}")
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"corpus_invalid:{path}") from exc
    if not isinstance(payload, list) or len(payload) != EXPECTED_ROW_COUNT:
        raise ValueError(f"corpus_must_contain_exactly_{EXPECTED_ROW_COUNT}_rows")
    rows: list[Mapping[str, Any]] = []
    for expected_ordinal, item in enumerate(payload):
        if not isinstance(item, Mapping):
            raise ValueError(f"corpus_row_invalid:{expected_ordinal}")
        if item.get("ordinal") != expected_ordinal:
            raise ValueError(f"corpus_row_order_invalid:{expected_ordinal}")
        raw = item.get("raw")
        if not isinstance(raw, Mapping):
            raise ValueError(f"corpus_row_raw_invalid:{expected_ordinal}")
        rows.append(item)
    return rows


def _failure(
    *, ordinal: int, raw: Mapping[str, Any], status: str, reason: str
) -> dict[str, str]:
    return {
        "ordinal": str(ordinal),
        "company": str(raw.get("company", "")),
        "title": str(raw.get("title", "")),
        "status": status,
        "reason": reason,
    }


def run_preflight(
    *,
    company_evidence_root: Path,
    company_evidence_contract_sha256: str,
) -> dict[str, Any]:
    """Resolve every readiness row against an explicitly supplied authority root."""
    guard = _OfflineNetworkGuard()
    rows: list[Mapping[str, Any]] = []
    catalog: Any = None
    failures: list[dict[str, str]] = []
    status_counts: dict[str, int] = {}
    current_item: Mapping[str, Any] | None = None
    try:
        with guard.installed():
            rows = _load_rows(DEFAULT_CORPUS)
            catalog = load_company_evidence_catalog_v3(
                company_evidence_root,
                company_evidence_contract_sha256=company_evidence_contract_sha256,
            )
            for item in rows:
                current_item = item
                ordinal = int(item["ordinal"])
                raw = item["raw"]
                try:
                    authority = resolve_company_authority_v3(raw, catalog)
                except NetworkAttemptDetected:
                    raise
                except Exception as exc:
                    status_counts["error"] = status_counts.get("error", 0) + 1
                    failures.append(
                        _failure(
                            ordinal=ordinal,
                            raw=raw,
                            status="error",
                            reason=f"{type(exc).__name__}:{exc}",
                        )
                    )
                    continue
                status = str(authority.status)
                status_counts[status] = status_counts.get(status, 0) + 1
                if status != "available":
                    reason = getattr(authority, "reason", "unknown")
                    failures.append(
                        _failure(
                            ordinal=ordinal,
                            raw=raw,
                            status=status,
                            reason=str(getattr(reason, "value", reason)),
                        )
                    )
    except NetworkAttemptDetected as exc:
        if current_item is not None:
            failures.append(
                _failure(
                    ordinal=int(current_item["ordinal"]),
                    raw=current_item["raw"],
                    status="network_blocked",
                    reason=str(exc),
                )
            )
        return _report(
            status="blocked",
            reason=str(exc),
            rows=rows,
            catalog=catalog,
            failures=failures,
            status_counts=status_counts,
            corpus_path=DEFAULT_CORPUS,
            company_evidence_root=company_evidence_root,
            company_evidence_contract_sha256=company_evidence_contract_sha256,
            guard=guard,
        )
    except Exception as exc:
        return _report(
            status="blocked",
            reason=f"{type(exc).__name__}:{exc}",
            rows=rows,
            catalog=catalog,
            failures=failures,
            status_counts=status_counts,
            corpus_path=DEFAULT_CORPUS,
            company_evidence_root=company_evidence_root,
            company_evidence_contract_sha256=company_evidence_contract_sha256,
            guard=guard,
        )

    available_count = status_counts.get("available", 0)
    return _report(
        status="ready" if available_count == len(rows) and not failures else "blocked",
        rows=rows,
        catalog=catalog,
        failures=failures,
        status_counts=status_counts,
        corpus_path=DEFAULT_CORPUS,
        company_evidence_root=company_evidence_root,
        company_evidence_contract_sha256=company_evidence_contract_sha256,
        guard=guard,
    )


def _report(
    *,
    status: str,
    rows: list[Mapping[str, Any]],
    catalog: Any,
    failures: list[dict[str, str]],
    status_counts: dict[str, int],
    corpus_path: Path,
    company_evidence_root: Path,
    company_evidence_contract_sha256: str,
    guard: _OfflineNetworkGuard,
    reason: str | None = None,
) -> dict[str, Any]:
    available_count = status_counts.get("available", 0)
    report: dict[str, Any] = {
        "status": status,
        "corpus": str(corpus_path.resolve()),
        "company_evidence_root": str(company_evidence_root.resolve()),
        "company_evidence_contract_sha256": company_evidence_contract_sha256,
        "row_count": len(rows),
        "available_count": available_count,
        "status_counts": dict(sorted(status_counts.items())),
        "bundle_count": 0 if catalog is None else len(catalog.bundles),
        "companies": []
        if catalog is None
        else sorted(bundle.company_identity.company_id for bundle in catalog.bundles),
        "failures": failures,
        "network_attempts": guard.attempts,
        "network_called": guard.attempts > 0,
    }
    if reason is not None:
        report["reason"] = reason
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline Gate B readiness company-authority preflight"
    )
    parser.add_argument("--company-evidence-root", type=Path, required=True)
    parser.add_argument("--company-evidence-contract-sha256", required=True)
    args = parser.parse_args()
    try:
        report = run_preflight(
            company_evidence_root=args.company_evidence_root,
            company_evidence_contract_sha256=args.company_evidence_contract_sha256,
        )
    except Exception as exc:
        report = {
            "status": "blocked",
            "reason": f"{type(exc).__name__}:{exc}",
        }
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
