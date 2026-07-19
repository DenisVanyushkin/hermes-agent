"""Semantic extractor runtime (Step 4B) — implementation of the Step 4A SoT.

Shadow/offline only: no production integration, no writes, no messaging.
"""
from job_intel.vacancy_understanding.semantic.runtime.models import (  # noqa: F401
    RUNTIME_VERSION,
    Observation,
    SemanticExtraction,
)
from job_intel.vacancy_understanding.semantic.runtime.pipeline import extract_semantic  # noqa: F401
from job_intel.vacancy_understanding.semantic.runtime.provider import (  # noqa: F401
    DeterministicPhraseProvider,
    LLMProvider,
)
