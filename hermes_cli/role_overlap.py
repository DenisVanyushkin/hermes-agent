"""Role package routing overlap and ambiguity validator (Slice 4).

Detects when a candidate role package's routing triggers collide with
built-in role trigger tables or would flip golden corpus routing decisions.

Severity:
  ERROR   — hard failure; install must be blocked
  WARNING — surfaced to operator; install may proceed
  INFO    — informational only

Finding codes:
  EXACT_DUPLICATE         — package trigger == built-in trigger (normalized)
  SUBSTRING_BUILTIN_IN_PKG — built-in trigger is substring of package trigger
  SUBSTRING_PKG_IN_BUILTIN — package trigger is substring of built-in trigger
  ROUTING_FLIP            — package trigger appears in golden corpus prompt
                            whose expected primary_profile is a built-in role
  BROAD_TRIGGER           — package trigger is very short / generic
  ROLE_FAMILY_OVERLAP     — role_family matches built-in domain vocabulary

Acknowledgement via role.routing.overlap_notes downgrades EXACT_DUPLICATE
and SUBSTRING findings from ERROR to WARNING.  ROUTING_FLIP is never
downgradeable — a flip of a golden-corpus built-in result is always an error.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEVERITY_ERROR = "ERROR"
SEVERITY_WARNING = "WARNING"
SEVERITY_INFO = "INFO"

CODE_EXACT_DUPLICATE = "EXACT_DUPLICATE"
CODE_SUBSTRING_BUILTIN_IN_PKG = "SUBSTRING_BUILTIN_IN_PKG"
CODE_SUBSTRING_PKG_IN_BUILTIN = "SUBSTRING_PKG_IN_BUILTIN"
CODE_ROUTING_FLIP = "ROUTING_FLIP"
CODE_BROAD_TRIGGER = "BROAD_TRIGGER"
CODE_ROLE_FAMILY_OVERLAP = "ROLE_FAMILY_OVERLAP"

# role_family values that overlap with built-in domain vocabulary → WARNING
_BUILTIN_FAMILY_VOCABULARY: frozenset[str] = frozenset({
    "engineering", "security", "career", "research", "operations",
    "infrastructure", "documentation", "scribe",
})

# Triggers at most this many chars get a BROAD_TRIGGER warning.
_BROAD_TRIGGER_MAX_CHARS = 4


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OverlapFinding:
    """One finding from the overlap/ambiguity validator."""

    severity: str                    # ERROR | WARNING | INFO
    code: str                        # finding code constant above
    message: str
    package_name: str
    trigger: str                     # normalized package trigger that caused the finding
    conflicting_role: str | None = None
    conflicting_trigger: str | None = None


# ---------------------------------------------------------------------------
# Normalization — mirrors profile_routing._normalize exactly
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    translated = text.lower().translate(
        str.maketrans({ch: " " for ch in string.punctuation})
    )
    return " ".join(translated.split())


# ---------------------------------------------------------------------------
# Built-in trigger table access
# ---------------------------------------------------------------------------


def _get_builtin_trigger_tables() -> dict[str, tuple[str, ...]]:
    """Return {role_id: (term, ...)} from profile_routing module-level constants."""
    from hermes_cli.profile_routing import (  # noqa: PLC0415
        _CAREER_TERMS,
        _DOCS_TERMS,
        _INFRA_TERMS,
        _RESEARCH_TERMS,
        _SECURITY_TERMS,
    )
    return {
        "security_auditor": _SECURITY_TERMS,
        "engineer": _INFRA_TERMS,
        "career_strategist": _CAREER_TERMS,
        "scribe": _DOCS_TERMS,
        "researcher": _RESEARCH_TERMS,
    }


def _default_corpus_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "tests"
        / "fixtures"
        / "role_packages"
        / "golden_routing_corpus.yaml"
    )


def _load_corpus(corpus_path: Path | None = None) -> list[dict[str, Any]]:
    path = corpus_path or _default_corpus_path()
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data.get("entries", []) if isinstance(data, dict) else []
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# Table-overlap helper — two-pass to guarantee exact match beats substring
# ---------------------------------------------------------------------------


def _check_one_trigger_against_role(
    norm_pkg: str,
    lang: str,
    builtin_role: str,
    norm_builtin_list: list[str],
    acknowledged: set[tuple[str, str]],
    package_name: str,
) -> OverlapFinding | None:
    """Return the highest-priority finding for one (package trigger, builtin role) pair.

    Priority: EXACT_DUPLICATE > SUBSTRING_BUILTIN_IN_PKG > SUBSTRING_PKG_IN_BUILTIN.
    A two-pass scan ensures that when a trigger (e.g. 'audit') matches both an
    exact entry ('audit') and a compound entry ('security audit') in the same
    table, EXACT_DUPLICATE is always reported rather than a substring code.
    """
    # Pass 1: exact match
    for norm_builtin in norm_builtin_list:
        if norm_pkg == norm_builtin:
            is_ack = ((builtin_role, norm_builtin) in acknowledged or
                      (builtin_role, norm_pkg) in acknowledged)
            sev = SEVERITY_WARNING if is_ack else SEVERITY_ERROR
            msg = (
                f"trigger {norm_pkg!r} (lang={lang}) is an exact duplicate of "
                f"built-in {builtin_role!r} trigger"
            )
            if is_ack:
                msg += " [acknowledged via overlap_notes]"
            return OverlapFinding(
                severity=sev,
                code=CODE_EXACT_DUPLICATE,
                message=msg,
                package_name=package_name,
                trigger=norm_pkg,
                conflicting_role=builtin_role,
                conflicting_trigger=norm_builtin,
            )

    # Pass 2: substring (first match wins; one finding per (pkg_trigger, builtin_role))
    for norm_builtin in norm_builtin_list:
        if not norm_builtin:
            continue
        if norm_builtin in norm_pkg:
            code = CODE_SUBSTRING_BUILTIN_IN_PKG
            msg = (
                f"built-in {builtin_role!r} trigger {norm_builtin!r} is a "
                f"substring of package trigger {norm_pkg!r} (lang={lang})"
            )
        elif norm_pkg in norm_builtin:
            code = CODE_SUBSTRING_PKG_IN_BUILTIN
            msg = (
                f"package trigger {norm_pkg!r} (lang={lang}) is a substring of "
                f"built-in {builtin_role!r} trigger {norm_builtin!r}"
            )
        else:
            continue

        is_ack = ((builtin_role, norm_builtin) in acknowledged or
                  (builtin_role, norm_pkg) in acknowledged)
        sev = SEVERITY_WARNING if is_ack else SEVERITY_ERROR
        if is_ack:
            msg += " [acknowledged via overlap_notes]"
        return OverlapFinding(
            severity=sev,
            code=code,
            message=msg,
            package_name=package_name,
            trigger=norm_pkg,
            conflicting_role=builtin_role,
            conflicting_trigger=norm_builtin,
        )

    return None


# ---------------------------------------------------------------------------
# Core validator
# ---------------------------------------------------------------------------


def validate_package_overlap(
    manifest: dict[str, Any],
    package_name: str,
    *,
    corpus_path: Path | None = None,
    check_corpus: bool = True,
) -> list[OverlapFinding]:
    """Validate a parsed manifest for routing overlap and ambiguity.

    Returns a list of OverlapFinding objects sorted by severity (ERROR first).
    Never raises — caller decides how to act based on finding severity.
    """
    findings: list[OverlapFinding] = []
    seen: set[tuple[str, str, str | None, str | None]] = set()

    def _add(f: OverlapFinding) -> None:
        key = (f.code, f.trigger, f.conflicting_role, f.conflicting_trigger)
        if key not in seen:
            seen.add(key)
            findings.append(f)

    role = manifest.get("role", {})
    routing = role.get("routing", {})
    triggers_by_lang: dict[str, list[Any]] = routing.get("triggers", {}) or {}
    overlap_notes: list[Any] = routing.get("overlap_notes", []) or []

    # Build acknowledged set: (conflicts_with_role, normalized_trigger) pairs.
    acknowledged: set[tuple[str, str]] = set()
    for note in overlap_notes:
        if isinstance(note, dict):
            conflicts_with = str(note.get("conflicts_with", ""))
            raw_trigger = str(note.get("trigger", ""))
            if conflicts_with and raw_trigger:
                acknowledged.add((conflicts_with, _normalize(raw_trigger)))

    # Role-family overlap warning is independent of whether triggers exist.
    role_family = str(role.get("role_family", "")).lower()
    if role_family and role_family in _BUILTIN_FAMILY_VOCABULARY:
        _add(OverlapFinding(
            severity=SEVERITY_WARNING,
            code=CODE_ROLE_FAMILY_OVERLAP,
            message=(
                f"role_family {role.get('role_family')!r} overlaps with built-in "
                "role family vocabulary; document responsibility boundaries clearly"
            ),
            package_name=package_name,
            trigger="",
        ))

    # Early return when no routing triggers are declared.
    if not triggers_by_lang:
        _sort(findings)
        return findings

    # Collect normalized (lang, trigger) pairs.
    pkg_triggers: list[tuple[str, str]] = []
    for lang, terms in triggers_by_lang.items():
        if isinstance(terms, list):
            for t in terms:
                norm = _normalize(str(t))
                if norm:
                    pkg_triggers.append((lang, norm))

    # Broad-trigger check.
    for lang, norm_pkg in pkg_triggers:
        words = norm_pkg.split()
        if len(norm_pkg) <= _BROAD_TRIGGER_MAX_CHARS or (
            len(words) == 1 and len(norm_pkg) <= _BROAD_TRIGGER_MAX_CHARS + 1
        ):
            _add(OverlapFinding(
                severity=SEVERITY_WARNING,
                code=CODE_BROAD_TRIGGER,
                message=(
                    f"trigger {norm_pkg!r} (lang={lang}) is very short and may match "
                    "unintended prompts; consider a more specific phrase"
                ),
                package_name=package_name,
                trigger=norm_pkg,
            ))

    # Table overlap check (two-pass per (trigger, builtin_role) pair).
    builtin_tables = _get_builtin_trigger_tables()
    normalized_builtins: dict[str, list[str]] = {
        role_id: [_normalize(t) for t in terms]
        for role_id, terms in builtin_tables.items()
    }

    for lang, norm_pkg in pkg_triggers:
        for builtin_role, norm_builtin_list in normalized_builtins.items():
            finding = _check_one_trigger_against_role(
                norm_pkg, lang, builtin_role, norm_builtin_list, acknowledged, package_name
            )
            if finding is not None:
                _add(finding)

    # Routing-flip simulation against golden corpus.
    if check_corpus:
        corpus = _load_corpus(corpus_path)
        flip_seen: set[tuple[str, str]] = set()
        for entry in corpus:
            prompt = str(entry.get("prompt", ""))
            norm_prompt = _normalize(prompt)
            expected_primary = str(entry.get("expected", {}).get("primary_profile", ""))
            if not expected_primary or expected_primary == "general_operator":
                continue
            for _lang, norm_pkg in pkg_triggers:
                if norm_pkg and norm_pkg in norm_prompt:
                    flip_key = (norm_pkg, expected_primary)
                    if flip_key not in flip_seen:
                        flip_seen.add(flip_key)
                        _add(OverlapFinding(
                            severity=SEVERITY_ERROR,
                            code=CODE_ROUTING_FLIP,
                            message=(
                                f"trigger {norm_pkg!r} matches golden corpus prompt "
                                f"(id={entry.get('id', '?')!r}) that routes to "
                                f"built-in {expected_primary!r}: {prompt!r}"
                            ),
                            package_name=package_name,
                            trigger=norm_pkg,
                            conflicting_role=expected_primary,
                        ))

    _sort(findings)
    return findings


def _sort(findings: list[OverlapFinding]) -> None:
    order = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}
    findings.sort(key=lambda f: order.get(f.severity, 9))


def has_errors(findings: list[OverlapFinding]) -> bool:
    """Return True if any finding has ERROR severity."""
    return any(f.severity == SEVERITY_ERROR for f in findings)


def format_findings(findings: list[OverlapFinding]) -> str:
    """Return a human-readable multiline string of all findings."""
    return "\n".join(f"  [{f.severity}] {f.code}: {f.message}" for f in findings)
