"""Semantic extractor runtime pipeline — stages 1-10 (Step 4B).

Faithful execution of the Step 4A contract. Facts are produced ONLY through
the observation -> mapping path; the mapping vocabulary is the contract's
fact inventory + Step 2 enums (signal convention ``<fact_leaf>=<value>``).
No candidate-fit policy, no candidate knowledge, no enrichment, no writes.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from job_intel.vacancy_understanding.model import (
    VacancyUnderstanding,
)
from job_intel.vacancy_understanding.semantic.contract import (
    ExtractionClass,
    SemanticFactContract,
    load_semantic_contract,
)
from job_intel.vacancy_understanding.semantic.runtime.models import (
    RUNTIME_VERSION,
    ClarificationOut,
    ConflictRecord,
    ExtractionDiagnosticsOut,
    FactProvenance,
    Observation,
    ObservationBasis,
    RejectedObservation,
    SemanticExtraction,
    SemanticProvider,
)

_BASIS_RANK = {ObservationBasis.weak: 0, ObservationBasis.direct: 1, ObservationBasis.explicit: 2}
_BASIS_CONF = {ObservationBasis.weak: "low", ObservationBasis.direct: "medium",
               ObservationBasis.explicit: "high"}
_SRC_SEM = "src_semantic_observations"

# Step 2 enum vocabularies per fact leaf (value validation for signals).
_ENUMS = {
    "scope_breadth": {"feature", "domain", "business_line", "region", "portfolio", "enterprise"},
    "revenue_proximity": {"support", "enabling", "indirect", "direct_revenue", "direct_pnl"},
    "transformation_phase": {"build", "scale", "turnaround", "expand", "optimize", "maintain"},
    "company.scale": {"local", "regional", "multi_region", "global"},
    "company.stage": {"seed", "growth", "scaleup", "public", "mature"},
    "company.customer_model": {"b2c", "smb_mass", "b2b_enterprise", "mixed"},
    "requirements.overall_transferability": {
        "transferable", "adjacent", "specialized_but_learnable", "non_transferable_barrier"},
}
_TRI = {"true", "false"}

_IMPOSSIBLE_PAIRS = [("mandate.platform_as_business", "mandate.platform_engineering")]


def _leaf(fact_id: str) -> str:
    return fact_id.split(".", 1)[1]


def _values_for(fact_id: str) -> set[str]:
    leaf = _leaf(fact_id)
    if fact_id.startswith(("company.", "requirements.", "organization.")):
        return _ENUMS.get(fact_id, _TRI)
    return _ENUMS.get(leaf, _TRI)


def extract_semantic(
    vu: VacancyUnderstanding,
    *,
    title: str,
    text: str,
    provider: SemanticProvider,
    contract: Optional[SemanticFactContract] = None,
) -> SemanticExtraction:
    contract = contract or load_semantic_contract()
    facts_by_id = {f.id: f for f in contract.facts}
    warnings: list[str] = []

    # ---- Stage 1: input validation ----------------------------------------
    if not vu.metadata.vacancy_key:
        raise ValueError("malformed input: missing vacancy_key")
    if not isinstance(title, str) or title.strip() == "":
        raise ValueError("malformed input: title required")
    text = text or ""

    # ---- Stage 2: observation extraction (provider emits ONLY observations)
    raw_obs = provider.extract_semantic_observations(
        title=title, text=text, structured={"title": title})

    # ---- Stage 3: observation validation ----------------------------------
    valid: list[Observation] = []
    rejected: list[RejectedObservation] = []
    seen_ids: set[str] = set()
    max_len = contract.observation_model.max_excerpt_len
    for o in raw_obs:
        reason = None
        hay = title if o.location == "title" else text
        if o.observation_id in seen_ids:
            reason = "duplicate_observation_id"
        elif o.location not in ("title", "description"):
            reason = "invalid_location"
        elif len(o.excerpt) > max_len:
            reason = "excerpt_too_long"
        elif o.excerpt not in hay:
            reason = "excerpt_not_verbatim"
        elif "=" not in o.signal_type:
            reason = "invalid_signal_type"
        else:
            leaf, value = o.signal_type.split("=", 1)
            fid = leaf if leaf.startswith(("company.", "requirements.", "organization.")) else f"mandate.{leaf}"
            if fid not in facts_by_id:
                reason = "unknown_fact_reference"
            elif facts_by_id[fid].extraction_class == ExtractionClass.enrichment_only:
                reason = "enrichment_only_fact_forbidden_for_semantic"
            elif value not in _values_for(fid):
                reason = "invalid_value_for_fact"
            elif any(m not in facts_by_id for m in o.maps_to):
                reason = "maps_to_unresolved"
        if reason:
            rejected.append(RejectedObservation(observation=o.model_dump(), reason=reason))
        else:
            seen_ids.add(o.observation_id)
            valid.append(o)

    # ---- Stage 4: normalization (merge duplicates, keep provenance) -------
    merged: dict[tuple[str, str, str], Observation] = {}
    obs_aliases: dict[str, list[str]] = defaultdict(list)
    for o in valid:
        key = (o.signal_type, o.excerpt, o.location)
        if key in merged:
            obs_aliases[merged[key].observation_id].append(o.observation_id)  # cf_duplicate
        else:
            merged[key] = o
    observations = list(merged.values())

    # ---- Stage 5: fact mapping (contract-driven, observations only) --------
    candidates: dict[str, list[tuple[str, Observation]]] = defaultdict(list)
    for o in observations:
        leaf, value = o.signal_type.split("=", 1)
        fid = leaf if leaf.startswith(("company.", "requirements.", "organization.")) else f"mandate.{leaf}"
        candidates[fid].append((value, o))

    # ---- Stage 6: conflict resolution (Step 4A rules, exactly) -------------
    conflicts: list[ConflictRecord] = []
    resolved: dict[str, tuple[str, list[Observation]]] = {}
    doc = vu.model_dump(mode="json")

    def det_value(fid: str):
        sect, leaf = fid.split(".", 1)
        node = doc.get(sect, {}).get(leaf)
        if not isinstance(node, dict):
            return None, None
        v = node.get("value")
        raw = v if not isinstance(v, list) else None
        if raw in (None, "unknown") or node.get("method") in (None, "none"):
            return None, None
        if node.get("method") in ("manual_gold_annotation",):  # gold is test truth
            return None, None
        return raw, node.get("method")

    for fid, cand in sorted(candidates.items()):
        if fid == "mandate.transformation_phase":
            phases = sorted({v for v, _ in cand})
            resolved[fid] = ("|".join(phases), [o for _, o in cand])
            continue
        values = {v for v, _ in cand}
        if len(values) > 1:
            ranks = {v: max(_BASIS_RANK[o.basis] for vv, o in cand if vv == v) for v in values}
            best = sorted(values, key=lambda v: -ranks[v])
            if ranks[best[0]] > ranks[best[1]]:
                # cf_evidence_level_conflict: stronger basis wins, loser retained
                winner = best[0]
                conflicts.append(ConflictRecord(
                    rule_id="cf_evidence_level_conflict", fact_id=fid,
                    detail=f"{winner!r} overrode {best[1:]!r} on evidence strength",
                    observation_ids=[o.observation_id for _, o in cand]))
                resolved[fid] = (winner, [o for v, o in cand if v == winner])
            else:
                conflicts.append(ConflictRecord(
                    rule_id="cf_contradictory_observations", fact_id=fid,
                    detail=f"incompatible values {sorted(values)} at equal evidence strength",
                    observation_ids=[o.observation_id for _, o in cand]))
                # fact stays unknown; risk added at fragment stage
            continue
        value = values.pop()
        dv, dmethod = det_value(fid)
        if dv is not None and str(dv) != value:
            conflicts.append(ConflictRecord(
                rule_id="cf_deterministic_vs_semantic", fact_id=fid,
                detail=f"deterministic {dv!r} wins over semantic {value!r} (never overwrite)",
                observation_ids=[o.observation_id for _, o in cand]))
            continue
        if dv is not None:
            continue  # already known deterministically with the same value
        resolved[fid] = (value, [o for _, o in cand])

    # cf_impossible_combination
    for a, b in _IMPOSSIBLE_PAIRS:
        if resolved.get(a, ("",))[0] == "true" and resolved.get(b, ("",))[0] == "true":
            ra = max(_BASIS_RANK[o.basis] for o in resolved[a][1])
            rb = max(_BASIS_RANK[o.basis] for o in resolved[b][1])
            loser = b if ra > rb else a if rb > ra else None
            obs_ids = [o.observation_id for o in resolved[a][1] + resolved[b][1]]
            if loser:
                conflicts.append(ConflictRecord(
                    rule_id="cf_impossible_combination", fact_id=loser,
                    detail="lower-evidence platform shape reset to unknown",
                    observation_ids=obs_ids))
                resolved.pop(loser)
            else:
                conflicts.append(ConflictRecord(
                    rule_id="cf_impossible_combination", fact_id=f"{a}+{b}",
                    detail="equal evidence: both platform shapes unknown",
                    observation_ids=obs_ids))
                resolved.pop(a), resolved.pop(b)

    # ---- Stage 7: confidence (evidence quality only) -----------------------
    # ---- Stage 8: unknown policy + clarifications --------------------------
    provenance: dict[str, FactProvenance] = {}
    registry_ids = {e["id"] for e in doc.get("evidence_registry", [])}
    if _SRC_SEM not in registry_ids:
        doc.setdefault("evidence_registry", []).append({
            "id": _SRC_SEM, "source_type": "semantic_inference",
            "description": "semantic observations (verbatim excerpts) of this extraction"})

    def fact_payload(value: str, obs: list[Observation], fid: str) -> dict:
        conf = _BASIS_CONF[max((o.basis for o in obs), key=lambda b: _BASIS_RANK[b])]
        evidence = [{
            "source_id": _SRC_SEM, "source_type": "semantic_inference",
            "excerpt": o.excerpt[:400], "location": o.location,
            "rationale": o.interpretation,
        } for o in obs[:3]]
        summary = (f"posting states {obs[0].excerpt[:80]!r} "
                   f"({', '.join(o.observation_id for o in obs[:3])}) -> {_leaf(fid)}={value}")
        provenance[fid] = FactProvenance(
            origin="semantic_inference", provider=provider.provider_id,
            prompt_version=provider.prompt_version,
            observation_ids=[o.observation_id for o in obs],
            reasoning_summary=summary, confidence=conf)
        return {"value": value, "confidence": conf,
                "method": "semantic_inference", "evidence": evidence}

    for fid, (value, obs) in sorted(resolved.items()):
        sect, leaf = fid.split(".", 1)
        if fid == "mandate.transformation_phase":
            doc[sect][leaf] = fact_payload(value, obs, fid) | {"value": value.split("|")}
            continue
        doc[sect][leaf] = fact_payload(value, obs, fid)

    # conflict risks + title/scope mismatch
    def add_risk(kind: str, note: str) -> None:
        risks = doc.setdefault("risks", [])
        if not any(r.get("kind") == kind and r.get("note") == note for r in risks):
            risks.append({"kind": kind, "note": note})

    for c in conflicts:
        if c.rule_id in ("cf_contradictory_observations", "cf_impossible_combination"):
            add_risk("internal_contradiction", f"{c.fact_id}: {c.detail}")
            involved_titles = [o for o in observations
                               if o.observation_id in c.observation_ids and o.location == "title"]
            if involved_titles:
                add_risk("title_scope_mismatch",
                         f"title signal conflicts with body evidence for {c.fact_id}")

    clarifications: list[ClarificationOut] = []
    for f in contract.facts:
        if f.extraction_class not in (ExtractionClass.semantic_only, ExtractionClass.hybrid):
            continue
        if f.id in resolved:
            continue
        if f.unknown.clarification_priority.value in ("blocking", "recommendation_changing"):
            clarifications.append(ClarificationOut(
                fact_id=f.id, priority=f.unknown.clarification_priority.value,
                question=f"Уточнить {f.id}: {f.unknown.unknown_required_when}"))

    # ---- Stage 9: Step 2 validation (terminal on failure) ------------------
    fragment = VacancyUnderstanding.model_validate(doc)

    # ---- Stage 10: deterministic serialization -----------------------------
    return SemanticExtraction(
        vacancy_key=vu.metadata.vacancy_key,
        fragment=fragment.model_dump(mode="json"),
        observations=observations,
        rejected_observations=rejected,
        provenance=provenance,
        conflicts=conflicts,
        clarifications=clarifications,
        diagnostics=ExtractionDiagnosticsOut(
            provider=provider.provider_id, prompt_version=provider.prompt_version,
            semantic_contract_version=contract.metadata.contract_version,
            runtime_version=RUNTIME_VERSION,
            observations_total=len(raw_obs), observations_rejected=len(rejected),
            facts_emitted=len(resolved),
            facts_unknown=sum(
                1 for f in contract.facts
                if f.extraction_class in (ExtractionClass.semantic_only, ExtractionClass.hybrid)
                and f.id not in resolved),
            warnings=warnings),
    )
