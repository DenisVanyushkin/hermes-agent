"""A stated absence of sponsorship must not read as sponsorship.

Both texts below are the real wording from run 510, where each of these
roles was accepted because the sentence denying sponsorship contains the
word "sponsorship". A hard gate that opens on its own negation is the worst
failure available here, so these cases are pinned.
"""

from __future__ import annotations

import pytest

from job_intel.product_search.role_fit import evaluate_role_fit

SOFTWARE_TEXT = "We are a B2B SaaS payments platform. You will own the product roadmap and lead the team."

AFFIRM_WORDING = SOFTWARE_TEXT + " Please note that visa sponsorship is not available for this position."
ADYEN_WORDING = (
    SOFTWARE_TEXT
    + " You must be work authorized in the United States without the need for new visa sponsorship."
    + " The company can support visa transfers but will not sponsor individuals for H-1B CAP applications."
)


@pytest.mark.parametrize(
    "description",
    [
        AFFIRM_WORDING,
        ADYEN_WORDING,
        SOFTWARE_TEXT + " We do not offer visa sponsorship.",
        SOFTWARE_TEXT + " No sponsorship is provided for this role.",
        SOFTWARE_TEXT + " This role is not eligible for visa sponsorship or relocation support.",
        SOFTWARE_TEXT + " Candidates must have existing work authorization; we cannot sponsor visas.",
    ],
)
def test_denied_sponsorship_does_not_open_the_us_onsite_gate(description: str) -> None:
    decision = evaluate_role_fit(
        "VP Product", "Acme", "San Francisco, California, United States", description
    )
    assert decision.verdict == "reject"
    assert "us_onsite_without_sponsorship" in decision.rule_ids


@pytest.mark.parametrize(
    "description",
    [AFFIRM_WORDING, ADYEN_WORDING],
)
def test_denied_sponsorship_keeps_remote_us_blocked(description: str) -> None:
    decision = evaluate_role_fit("VP Product", "Acme", "Remote US", description)
    assert decision.verdict == "blocked"
    assert "us_remote_eligibility_unknown" in decision.rule_ids


@pytest.mark.parametrize(
    "description",
    [
        SOFTWARE_TEXT + " We sponsor work visas for this role, including H-1B transfers.",
        SOFTWARE_TEXT + " Visa sponsorship is available for exceptional candidates.",
        SOFTWARE_TEXT + " We offer relocation support and visa sponsorship.",
    ],
)
def test_offered_sponsorship_still_opens_the_gate(description: str) -> None:
    """The negation handling must not swallow a genuine offer."""
    decision = evaluate_role_fit(
        "VP Product", "Acme", "San Francisco, California, United States", description
    )
    assert "us_onsite_without_sponsorship" not in decision.rule_ids
    assert decision.verdict == "accept"
