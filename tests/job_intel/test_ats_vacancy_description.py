"""An absent description must stay absent, not become the title."""
from job_intel.ats_sources import _vacancy


def test_missing_description_stays_empty():
    v = _vacancy("smartrecruiters", url="https://x/1", title="Head of Product",
                 company="Acme")
    assert v.description == ""


def test_present_description_is_kept():
    v = _vacancy("greenhouse", url="https://x/2", title="Head of Product",
                 company="Acme", description="  You will own the P&L.  ")
    assert v.description == "You will own the P&L."


def test_whitespace_only_description_is_empty_not_title():
    v = _vacancy("teamtailor", url="https://x/3", title="Product Lead",
                 company="Acme", description="   \n  ")
    assert v.description == ""
