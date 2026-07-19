"""Deterministic, read-only derivation of compatible-match equivalence
classes from the Decision SoT's 36-cell matrix (Step 5B, Slice 5B-1).

step5b-benchmark-contract.md §2.1 defines "compatible_match" as: two
different fact-level outputs that the Decision SoT matrix treats as
equivalent for the FINAL decision. The matrix (decision-contract.yaml
recommendation_matrix.feasible_matrix) is the only place that formal
equivalence lives — band criteria themselves are natural-language strings,
not a fact-value function, so this derivation operates at the one level the
matrix actually formalizes: (mandate_band, company_band) -> recommendation.

Two (mandate_band, company_band) pairs are "compatible" for benchmark
matching purposes iff the matrix maps them to the SAME recommendation. This
is a pure grouping of the 36 existing rows — it invents nothing, mutates
nothing, and changes no runtime behaviour (Roadmap SoT §9.6 / owner
decision on this task: read-only derivation, benchmark matching only).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from job_intel.shadow_evaluator.contract import CONTRACT_PATH, load_contract

from .hashing import sha256_file

DERIVATION_VERSION = "1.0.0"


def derive_recommendation_equivalences(
    source_path: Path | str | None = None,
) -> dict[str, Any]:
    """Group the matrix's 36 (mandate, company) rows by recommendation.

    Returns a stable, content-hashed artifact — never mutates the source
    file or any in-memory contract object.
    """
    path = Path(source_path) if source_path else CONTRACT_PATH
    contract = load_contract(path)

    rows = contract.recommendation_matrix.feasible_matrix
    classes: dict[str, list[list[str]]] = {}
    for row in rows:
        classes.setdefault(row.recommendation.value, []).append(
            [row.mandate.value, row.company.value])

    # Stable ordering: recommendations alphabetically, pairs as they appear
    # in the source (source order carries no semantics per the contract's
    # own precedence rules, but a fixed traversal order keeps the artifact
    # byte-stable across re-derivations of the same source).
    ordered_classes = {
        rec: sorted(pairs) for rec, pairs in sorted(classes.items())
    }

    return {
        "derivation_version": DERIVATION_VERSION,
        "source_path": str(path),
        "source_sha256": sha256_file(path),
        "decision_contract_version": contract.metadata.contract_version,
        "cell_count": len(rows),
        "equivalence_classes": ordered_classes,
    }
