from job_intel.enrichment import detect_high_value_questions


def test_enrichment_only_asks_for_missing_high_value_gaps() -> None:
    questions = detect_high_value_questions({"preferred_company_stage": "scale-up"})

    assert questions
    assert all("stage" not in q.lower() for q in questions if "stage" in q.lower())
