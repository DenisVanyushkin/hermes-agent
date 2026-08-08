"""Shared text-length thresholds for 'does this row have usable text'.

FULL_MIN and PARTIAL_MIN answer the same question in two callers that must
not drift apart: the semantic replay layer's corpus classification
(job_intel.vacancy_understanding.semantic.runtime.replay_full) and
production's text-backfill selection (job_intel.text_backfill). They live
here, outside vacancy_understanding, so that production code can depend on
them without crossing the import boundary that
tests/job_intel/test_vacancy_understanding_model.py::test_no_production_import
protects — that test forbids production job_intel modules from importing
job_intel.vacancy_understanding.
"""
from __future__ import annotations

FULL_MIN, PARTIAL_MIN = 600, 200
