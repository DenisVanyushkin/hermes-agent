"""Career preference model contract (Step 1 — not production-integrated).

Production job_intel modules must NOT import this package until a later,
explicitly approved rollout step; a contract test enforces that.
"""
from job_intel.preference_model.model import (  # noqa: F401
    DEFAULT_MODEL_PATH,
    SCHEMA_PATH,
    CareerPreferenceModel,
    Scenario,
    applicable_anti_preferences,
    applicable_positive_preferences,
    evaluate_feasibility,
    export_json_schema,
    load_model,
    role_level_vetoes,
    write_json_schema,
)
