"""Shadow Preference Evaluator (Step 3 — shadow/offline only).

Contains the decision-contract validator (Step 3A) and the runtime engine — a
faithful executable translation of the approved Decision SoT. Production
job_intel modules must NOT import this package (test-enforced); the engine
consumes only the canonical Step 1/2 models and never writes anywhere.
"""
from job_intel.shadow_evaluator.contract import (  # noqa: F401
    CONTRACT_PATH,
    SCHEMA_PATH,
    DecisionContract,
    export_json_schema,
    load_contract,
    write_json_schema,
)
from job_intel.shadow_evaluator.engine import evaluate  # noqa: F401
from job_intel.shadow_evaluator.models import (  # noqa: F401
    EvaluationError,
    ShadowEvaluation,
)
from job_intel.shadow_evaluator.policy import (  # noqa: F401
    EVALUATOR_VERSION,
    load_policy,
)
