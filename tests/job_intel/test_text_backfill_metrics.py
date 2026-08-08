"""filled must be visible, because it is the one number that says whether this
work is doing anything: it should fall as the backlog drains."""
from job_intel.cli import _merge_backfill_into_statuses
from job_intel.text_backfill import BackfillReport


def test_counters_are_attached_per_source():
    report = BackfillReport()
    report.per_source = {"smartrecruiters": {"attempted": 5, "filled": 4,
                                             "failed": 0, "unavailable": 1}}
    statuses = {"smartrecruiters": {"source": "smartrecruiters", "hits": 10},
                "greenhouse": {"source": "greenhouse", "hits": 3}}
    _merge_backfill_into_statuses(statuses, report)
    assert statuses["smartrecruiters"]["text_backfill"]["filled"] == 4
    assert "text_backfill" not in statuses["greenhouse"]


def test_a_source_absent_from_statuses_is_ignored():
    report = BackfillReport()
    report.per_source = {"teamtailor": {"attempted": 1, "filled": 1,
                                        "failed": 0, "unavailable": 0}}
    statuses = {}
    _merge_backfill_into_statuses(statuses, report)
    assert statuses == {}
