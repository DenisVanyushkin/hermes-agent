"""Shadow Evaluator Decision SoT package (Step 3A — contract only).

Contains the machine-readable decision contract and its structural validator.
There is NO runtime evaluator here: implementing evaluation before the
Decision SoT is approved is prohibited (see the human SoT). Production
job_intel modules must not import this package (test-enforced), and this
package imports neither the preference model nor vacancy understanding at
runtime.
"""
from job_intel.shadow_evaluator.contract import (  # noqa: F401
    CONTRACT_PATH,
    SCHEMA_PATH,
    DecisionContract,
    export_json_schema,
    load_contract,
    write_json_schema,
)
