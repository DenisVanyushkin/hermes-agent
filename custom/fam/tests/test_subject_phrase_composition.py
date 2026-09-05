import pytest


@pytest.mark.skip(
    reason="phrase-to-FAM Docker harness unavailable: docker compose run --rm --no-deps gateway fam --help could not build ghcr.io/astral-sh/uv (network reset)"
)
def test_phrase_to_fam_subject_composition():
    """Runnable agent path: «запиши Тае математику» -> fam cal add --for-person."""
    # Kept as an explicit named acceptance test until the S7 harness can run.
    raise AssertionError("the runnable agent harness is required for this test")
