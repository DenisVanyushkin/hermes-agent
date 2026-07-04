from unittest.mock import MagicMock

from job_intel.universe.endpoints import probe_ats, apply_probe
from job_intel.universe.models import CandidateCompany


def _resp(status=200, json_data=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.text = text
    if json_data is not None:
        r.json = lambda: json_data
    else:
        r.json = MagicMock(side_effect=ValueError)
    return r


def test_probe_hits_greenhouse_first():
    session = MagicMock()
    session.get.return_value = _resp(200, json_data={"jobs": []})
    assert probe_ats("nium", session=session) == (
        "greenhouse", "https://boards-api.greenhouse.io/v1/boards/nium/jobs")


def test_probe_falls_through_to_none():
    session = MagicMock()
    session.get.return_value = _resp(404)
    assert probe_ats("ghost-co", session=session) is None


def test_probe_teamtailor_needs_marker():
    session = MagicMock()
    session.get.side_effect = lambda url, **kw: (
        _resp(200, text="powered by Teamtailor") if "teamtailor" in url else _resp(404))
    assert probe_ats("instabee", session=session) == (
        "teamtailor", "https://instabee.teamtailor.com/jobs")


def test_apply_probe_sets_reason():
    session = MagicMock()
    session.get.return_value = _resp(404)
    c = CandidateCompany(name="Ghost Co")
    apply_probe(c, session=session)
    assert "no_endpoint" in c.reasons and c.ats_type is None


def test_apply_probe_success_sets_supported_ats():
    session = MagicMock()
    session.get.return_value = _resp(200, json_data={"jobs": []})
    c = CandidateCompany(name="Nium")
    apply_probe(c, session=session)
    assert c.ats_type == "greenhouse" and "supported_ats" in c.reasons
