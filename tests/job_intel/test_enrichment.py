from job_intel.enrichment import detect_high_value_questions


def test_enrichment_returns_no_questions_when_known_gaps_are_filled() -> None:
    memory = {
        "preferred_company_stage": "scale-up",
        "travel_tolerance": "monthly",
        "preferred_compensation_mix": "balanced",
        "willingness_for_APAC_relocation": "no",
    }

    assert detect_high_value_questions(memory) == []
