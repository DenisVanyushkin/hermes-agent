"""Shadow Evaluator — immutable runtime policy loaded from the SoT artifacts.

Central place that loads and validates:
- the decision contract (matrix, caps, unknown policy, action mapping);
- the career preference model (Step 1);
and enforces supported input majors. Runtime code must consult the parsed
policy — never re-encode matrix/caps in ad-hoc conditionals.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from job_intel.preference_model.model import (
    CareerPreferenceModel,
    load_model as load_preference_model,
)
from job_intel.shadow_evaluator.contract import (
    Cap,
    DecisionContract,
    FitBand,
    Recommendation,
    UnknownPolicyEntry,
    load_contract,
)

EVALUATOR_VERSION = "0.1.0"

_REC_ORDER = ["not_recommended", "unclear", "promising", "strong", "exceptional"]


class UnsupportedInputError(Exception):
    """Unsupported schema major — error record, no verdict, no legacy fallback."""


def _major(version: str) -> int:
    return int(version.split(".")[0])


def _supported(range_spec: str, version: str) -> bool:
    return _major(version) == int(range_spec.split(".")[0])


@dataclass(frozen=True)
class RuntimePolicy:
    contract: DecisionContract
    preference_model: CareerPreferenceModel
    matrix: dict[tuple[str, str], str]
    caps: dict[str, Cap]
    unknown_policy: dict[str, UnknownPolicyEntry]

    # ---- guards ----

    def check_input_versions(self, pref_version: str, vu_schema_version: str) -> None:
        siv = self.contract.supported_input_versions
        if not _supported(siv.preference_model, pref_version):
            raise UnsupportedInputError(
                f"preference model {pref_version} unsupported (need {siv.preference_model})")
        if not _supported(siv.vacancy_understanding, vu_schema_version):
            raise UnsupportedInputError(
                f"vacancy understanding {vu_schema_version} unsupported "
                f"(need {siv.vacancy_understanding})")

    # ---- central matrix resolver ----

    def resolve_matrix(self, mandate: FitBand, company: FitBand) -> Recommendation:
        return Recommendation(self.matrix[(mandate.value, company.value)])

    # ---- central cap resolver (monotonic: caps may only lower) ----

    def apply_caps(self, recommendation: Recommendation,
                   cap_ids: list[str]) -> tuple[Recommendation, list[str]]:
        """Monotonic: caps may only lower. Every TRIGGERED cap is recorded in
        applied_caps (a ceiling in force stays visible in the trace and
        explanations even when the result already sits at or below it)."""
        result = recommendation.value
        applied: list[str] = []
        for cap_id in cap_ids:
            ceiling = self.caps[cap_id].ceiling.value
            if _REC_ORDER.index(result) > _REC_ORDER.index(ceiling):
                result = ceiling
            if cap_id not in applied:
                applied.append(cap_id)
        return Recommendation(result), applied

    def action_for(self, recommendation: Recommendation, confidence: str,
                   feasibility_uncertain: bool) -> str:
        entry = next(m for m in self.contract.action_vocabulary.mapping
                     if m.recommendation == recommendation)
        if recommendation == Recommendation.promising and (
            confidence == "low" or feasibility_uncertain
        ):
            return entry.low_confidence_or_uncertain_action or entry.action
        return entry.action


@lru_cache(maxsize=1)
def load_policy() -> RuntimePolicy:
    contract = load_contract()
    pref = load_preference_model()
    matrix = {(c.mandate.value, c.company.value): c.recommendation.value
              for c in contract.recommendation_matrix.feasible_matrix}
    return RuntimePolicy(
        contract=contract,
        preference_model=pref,
        matrix=matrix,
        caps={c.id: c for c in contract.caps},
        unknown_policy={u.id: u for u in contract.unknown_policy},
    )
