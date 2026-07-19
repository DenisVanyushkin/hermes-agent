"""Vacancy Understanding Layer (Step 2 — not production-integrated).

Describes what a vacancy means, candidate-independently. Production job_intel
modules must NOT import this package until an explicitly approved rollout
step; a contract test enforces that. This package must not import the career
preference model at runtime (also test-enforced).
"""
from job_intel.vacancy_understanding.model import (  # noqa: F401
    SCHEMA_PATH,
    SCHEMA_VERSION,
    VacancyUnderstanding,
    export_json_schema,
    load_understanding,
    write_json_schema,
)
from job_intel.vacancy_understanding.country_groups import (  # noqa: F401
    RESOLVER_VERSION,
    resolve_country_group,
)
