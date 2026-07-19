"""Semantic Vacancy Understanding SoT (Step 4A — contract only).

The canonical, implementation-independent policy for semantic extraction.
NO runtime extraction lives here (test-enforced: only contract.py). Any
conformant extractor (LLM, deterministic, hybrid) must emit the canonical
Step 2 record fragments + observations defined by this contract.
"""
from job_intel.vacancy_understanding.semantic.contract import (  # noqa: F401
    CONTRACT_PATH,
    SCHEMA_PATH,
    SemanticFactContract,
    export_json_schema,
    load_semantic_contract,
    write_json_schema,
)
