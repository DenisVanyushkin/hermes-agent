"""Task 2: ICS parser (`parse_ics`) + recurrence expansion (`expand`).

Fixtures here are SYNTHETIC -- there is no live probe of her actual iCloud
calendar yet (no app-specific password configured). They are written to
RFC 5545 plus what task-2-report.md documents as the Apple-format
assumptions this module makes (TZID=Asia/Almaty, floating times treated as
Asia/Almaty local, VALUE=DATE all-day, RECURRENCE-ID overrides carrying a
full property set). None of these fixtures touch the network -- `parse_ics`
and `expand` are pure functions over text/lists.

`expand()`'s return shape is `{"occurrences": [...], "errors": [...]}`
(review finding C1 fix -- see extcal.py's own docstring and
task-2-report.md's fix-round section for why). Every test below goes
through `occurrences`/`errors` explicitly rather than treating the return
value as a bare list, so a regression back to the old "error item hidden
inside the list" shape would fail loudly here.
"""
import importlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

from fam import extcal


def _expand(components, window_start, window_end):
    """Thin wrapper -- just documents, at every call site, that `expand()`
    returns a dict, not a bare list."""
    result = extcal.expand(components, window_start, window_end)
    assert isinstance(result, dict)
    assert set(result.keys()) == {"occurrences", "errors"}
    return result


# ---------------------------------------------------------------------
# folding + escaping
# ---------------------------------------------------------------------

def test_unfold_and_unescape_summary_across_folded_continuation():
    # The RFC 5545 fold point can land mid-word -- the single leading
    # space of the continuation line is a pure delimiter, not content, so
    # unfolding does NOT re-insert a space at the fold point.
    ics = (
        "BEGIN:VCALENDAR\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:evt-fold@example.com\r\n"
        "SUMMARY:Buy milk\\, bread and eggs\\; then call mom\\n(urgent)\r\n"
        " -- continu\r\n"
        " ed here\r\n"
        "DTSTART:20260705T100000Z\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    comps = extcal.parse_ics(ics)
    assert len(comps) == 1
    assert comps[0]["summary"] == (
        "Buy milk, bread and eggs; then call mom\n(urgent)-- continued here"
    )


def test_location_escaping_is_applied_too():
    ics = (
        "BEGIN:VEVENT\r\n"
        "UID:evt-loc@example.com\r\n"
        "SUMMARY:Meeting\r\n"
        "LOCATION:Building A\\, Room 5\r\n"
        "DTSTART:20260705T100000Z\r\n"
        "END:VEVENT\r\n"
    )
    comps = extcal.parse_ics(ics)
    assert comps[0]["location"] == "Building A, Room 5"


# ---------------------------------------------------------------------
# DTSTART/DTEND: TZID, UTC-Z, VALUE=DATE, missing DTEND, unknown TZID
# ---------------------------------------------------------------------

def test_dtstart_tzid_almaty_converts_to_correct_utc():
    ics = (
        "BEGIN:VEVENT\r\n"
        "UID:evt-tz@example.com\r\n"
        "SUMMARY:Yoga\r\n"
        "DTSTART;TZID=Asia/Almaty:20260728T180000\r\n"
        "DTEND;TZID=Asia/Almaty:20260728T190000\r\n"
        "END:VEVENT\r\n"
    )
    comps = extcal.parse_ics(ics)
    c = comps[0]
    # Asia/Almaty is UTC+5, no DST -- 18:00 local == 13:00 UTC.
    assert c["dtstart_utc"] == datetime(2026, 7, 28, 13, 0, 0, tzinfo=timezone.utc)
    assert c["dtend_utc"] == datetime(2026, 7, 28, 14, 0, 0, tzinfo=timezone.utc)
    assert c["all_day"] is False


def test_dtstart_utc_z_form_is_used_as_is():
    ics = (
        "BEGIN:VEVENT\r\n"
        "UID:evt-utc@example.com\r\n"
        "SUMMARY:Standup\r\n"
        "DTSTART:20260705T090000Z\r\n"
        "END:VEVENT\r\n"
    )
    comps = extcal.parse_ics(ics)
    assert comps[0]["dtstart_utc"] == datetime(2026, 7, 5, 9, 0, 0, tzinfo=timezone.utc)


def test_value_date_sets_all_day_true():
    ics = (
        "BEGIN:VEVENT\r\n"
        "UID:evt-allday@example.com\r\n"
        "SUMMARY:Birthday\r\n"
        "DTSTART;VALUE=DATE:20260801\r\n"
        "DTEND;VALUE=DATE:20260802\r\n"
        "END:VEVENT\r\n"
    )
    comps = extcal.parse_ics(ics)
    c = comps[0]
    assert c["all_day"] is True
    # midnight Asia/Almaty (documented assumption) on 2026-08-01.
    assert c["dtstart_utc"] == datetime(2026, 7, 31, 19, 0, 0, tzinfo=timezone.utc)
    assert c["dtend_utc"] == datetime(2026, 8, 1, 19, 0, 0, tzinfo=timezone.utc)


def test_missing_dtend_defaults_to_one_hour_duration():
    ics = (
        "BEGIN:VEVENT\r\n"
        "UID:evt-nodtend@example.com\r\n"
        "SUMMARY:Quick call\r\n"
        "DTSTART;TZID=Asia/Almaty:20260705T140000\r\n"
        "END:VEVENT\r\n"
    )
    comps = extcal.parse_ics(ics)
    c = comps[0]
    assert c["dtend_utc"] - c["dtstart_utc"] == timedelta(hours=1)


def test_unknown_tzid_falls_back_to_almaty_instead_of_dropping_component():
    # Documented assumption: an unresolvable TZID (typo, or tzdata gap)
    # degrades to Asia/Almaty rather than losing the whole VEVENT.
    ics = (
        "BEGIN:VEVENT\r\n"
        "UID:evt-badtz@example.com\r\n"
        "SUMMARY:Weird tz\r\n"
        "DTSTART;TZID=Not/A_Real_Zone:20260705T100000\r\n"
        "END:VEVENT\r\n"
    )
    comps = extcal.parse_ics(ics)
    assert len(comps) == 1
    # 10:00 interpreted as Asia/Almaty (UTC+5) -> 05:00 UTC.
    assert comps[0]["dtstart_utc"] == datetime(2026, 7, 5, 5, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------
# VALARM presence, STATUS, SEQUENCE
# ---------------------------------------------------------------------

def test_valarm_presence_is_detected_and_its_own_properties_are_skipped():
    ics = (
        "BEGIN:VEVENT\r\n"
        "UID:evt-alarm@example.com\r\n"
        "SUMMARY:Real summary\r\n"
        "DTSTART:20260705T100000Z\r\n"
        "SEQUENCE:3\r\n"
        "BEGIN:VALARM\r\n"
        "ACTION:DISPLAY\r\n"
        "DESCRIPTION:This must not overwrite the VEVENT's own fields\r\n"
        "TRIGGER:-PT15M\r\n"
        "END:VALARM\r\n"
        "STATUS:CONFIRMED\r\n"
        "END:VEVENT\r\n"
    )
    comps = extcal.parse_ics(ics)
    c = comps[0]
    assert c["has_alarm"] is True
    assert c["summary"] == "Real summary"
    assert c["status"] == "CONFIRMED"
    assert c["seq"] == 3


def test_no_valarm_means_has_alarm_false():
    ics = (
        "BEGIN:VEVENT\r\n"
        "UID:evt-noalarm@example.com\r\n"
        "SUMMARY:Silent\r\n"
        "DTSTART:20260705T100000Z\r\n"
        "END:VEVENT\r\n"
    )
    comps = extcal.parse_ics(ics)
    assert comps[0]["has_alarm"] is False


def test_status_cancelled_is_recorded():
    ics = (
        "BEGIN:VEVENT\r\n"
        "UID:evt-cancelled@example.com\r\n"
        "SUMMARY:Cancelled meeting\r\n"
        "DTSTART:20260705T100000Z\r\n"
        "STATUS:CANCELLED\r\n"
        "END:VEVENT\r\n"
    )
    comps = extcal.parse_ics(ics)
    assert comps[0]["status"] == "CANCELLED"


# ---------------------------------------------------------------------
# garbage input -> [] , never an exception
# ---------------------------------------------------------------------

def test_garbage_ics_returns_empty_list_not_exception():
    assert extcal.parse_ics("this is not ics at all, just noise\nrandom\x00bytes") == []


def test_empty_and_none_text_returns_empty_list():
    assert extcal.parse_ics("") == []
    assert extcal.parse_ics(None) == []


def test_truncated_vevent_with_no_end_is_dropped_not_raised():
    ics = (
        "BEGIN:VEVENT\r\n"
        "UID:evt-truncated@example.com\r\n"
        "SUMMARY:Never closed\r\n"
        "DTSTART:20260705T100000Z\r\n"
    )
    assert extcal.parse_ics(ics) == []


def test_vevent_missing_uid_is_dropped_but_siblings_survive():
    ics = (
        "BEGIN:VEVENT\r\n"
        "SUMMARY:No uid, unusable\r\n"
        "DTSTART:20260705T100000Z\r\n"
        "END:VEVENT\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:evt-good@example.com\r\n"
        "SUMMARY:Has uid\r\n"
        "DTSTART:20260705T110000Z\r\n"
        "END:VEVENT\r\n"
    )
    comps = extcal.parse_ics(ics)
    assert len(comps) == 1
    assert comps[0]["uid"] == "evt-good@example.com"


def test_vevent_missing_dtstart_is_dropped_but_siblings_survive():
    ics = (
        "BEGIN:VEVENT\r\n"
        "UID:evt-no-dtstart@example.com\r\n"
        "SUMMARY:No dtstart, unusable\r\n"
        "END:VEVENT\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:evt-good2@example.com\r\n"
        "SUMMARY:Has dtstart\r\n"
        "DTSTART:20260705T110000Z\r\n"
        "END:VEVENT\r\n"
    )
    comps = extcal.parse_ics(ics)
    assert len(comps) == 1
    assert comps[0]["uid"] == "evt-good2@example.com"


# ---------------------------------------------------------------------
# RRULE expansion: weekly, monthly+interval, COUNT, UNTIL, window bounds
# ---------------------------------------------------------------------

def _weekly_component(uid="evt-weekly@example.com"):
    return extcal.parse_ics(
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        "SUMMARY:Gym\r\n"
        "DTSTART;TZID=Asia/Almaty:20260706T070000\r\n"
        "DTEND;TZID=Asia/Almaty:20260706T080000\r\n"
        "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR\r\n"
        "END:VEVENT\r\n"
    )[0]


def test_rrule_weekly_expands_within_window_and_not_beyond_it():
    comp = _weekly_component()
    w_start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    w_end = datetime(2026, 7, 20, tzinfo=timezone.utc)
    result = _expand([comp], w_start, w_end)
    assert result["errors"] == []
    occs = result["occurrences"]
    starts = sorted(o["start_utc"] for o in occs)
    # Mon/Wed/Fri from 2026-07-06 through 2026-07-17 (Almaty UTC+5 -> 07:00
    # local == 02:00 UTC each day), nothing before window start (Jul 1) or
    # after window end (Jul 20).
    expected = [
        "2026-07-06T02:00:00+00:00", "2026-07-08T02:00:00+00:00",
        "2026-07-10T02:00:00+00:00", "2026-07-13T02:00:00+00:00",
        "2026-07-15T02:00:00+00:00", "2026-07-17T02:00:00+00:00",
    ]
    assert starts == expected
    for o in occs:
        assert o["uid"] == "evt-weekly@example.com"
        assert o["title"] == "Gym"
        assert o["recurrence_id"] == o["start_utc"]  # un-overridden identity


def test_rrule_respects_window_start_and_end_strictly():
    comp = _weekly_component()
    # A tight window covering only 2026-07-08 (Wed).
    w_start = datetime(2026, 7, 8, 0, 0, tzinfo=timezone.utc)
    w_end = datetime(2026, 7, 8, 23, 59, tzinfo=timezone.utc)
    occs = _expand([comp], w_start, w_end)["occurrences"]
    assert len(occs) == 1
    assert occs[0]["start_utc"] == "2026-07-08T02:00:00+00:00"


def test_rrule_monthly_with_interval_expands_correctly():
    comp = extcal.parse_ics(
        "BEGIN:VEVENT\r\n"
        "UID:evt-monthly@example.com\r\n"
        "SUMMARY:Rent reminder\r\n"
        "DTSTART;TZID=Asia/Almaty:20260115T090000\r\n"
        "DTEND;TZID=Asia/Almaty:20260115T093000\r\n"
        "RRULE:FREQ=MONTHLY;INTERVAL=2;BYMONTHDAY=15\r\n"
        "END:VEVENT\r\n"
    )[0]
    w_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    w_end = datetime(2026, 7, 1, tzinfo=timezone.utc)
    result = _expand([comp], w_start, w_end)
    assert result["errors"] == []
    starts = sorted(o["start_utc"] for o in result["occurrences"])
    # every 2 months starting Jan 15: Jan, Mar, May -- not Feb/Apr/Jun/Jul.
    assert starts == [
        "2026-01-15T04:00:00+00:00",
        "2026-03-15T04:00:00+00:00",
        "2026-05-15T04:00:00+00:00",
    ]


def test_rrule_count_limits_total_instances_even_if_window_is_wider():
    # COUNT=3 must stop the series after 3 instances, even though the
    # window given to expand() would otherwise fit many more weekly slots.
    comp = extcal.parse_ics(
        "BEGIN:VEVENT\r\n"
        "UID:evt-count@example.com\r\n"
        "SUMMARY:Physio\r\n"
        "DTSTART;TZID=Asia/Almaty:20260706T070000\r\n"
        "RRULE:FREQ=WEEKLY;COUNT=3\r\n"
        "END:VEVENT\r\n"
    )[0]
    w_start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    w_end = datetime(2026, 9, 1, tzinfo=timezone.utc)  # much wider than 3 weeks
    result = _expand([comp], w_start, w_end)
    assert result["errors"] == []
    starts = sorted(o["start_utc"] for o in result["occurrences"])
    assert starts == [
        "2026-07-06T02:00:00+00:00",
        "2026-07-13T02:00:00+00:00",
        "2026-07-20T02:00:00+00:00",
    ]


def test_rrule_count_boundary_partially_inside_window():
    # Same COUNT=3 series, but the window only covers the first 2 of the
    # 3 total instances -- COUNT must still cap the series at 3 (not
    # regenerate more because the window is narrow), and the window must
    # still correctly exclude the 3rd instance and anything beyond it.
    comp = extcal.parse_ics(
        "BEGIN:VEVENT\r\n"
        "UID:evt-count2@example.com\r\n"
        "SUMMARY:Physio\r\n"
        "DTSTART;TZID=Asia/Almaty:20260706T070000\r\n"
        "RRULE:FREQ=WEEKLY;COUNT=3\r\n"
        "END:VEVENT\r\n"
    )[0]
    w_start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    w_end = datetime(2026, 7, 14, tzinfo=timezone.utc)  # covers only 07-06, 07-13
    result = _expand([comp], w_start, w_end)
    assert result["errors"] == []
    starts = sorted(o["start_utc"] for o in result["occurrences"])
    assert starts == ["2026-07-06T02:00:00+00:00", "2026-07-13T02:00:00+00:00"]


def test_rrule_until_stops_series_at_the_given_date():
    # UNTIL in UTC form (the RFC-recommended form for an UNTIL paired with
    # a TZID'd DTSTART) -- the series must stop on/before UNTIL even
    # though the window given to expand() extends well past it.
    comp = extcal.parse_ics(
        "BEGIN:VEVENT\r\n"
        "UID:evt-until@example.com\r\n"
        "SUMMARY:Summer class\r\n"
        "DTSTART;TZID=Asia/Almaty:20260706T070000\r\n"
        "RRULE:FREQ=WEEKLY;UNTIL=20260720T020000Z\r\n"
        "END:VEVENT\r\n"
    )[0]
    w_start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    w_end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    result = _expand([comp], w_start, w_end)
    assert result["errors"] == []
    starts = sorted(o["start_utc"] for o in result["occurrences"])
    # UNTIL is inclusive (RFC 5545) -- 07-06, 07-13, 07-20 (== UNTIL) all
    # count; 07-27 (which would fall inside the wide window) must not.
    assert starts == [
        "2026-07-06T02:00:00+00:00",
        "2026-07-13T02:00:00+00:00",
        "2026-07-20T02:00:00+00:00",
    ]


def test_rrule_until_boundary_narrower_than_window():
    # The window extends past UNTIL in BOTH directions -- confirms UNTIL,
    # not the window, is what stops the series (the window alone would
    # otherwise admit at least one more weekly instance).
    comp = extcal.parse_ics(
        "BEGIN:VEVENT\r\n"
        "UID:evt-until2@example.com\r\n"
        "SUMMARY:Summer class\r\n"
        "DTSTART;TZID=Asia/Almaty:20260706T070000\r\n"
        "RRULE:FREQ=WEEKLY;UNTIL=20260713T020000Z\r\n"
        "END:VEVENT\r\n"
    )[0]
    w_start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    w_end = datetime(2026, 8, 1, tzinfo=timezone.utc)
    result = _expand([comp], w_start, w_end)
    assert result["errors"] == []
    starts = sorted(o["start_utc"] for o in result["occurrences"])
    assert starts == ["2026-07-06T02:00:00+00:00", "2026-07-13T02:00:00+00:00"]


def test_early_morning_local_event_does_not_roll_over_calendar_day_in_utc():
    # Regression guard for the day-rollover trap: 01:00 Asia/Almaty is
    # 20:00 UTC the PREVIOUS day. A BYMONTHDAY=1 rule must still fire on
    # the 1st of each month (local calendar day), not silently drift to
    # the last day of the previous month if expansion were done in UTC.
    comp = extcal.parse_ics(
        "BEGIN:VEVENT\r\n"
        "UID:evt-earlymorning@example.com\r\n"
        "SUMMARY:Early alarm\r\n"
        "DTSTART;TZID=Asia/Almaty:20260201T010000\r\n"
        "RRULE:FREQ=MONTHLY;BYMONTHDAY=1\r\n"
        "END:VEVENT\r\n"
    )[0]
    w_start = datetime(2026, 2, 1, tzinfo=timezone.utc)
    w_end = datetime(2026, 4, 1, tzinfo=timezone.utc)
    result = _expand([comp], w_start, w_end)
    assert result["errors"] == []
    starts = sorted(o["start_utc"] for o in result["occurrences"])
    # Correct (local-anchored) expansion fires on the true local 1st of
    # Feb/Mar/Apr; each 01:00-Almaty instant is 20:00 UTC the day before
    # (Almaty is UTC+5). The window [2026-02-01, 2026-04-01) UTC catches
    # the Mar-1st and Apr-1st local instances (as 2026-02-28T20:00 and
    # 2026-03-31T20:00 UTC) -- Feb-1st local (2026-01-31T20:00 UTC) falls
    # just before the window and is correctly excluded. A BUGGY
    # UTC-anchored expansion would instead produce dates literally on the
    # 1st in UTC (2026-02-01T20:00, 2026-03-01T20:00) -- verifiably
    # different values, which is what this regression guards against.
    assert starts == ["2026-02-28T20:00:00+00:00", "2026-03-31T20:00:00+00:00"]


def test_unparseable_rrule_yields_error_not_silent_disappearance():
    # Review finding I2: a real RRULE dateutil.rrulestr rejects (garbage
    # syntax stands in for "an Apple quirk dateutil can't parse" -- the
    # exact construct doesn't matter, what matters is the failure mode)
    # must produce an `errors` entry naming the uid, not a silently empty
    # result indistinguishable from "this series has zero instances".
    comp = extcal.parse_ics(
        "BEGIN:VEVENT\r\n"
        "UID:evt-badrrule@example.com\r\n"
        "SUMMARY:Broken series\r\n"
        "DTSTART;TZID=Asia/Almaty:20260706T070000\r\n"
        "RRULE:NOT_A_VALID_RRULE_AT_ALL\r\n"
        "END:VEVENT\r\n"
    )[0]
    result = _expand([comp], datetime(2026, 7, 1, tzinfo=timezone.utc),
                      datetime(2026, 7, 20, tzinfo=timezone.utc))
    assert result["occurrences"] == []
    assert len(result["errors"]) == 1
    assert "evt-badrrule@example.com" in result["errors"][0]


def test_unparseable_rrule_does_not_block_other_components_in_same_call():
    bad = extcal.parse_ics(
        "BEGIN:VEVENT\r\nUID:evt-badrrule2@example.com\r\nSUMMARY:Broken\r\n"
        "DTSTART;TZID=Asia/Almaty:20260706T070000\r\n"
        "RRULE:NOT_A_VALID_RRULE_AT_ALL\r\nEND:VEVENT\r\n"
    )[0]
    good = extcal.parse_ics(
        "BEGIN:VEVENT\r\nUID:evt-plain-ok@example.com\r\nSUMMARY:Fine\r\n"
        "DTSTART:20260705T100000Z\r\nEND:VEVENT\r\n"
    )[0]
    result = _expand([bad, good], datetime(2026, 7, 1, tzinfo=timezone.utc),
                      datetime(2026, 7, 20, tzinfo=timezone.utc))
    titles = [o["title"] for o in result["occurrences"]]
    assert titles == ["Fine"]
    assert len(result["errors"]) == 1


# ---------------------------------------------------------------------
# EXDATE excludes an occurrence
# ---------------------------------------------------------------------

def test_exdate_excludes_matching_occurrence():
    comp = extcal.parse_ics(
        "BEGIN:VEVENT\r\n"
        "UID:evt-exdate@example.com\r\n"
        "SUMMARY:Gym\r\n"
        "DTSTART;TZID=Asia/Almaty:20260706T070000\r\n"
        "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR\r\n"
        "EXDATE;TZID=Asia/Almaty:20260710T070000\r\n"
        "END:VEVENT\r\n"
    )[0]
    w_start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    w_end = datetime(2026, 7, 20, tzinfo=timezone.utc)
    occs = _expand([comp], w_start, w_end)["occurrences"]
    starts = [o["start_utc"] for o in occs]
    assert "2026-07-10T02:00:00+00:00" not in starts
    assert len(starts) == 5  # 6 normally, minus the one EXDATE


def test_exdate_with_multiple_comma_separated_values():
    comp = extcal.parse_ics(
        "BEGIN:VEVENT\r\n"
        "UID:evt-exdate-multi@example.com\r\n"
        "SUMMARY:Gym\r\n"
        "DTSTART;TZID=Asia/Almaty:20260706T070000\r\n"
        "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR\r\n"
        "EXDATE;TZID=Asia/Almaty:20260708T070000,20260710T070000\r\n"
        "END:VEVENT\r\n"
    )[0]
    w_start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    w_end = datetime(2026, 7, 20, tzinfo=timezone.utc)
    occs = _expand([comp], w_start, w_end)["occurrences"]
    starts = [o["start_utc"] for o in occs]
    assert "2026-07-08T02:00:00+00:00" not in starts
    assert "2026-07-10T02:00:00+00:00" not in starts
    assert len(starts) == 4


def test_exdate_in_a_different_form_than_dtstart_still_matches():
    # EXDATE and DTSTART are each converted to UTC independently (from
    # their own TZID/VALUE params) before comparison, so an EXDATE
    # expressed in a different form than DTSTART (here: explicit UTC "Z"
    # instead of TZID=Asia/Almaty) still correctly excludes the instance.
    # This is an already-correct property of the implementation, not an
    # open assumption -- see task-2-report.md's fix-round section (Minor).
    comp = extcal.parse_ics(
        "BEGIN:VEVENT\r\n"
        "UID:evt-exdate-mixed-tz@example.com\r\n"
        "SUMMARY:Gym\r\n"
        "DTSTART;TZID=Asia/Almaty:20260706T070000\r\n"
        "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR\r\n"
        "EXDATE:20260710T020000Z\r\n"  # same instant as 07:00 Almaty, in UTC form
        "END:VEVENT\r\n"
    )[0]
    w_start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    w_end = datetime(2026, 7, 20, tzinfo=timezone.utc)
    occs = _expand([comp], w_start, w_end)["occurrences"]
    starts = [o["start_utc"] for o in occs]
    assert "2026-07-10T02:00:00+00:00" not in starts
    assert len(starts) == 5


# ---------------------------------------------------------------------
# RECURRENCE-ID override replaces the generated occurrence, not adds
# ---------------------------------------------------------------------

def test_recurrence_id_override_replaces_generated_occurrence_not_duplicates():
    master = _weekly_component()
    override = extcal.parse_ics(
        "BEGIN:VEVENT\r\n"
        "UID:evt-weekly@example.com\r\n"
        "SUMMARY:Gym (moved to later)\r\n"
        "DTSTART;TZID=Asia/Almaty:20260713T090000\r\n"
        "DTEND;TZID=Asia/Almaty:20260713T100000\r\n"
        "RECURRENCE-ID;TZID=Asia/Almaty:20260713T070000\r\n"
        "END:VEVENT\r\n"
    )[0]
    w_start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    w_end = datetime(2026, 7, 20, tzinfo=timezone.utc)
    result = _expand([master, override], w_start, w_end)
    assert result["errors"] == []
    occs = result["occurrences"]

    titles = [o["title"] for o in occs]
    assert titles.count("Gym") == 5          # 6 generated minus the overridden slot
    assert titles.count("Gym (moved to later)") == 1
    assert len(occs) == 6                    # no duplicate for 2026-07-13

    moved = next(o for o in occs if o["title"] == "Gym (moved to later)")
    assert moved["start_utc"] == "2026-07-13T04:00:00+00:00"  # 09:00 Almaty
    assert moved["recurrence_id"] == "2026-07-13T02:00:00+00:00"  # original 07:00 slot


def test_override_moved_outside_window_still_excluded_and_original_slot_stays_gone():
    # The override moves the 2026-07-13 instance to 2026-07-25, OUTSIDE
    # the [Jul 1, Jul 20] window -- it must not appear, AND the original
    # 07-13 07:00 slot must still be suppressed (not resurrected).
    master = _weekly_component()
    override = extcal.parse_ics(
        "BEGIN:VEVENT\r\n"
        "UID:evt-weekly@example.com\r\n"
        "SUMMARY:Gym (moved way later)\r\n"
        "DTSTART;TZID=Asia/Almaty:20260725T070000\r\n"
        "RECURRENCE-ID;TZID=Asia/Almaty:20260713T070000\r\n"
        "END:VEVENT\r\n"
    )[0]
    w_start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    w_end = datetime(2026, 7, 20, tzinfo=timezone.utc)
    occs = _expand([master, override], w_start, w_end)["occurrences"]
    titles = [o["title"] for o in occs]
    assert "Gym (moved way later)" not in titles
    assert titles.count("Gym") == 5  # 07-13 slot suppressed, not replaced-and-visible


def test_override_moved_into_window_appears_even_if_original_slot_was_outside():
    master = _weekly_component()  # weekly Mon/Wed/Fri from 2026-07-06
    override = extcal.parse_ics(
        "BEGIN:VEVENT\r\n"
        "UID:evt-weekly@example.com\r\n"
        "SUMMARY:Gym (pulled forward)\r\n"
        "DTSTART;TZID=Asia/Almaty:20260703T070000\r\n"
        "RECURRENCE-ID;TZID=Asia/Almaty:20260629T070000\r\n"
        "END:VEVENT\r\n"
    )[0]
    # Window starts AFTER the original 06-29 slot but the moved 07-03 date
    # falls inside it.
    w_start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    w_end = datetime(2026, 7, 5, tzinfo=timezone.utc)
    occs = _expand([master, override], w_start, w_end)["occurrences"]
    titles = [o["title"] for o in occs]
    assert "Gym (pulled forward)" in titles


def test_status_cancelled_override_is_flagged_not_dropped():
    master = _weekly_component()
    cancelled = extcal.parse_ics(
        "BEGIN:VEVENT\r\n"
        "UID:evt-weekly@example.com\r\n"
        "SUMMARY:Gym\r\n"
        "DTSTART;TZID=Asia/Almaty:20260710T070000\r\n"
        "RECURRENCE-ID;TZID=Asia/Almaty:20260710T070000\r\n"
        "STATUS:CANCELLED\r\n"
        "END:VEVENT\r\n"
    )[0]
    w_start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    w_end = datetime(2026, 7, 20, tzinfo=timezone.utc)
    occs = _expand([master, cancelled], w_start, w_end)["occurrences"]
    # Exactly one occurrence at that slot (not zero, not two), and it is
    # flagged CANCELLED rather than silently dropped -- the caller
    # (plan_changes, a later task) decides cancel-vs-drop, not expand().
    at_slot = [o for o in occs if o["start_utc"] == "2026-07-10T02:00:00+00:00"]
    assert len(at_slot) == 1
    assert at_slot[0]["status"] == "CANCELLED"


def test_standalone_status_cancelled_component_is_flagged():
    comp = extcal.parse_ics(
        "BEGIN:VEVENT\r\n"
        "UID:evt-standalone-cancelled@example.com\r\n"
        "SUMMARY:Cancelled meeting\r\n"
        "DTSTART:20260705T100000Z\r\n"
        "STATUS:CANCELLED\r\n"
        "END:VEVENT\r\n"
    )
    occs = _expand(comp, datetime(2026, 7, 1, tzinfo=timezone.utc),
                   datetime(2026, 7, 10, tzinfo=timezone.utc))["occurrences"]
    assert len(occs) == 1
    assert occs[0]["status"] == "CANCELLED"


# ---------------------------------------------------------------------
# singles: plain (non-recurring) components, window inclusion
# ---------------------------------------------------------------------

def test_single_component_included_only_if_inside_window():
    comps = extcal.parse_ics(
        "BEGIN:VEVENT\r\n"
        "UID:evt-inside@example.com\r\n"
        "SUMMARY:Inside\r\n"
        "DTSTART:20260705T100000Z\r\n"
        "END:VEVENT\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:evt-outside@example.com\r\n"
        "SUMMARY:Outside\r\n"
        "DTSTART:20260801T100000Z\r\n"
        "END:VEVENT\r\n"
    )
    occs = _expand(comps, datetime(2026, 7, 1, tzinfo=timezone.utc),
                   datetime(2026, 7, 10, tzinfo=timezone.utc))["occurrences"]
    titles = [o["title"] for o in occs]
    assert titles == ["Inside"]


def test_expand_accepts_iso_strings_for_window_bounds():
    comps = extcal.parse_ics(
        "BEGIN:VEVENT\r\n"
        "UID:evt-iso-window@example.com\r\n"
        "SUMMARY:Inside\r\n"
        "DTSTART:20260705T100000Z\r\n"
        "END:VEVENT\r\n"
    )
    result = _expand(comps, "2026-07-01T00:00:00+00:00", "2026-07-10T00:00:00+00:00")
    assert result["errors"] == []
    assert len(result["occurrences"]) == 1


def test_expand_empty_components_list_returns_empty_result():
    result = _expand([], datetime(2026, 7, 1, tzinfo=timezone.utc),
                      datetime(2026, 7, 10, tzinfo=timezone.utc))
    assert result == {"occurrences": [], "errors": []}


def test_expand_invalid_window_bounds_reports_error_not_exception():
    comps = extcal.parse_ics(
        "BEGIN:VEVENT\r\nUID:e@x.com\r\nSUMMARY:x\r\nDTSTART:20260705T100000Z\r\nEND:VEVENT\r\n"
    )
    result = _expand(comps, None, None)
    assert result["occurrences"] == []
    assert result["errors"] != []

    result2 = _expand(comps, "garbage", "also garbage")
    assert result2["occurrences"] == []
    assert result2["errors"] != []


# ---------------------------------------------------------------------
# I3: expand() must never raise, even on malformed `components` entries
# ---------------------------------------------------------------------

def test_expand_non_dict_component_is_skipped_not_raised():
    comps = extcal.parse_ics(
        "BEGIN:VEVENT\r\nUID:evt-ok@example.com\r\nSUMMARY:Ok\r\n"
        "DTSTART:20260705T100000Z\r\nEND:VEVENT\r\n"
    )
    garbage_mixed_in = [comps[0], "not a component", 42, None, ["also garbage"]]
    result = _expand(garbage_mixed_in, datetime(2026, 7, 1, tzinfo=timezone.utc),
                      datetime(2026, 7, 10, tzinfo=timezone.utc))
    titles = [o["title"] for o in result["occurrences"]]
    assert titles == ["Ok"]
    # every non-dict entry is noted, not silently swallowed nor raised
    assert len(result["errors"]) == 4


def test_expand_all_garbage_components_returns_empty_result_not_exception():
    result = _expand(["nope", 1, 2.0, {"totally": "unrelated"}],
                      datetime(2026, 7, 1, tzinfo=timezone.utc),
                      datetime(2026, 7, 10, tzinfo=timezone.utc))
    assert result["occurrences"] == []
    # the dict entry lacking uid/dtstart_utc is silently skipped (same
    # rule as a parse_ics-produced component missing those fields); only
    # the three non-dict entries are noted as errors.
    assert len(result["errors"]) == 3


# ---------------------------------------------------------------------
# dateutil missing: explicit error, never a silent empty/zero result
# ---------------------------------------------------------------------

def _block_dateutil_import(monkeypatch):
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name == "dateutil" or name.startswith("dateutil."):
            raise ImportError("simulated: python-dateutil not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)


def test_dateutil_missing_yields_explicit_error_not_silent_zero(monkeypatch):
    comp = _weekly_component()
    _block_dateutil_import(monkeypatch)

    result = _expand([comp], datetime(2026, 7, 1, tzinfo=timezone.utc),
                      datetime(2026, 7, 20, tzinfo=timezone.utc))
    # Structurally impossible for the error to hide inside `occurrences`:
    # that key holds only real, fully-populated Occurrence dicts.
    assert result["occurrences"] == []
    assert len(result["errors"]) == 1
    assert "python-dateutil" in result["errors"][0]
    for occ in result["occurrences"]:
        assert occ["uid"] is not None  # (vacuous here, but pins the invariant)


def test_dateutil_missing_does_not_block_non_recurring_components(monkeypatch):
    # Only the RRULE master needs dateutil -- a plain single component in
    # the SAME call must still be expanded normally, and it must never be
    # confusable with the error (which lives in `errors`, not mixed in).
    master = _weekly_component()
    single = extcal.parse_ics(
        "BEGIN:VEVENT\r\nUID:evt-plain@example.com\r\nSUMMARY:Plain\r\n"
        "DTSTART:20260705T100000Z\r\nEND:VEVENT\r\n"
    )[0]
    _block_dateutil_import(monkeypatch)

    result = _expand([master, single], datetime(2026, 7, 1, tzinfo=timezone.utc),
                      datetime(2026, 7, 20, tzinfo=timezone.utc))
    titles = [o["title"] for o in result["occurrences"]]
    assert titles == ["Plain"]  # the RRULE one couldn't expand, Plain still did
    assert any("python-dateutil" in e for e in result["errors"])
    # every item in `occurrences` is a real occurrence -- no `.get("error")`
    # guard is needed by a caller, by construction.
    for occ in result["occurrences"]:
        assert "error" not in occ


def test_expand_without_any_rrule_component_never_touches_dateutil(monkeypatch):
    # No RRULE anywhere in the input -- _load_rrule_module must not even
    # be called, so this must succeed identically whether or not dateutil
    # is importable.
    def boom(name, *a, **k):
        if name == "dateutil" or name.startswith("dateutil."):
            raise AssertionError("dateutil must not be imported when there is no RRULE")
        return __import__(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", boom)
    single = extcal.parse_ics(
        "BEGIN:VEVENT\r\nUID:evt-plain2@example.com\r\nSUMMARY:Plain\r\n"
        "DTSTART:20260705T100000Z\r\nEND:VEVENT\r\n"
    )
    result = _expand(single, datetime(2026, 7, 1, tzinfo=timezone.utc),
                      datetime(2026, 7, 20, tzinfo=timezone.utc))
    assert len(result["occurrences"]) == 1
    assert result["errors"] == []


# ---------------------------------------------------------------------
# regression guard: `fam.cli` (and therefore every `fam` command) must
# import cleanly with python-dateutil absent -- same pattern as the
# existing test_no_pillow_import.py / test_no_google_import.py guards.
# ---------------------------------------------------------------------

class _BlockDateutil:
    """Meta path finder that makes `import dateutil` (or any submodule)
    fail, simulating the Docker sandbox where python-dateutil is not
    installed until a separate, later image rebuild."""

    def find_spec(self, fullname, path, target=None):
        if fullname == "dateutil" or fullname.startswith("dateutil."):
            raise ImportError(f"blocked by test_no_dateutil_import: {fullname}")
        return None  # defer to the normal finders for everything else


def _fresh_import_without_dateutil(monkeypatch, module_names):
    stale = [
        name for name in list(sys.modules)
        if name == "dateutil" or name.startswith("dateutil.")
        or name in module_names
        or name.startswith(tuple(f"{m}." for m in module_names))
    ]
    for name in stale:
        monkeypatch.delitem(sys.modules, name, raising=False)

    blocker = _BlockDateutil()
    sys.meta_path.insert(0, blocker)
    try:
        for name in module_names:
            importlib.import_module(name)
    finally:
        sys.meta_path.remove(blocker)


def test_fam_extcal_imports_without_dateutil(monkeypatch):
    _fresh_import_without_dateutil(monkeypatch, ["fam.extcal"])
    assert "dateutil" not in sys.modules


def test_fam_cli_imports_without_dateutil(monkeypatch):
    _fresh_import_without_dateutil(monkeypatch, ["fam.cli"])
    assert "dateutil" not in sys.modules


def test_fam_cli_help_still_works_without_dateutil(monkeypatch):
    _fresh_import_without_dateutil(monkeypatch, ["fam.cli"])
    from fam import cli
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0


def test_existing_cal_ext_probe_command_still_works_without_dateutil(monkeypatch, capsys):
    _fresh_import_without_dateutil(monkeypatch, ["fam.cli"])
    from fam import cli, extcal as extcal_mod
    monkeypatch.setattr(
        extcal_mod, "probe",
        lambda cfg, request=None: {
            "calendars": [], "counts": extcal_mod._empty_counts(), "errors": [],
        })
    rc = cli.main(["cal-ext", "probe", "--json"])
    assert rc == 0
