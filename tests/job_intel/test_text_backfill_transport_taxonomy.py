"""The transient/permanent taxonomy must be real, not nominal.

`unavailable` is TERMINAL: rows_needing_text admits only
`text_backfill_state IS NULL OR = 'failed'`, so a row marked unavailable is
never offered for backfill again. Before this file existed, _detail_json and
_detail_html returned None identically for a 404, a 429, a 502 and a socket
timeout, and backfill() mapped every falsy result to that terminal bucket —
so one rate-limit burst against api.hh.ru permanently retired up to `budget`
rows, and the retryable `failed` bucket was near-unreachable.

The rule these tests pin: the terminal bucket may only be entered on positive
evidence of ABSENCE (404/410, or a well-formed payload that genuinely carries
no job text). Absence of evidence — a body we could not parse, a status we did
not recognise, a connection that died — is transient. A wrong transient
verdict costs one more request tomorrow; a wrong permanent verdict costs the
row forever.
"""
from __future__ import annotations

import pytest
import requests

from job_intel import ats_sources
from job_intel.ats_sources import (
    DETAIL_RATE_LIMITED,
    DETAIL_REQUEST_DELAY_SECONDS,
    DETAIL_TRANSIENT,
    _detail_html,
    _detail_json,
    fetch_headhunter_detail,
    fetch_smartrecruiters_detail,
    fetch_teamtailor_detail,
    is_rate_limited_detail,
    is_transient_detail,
)
from job_intel.text_backfill import backfill

SR_URL = "https://api.smartrecruiters.com/v1/companies/wise/postings/1"
HH_URL = "https://hh.ru/vacancy/133446873"
TT_URL = "https://acme.teamtailor.com/jobs/1"

# A payload that yields >= PARTIAL_MIN characters of real job text.
LONG_TEXT = "You will own the pricing and incentive structure. " + "x" * 300
SR_OK_PAYLOAD = {"jobAd": {"sections": {"jobDescription": {"text": LONG_TEXT}}}}


class _Resp:
    """The slice of requests.Response the detail helpers actually touch."""

    def __init__(self, status_code, *, payload=None, text="", headers=None,
                 json_error=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


@pytest.fixture(autouse=True)
def _no_politeness_sleep(monkeypatch):
    """Every test here except the delay tests wants the delay out of the way.
    Setting it to 0 (rather than patching time.sleep) exercises the real
    configuration path instead of bypassing it."""
    monkeypatch.setenv("JOB_INTEL_TEXT_BACKFILL_DELAY_SECONDS", "0")


def _responder(resp_or_exc):
    def _fake(url, **kwargs):
        if isinstance(resp_or_exc, BaseException):
            raise resp_or_exc
        return resp_or_exc
    return _fake


def _row(source="smartrecruiters", url=SR_URL, title="Head of Product"):
    return {"source": source, "title": title, "description": "", "url": url}


# --- the signals themselves --------------------------------------------------

def test_the_transient_signals_are_truthy():
    """Load-bearing. Every fetcher already guards with `if not payload:
    return None` (= permanent). A falsy sentinel would be swallowed by those
    guards and silently become the terminal verdict it exists to avoid."""
    assert bool(DETAIL_TRANSIENT) is True
    assert bool(DETAIL_RATE_LIMITED) is True
    assert DETAIL_TRANSIENT is not DETAIL_RATE_LIMITED


def test_the_signal_predicates_do_not_claim_ordinary_values():
    assert is_transient_detail(DETAIL_TRANSIENT) is True
    assert is_transient_detail(DETAIL_RATE_LIMITED) is True
    assert is_rate_limited_detail(DETAIL_RATE_LIMITED) is True
    assert is_rate_limited_detail(DETAIL_TRANSIENT) is False
    for ordinary in (None, "", "text", {}, {"a": 1}, 0, [], object()):
        assert is_transient_detail(ordinary) is False
        assert is_rate_limited_detail(ordinary) is False


# --- _detail_json: status -> outcome ----------------------------------------

@pytest.mark.parametrize("status", [404, 410])
def test_detail_json_absent_statuses_are_permanent(monkeypatch, status):
    """404/410 are the only positive evidence that the posting is gone."""
    monkeypatch.setattr(ats_sources, "_http_get", _responder(_Resp(status)))
    assert _detail_json(SR_URL) is None


def test_detail_json_429_is_rate_limited(monkeypatch):
    monkeypatch.setattr(ats_sources, "_http_get", _responder(_Resp(429)))
    assert _detail_json(SR_URL) is DETAIL_RATE_LIMITED


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_detail_json_server_errors_are_transient(monkeypatch, status):
    monkeypatch.setattr(ats_sources, "_http_get", _responder(_Resp(status)))
    assert _detail_json(SR_URL) is DETAIL_TRANSIENT


@pytest.mark.parametrize("status", [401, 403, 408, 400, 301])
def test_detail_json_unrecognised_statuses_default_to_transient(monkeypatch, status):
    """A 403 anti-bot block or a 401 is not evidence the job is gone, and 403
    is often what a soft rate-limit looks like. Only 404/410 retire a row."""
    monkeypatch.setattr(ats_sources, "_http_get", _responder(_Resp(status)))
    assert _detail_json(SR_URL) is DETAIL_TRANSIENT


@pytest.mark.parametrize("exc", [
    requests.Timeout("timed out"),
    requests.ConnectionError("connection reset by peer"),
    requests.RequestException("something else"),
    RuntimeError("an unexpected bug in the transport layer"),
])
def test_detail_json_transport_exceptions_are_transient(monkeypatch, exc):
    monkeypatch.setattr(ats_sources, "_http_get", _responder(exc))
    assert _detail_json(SR_URL) is DETAIL_TRANSIENT


def test_detail_json_undecodable_200_is_transient(monkeypatch):
    """A 200 we cannot parse is evidence about the transport (an interstitial,
    a truncated body, an HTML error page served with 200), never evidence
    about the posting. It must not retire the row."""
    monkeypatch.setattr(ats_sources, "_http_get",
                        _responder(_Resp(200, json_error=ValueError("no JSON"))))
    assert _detail_json(SR_URL) is DETAIL_TRANSIENT


def test_detail_json_valid_non_dict_200_is_permanent(monkeypatch):
    """A parseable answer that is not a job object is a well-formed 'no job
    here' — absent, not transient."""
    monkeypatch.setattr(ats_sources, "_http_get", _responder(_Resp(200, payload=[1, 2])))
    assert _detail_json(SR_URL) is None


def test_detail_json_200_returns_the_payload(monkeypatch):
    monkeypatch.setattr(ats_sources, "_http_get",
                        _responder(_Resp(200, payload={"id": "1"})))
    assert _detail_json(SR_URL) == {"id": "1"}


# --- _detail_html: same taxonomy --------------------------------------------

@pytest.mark.parametrize("status,expected", [
    (404, None), (410, None), (429, DETAIL_RATE_LIMITED),
    (500, DETAIL_TRANSIENT), (503, DETAIL_TRANSIENT), (403, DETAIL_TRANSIENT),
])
def test_detail_html_maps_status_to_the_same_taxonomy(monkeypatch, status, expected):
    monkeypatch.setattr(ats_sources, "_http_get", _responder(_Resp(status)))
    assert _detail_html(TT_URL) is expected


def test_detail_html_transport_exception_is_transient(monkeypatch):
    monkeypatch.setattr(ats_sources, "_http_get", _responder(requests.Timeout("t")))
    assert _detail_html(TT_URL) is DETAIL_TRANSIENT


def test_detail_html_200_returns_the_body(monkeypatch):
    monkeypatch.setattr(ats_sources, "_http_get",
                        _responder(_Resp(200, text="<html>hi</html>")))
    assert _detail_html(TT_URL) == "<html>hi</html>"


# --- the fetchers forward the signal instead of flattening it to None -------

@pytest.mark.parametrize("signal", [DETAIL_TRANSIENT, DETAIL_RATE_LIMITED])
def test_smartrecruiters_forwards_the_signal(monkeypatch, signal):
    monkeypatch.setattr(ats_sources, "_detail_json", lambda url, **kw: signal)
    assert fetch_smartrecruiters_detail(SR_URL) is signal


@pytest.mark.parametrize("signal", [DETAIL_TRANSIENT, DETAIL_RATE_LIMITED])
def test_headhunter_forwards_the_signal(monkeypatch, signal):
    error = (ats_sources.hh_api.HHRateLimited if signal is DETAIL_RATE_LIMITED
             else ats_sources.hh_api.HHError)("transport")
    monkeypatch.setattr(
        ats_sources.hh_api,
        "fetch_vacancy_detail",
        lambda vacancy_id: (_ for _ in ()).throw(error),
    )
    assert fetch_headhunter_detail(HH_URL) is signal


@pytest.mark.parametrize("signal", [DETAIL_TRANSIENT, DETAIL_RATE_LIMITED])
def test_teamtailor_forwards_the_signal(monkeypatch, signal):
    monkeypatch.setattr(ats_sources, "_detail_html", lambda url, **kw: signal)
    assert fetch_teamtailor_detail(TT_URL) is signal


# --- end to end: HTTP status -> persisted backfill state --------------------
# These use the REAL fetchers (fetchers=None -> FETCHERS), so they prove the
# whole chain rather than the mapping of one layer in isolation.

@pytest.mark.parametrize("status,expected", [
    (404, "unavailable"),
    (410, "unavailable"),
    (429, "failed"),
    (500, "failed"),
    (503, "failed"),
    (403, "failed"),
])
def test_http_status_maps_end_to_end_to_the_backfill_state(monkeypatch, status, expected):
    monkeypatch.setattr(ats_sources, "_http_get", _responder(_Resp(status)))
    report = backfill([_row()], budget=10)
    assert [r.state for r in report.results] == [expected]


def test_a_timeout_maps_end_to_end_to_failed(monkeypatch):
    monkeypatch.setattr(ats_sources, "_http_get", _responder(requests.Timeout("t")))
    report = backfill([_row()], budget=10)
    assert report.failed == 1
    assert report.unavailable == 0
    assert [r.state for r in report.results] == ["failed"]


def test_a_200_carrying_no_job_text_maps_end_to_end_to_unavailable(monkeypatch):
    """The one non-status route into the terminal bucket: the endpoint answered
    properly and the answer contains no job description."""
    monkeypatch.setattr(ats_sources, "_http_get",
                        _responder(_Resp(200, payload={"id": "1"})))
    report = backfill([_row()], budget=10)
    assert [r.state for r in report.results] == ["unavailable"]


def test_a_200_with_real_text_maps_end_to_end_to_ok(monkeypatch):
    """Discrimination control: the same harness still produces a success, so
    the failure mappings above are not an artefact of a broken fixture."""
    monkeypatch.setattr(ats_sources, "_http_get",
                        _responder(_Resp(200, payload=SR_OK_PAYLOAD)))
    report = backfill([_row()], budget=10)
    assert [r.state for r in report.results] == ["ok"]
    assert report.filled == 1


# --- 429 closes the source for the rest of the run --------------------------

def test_a_429_closes_that_source_for_the_rest_of_the_run():
    """400 sequential requests into a source that just said 'stop' is what
    turns one 429 into a burst of them."""
    calls = []

    def rate_limited(url):
        calls.append(url)
        return DETAIL_RATE_LIMITED

    rows = [_row(url=f"https://x/{i}") for i in range(5)]
    report = backfill(rows, budget=10, fetchers={"smartrecruiters": rate_limited})

    assert len(calls) == 1, "only the first row may be attempted"
    assert report.attempted == 1
    assert report.failed == 1
    assert report.rate_limited == 4
    assert [r.state for r in report.results] == ["failed"], (
        "the four skipped rows must produce NO result, so the sweep writes no "
        "state for them and they stay eligible")
    assert report.per_source["smartrecruiters"]["rate_limited"] == 4


def test_closing_one_source_does_not_close_the_others():
    """Local, not a general circuit breaker: headhunter rate-limiting us says
    nothing about smartrecruiters."""
    rows = [_row(source="headhunter", url="https://hh.ru/vacancy/1"),
            _row(source="headhunter", url="https://hh.ru/vacancy/2"),
            _row(source="smartrecruiters", url=SR_URL)]
    report = backfill(rows, budget=10, fetchers={
        "headhunter": lambda url: DETAIL_RATE_LIMITED,
        "smartrecruiters": lambda url: LONG_TEXT,
    })
    assert report.per_source["headhunter"]["attempted"] == 1
    assert report.per_source["headhunter"]["rate_limited"] == 1
    assert report.per_source["smartrecruiters"]["filled"] == 1


def test_a_plain_transient_signal_does_not_close_the_source():
    """A 502 on one posting is not a request to back off the whole source."""
    calls = []

    def transient(url):
        calls.append(url)
        return DETAIL_TRANSIENT

    rows = [_row(url=f"https://x/{i}") for i in range(3)]
    report = backfill(rows, budget=10, fetchers={"smartrecruiters": transient})
    assert len(calls) == 3
    assert report.failed == 3
    assert report.rate_limited == 0
    assert report.unavailable == 0


# --- the never-raise contract ----------------------------------------------

@pytest.mark.parametrize("payload", [
    {"jobAd": ["a", "b"]},
    {"jobAd": "a string"},
    {"jobAd": 7},
    {"jobAd": {"sections": ["not", "a", "dict"]}},
    {"jobAd": {"sections": "nope"}},
])
def test_a_malformed_job_ad_is_permanent_and_never_raises(monkeypatch, payload):
    """`(payload.get("jobAd") or {}).get("sections")` raised AttributeError on
    a truthy non-dict jobAd. Containment made that survivable but filed it as
    `failed`; an isinstance guard makes it correct."""
    monkeypatch.setattr(ats_sources, "_detail_json", lambda url, **kw: payload)
    assert fetch_smartrecruiters_detail(SR_URL) is None


@pytest.mark.parametrize("unaddressable", [
    "https://jobs.smartrecruiters.com/Wise/744000142223879",
    "https://jobs.smartrecruiters.com/Wise/744000142223879#dup:12",
    "https://x/1",
    "",
])
def test_smartrecruiters_refuses_an_unaddressable_url_without_a_request(
        monkeypatch, unaddressable):
    """Pre-M3 such a URL returned HTML, resp.json() raised, and the row was
    retired as `unavailable` -- wrong bucket, but bounded. Post-M3 an unparseable
    200 is transient, so without this gate the row would be re-fetched on every
    sweep for ever, and rows_needing_text has no ORDER BY, so it would be the
    same rows each night, starving the ones that can be filled."""
    calls = []
    monkeypatch.setattr(ats_sources, "_http_get",
                        lambda url, **kw: calls.append(url) or _Resp(200, text="<html/>"))
    assert fetch_smartrecruiters_detail(unaddressable) is None
    assert calls == [], "no request may be issued for a URL we cannot address"


def test_smartrecruiters_accepts_the_ref_url_shape_the_listing_stores(monkeypatch):
    """Discrimination control for the gate. This is the verified real shape of a
    posting's `ref` field (checked live against api.smartrecruiters.com), which is
    what fetch_smartrecruiters puts in vacancies.url on the API path -- so the
    gate must not reject the rows the sweep actually holds."""
    monkeypatch.setattr(ats_sources, "_http_get",
                        _responder(_Resp(200, payload=SR_OK_PAYLOAD)))
    text = fetch_smartrecruiters_detail(
        "https://api.smartrecruiters.com/v1/companies/Wise/postings/744000142223879")
    assert "own the pricing and incentive structure" in text


def test_a_non_string_section_text_is_skipped_not_repr_dumped():
    """`str(node.get("text") or "")` turned a dict or list section body into a
    Python repr and joined it into the stored description -- quiet data
    pollution that then gets scored and shown to a human."""
    payload = {"jobAd": {"sections": {
        "jobDescription": {"text": {"nested": "surprise"}},
        "qualifications": {"text": ["a", "list"]},
        "additionalInformation": {"text": LONG_TEXT},
    }}}
    import job_intel.ats_sources as m
    original = m._detail_json
    m._detail_json = lambda url, **kw: payload
    try:
        text = fetch_smartrecruiters_detail(SR_URL)
    finally:
        m._detail_json = original
    assert "nested" not in text
    assert "surprise" not in text
    assert "{" not in text and "[" not in text
    assert "You will own the pricing" in text


@pytest.mark.parametrize("hostile", [
    _Resp(200, json_error=ValueError("no JSON")),
    _Resp(200, payload={"jobAd": ["a"]}),
    _Resp(200, payload="a bare string"),
    _Resp(999),
    object(),                      # no .status_code at all
    requests.Timeout("t"),
    RuntimeError("bug"),
])
def test_no_hostile_transport_outcome_escapes_backfill(monkeypatch, hostile):
    """Whatever the transport does, backfill() returns a report and the row
    lands in a bucket. Nothing propagates into the daily run."""
    monkeypatch.setattr(ats_sources, "_http_get", _responder(hostile))
    report = backfill([_row()], budget=10)
    assert report.attempted == 1
    assert [r.state for r in report.results][0] in {"failed", "unavailable"}


def test_a_fetcher_returning_a_junk_object_is_failed_not_terminal():
    """Defensive: a fetcher that violates its contract is a bug, and a bug
    must not write irreversible terminal state."""
    report = backfill([_row()], budget=10,
                      fetchers={"smartrecruiters": lambda url: {"unexpected": True}})
    assert report.failed == 1
    assert report.unavailable == 0
    assert report.results[0].state == "failed"
    assert report.results[0].text is None


def test_a_signal_never_reaches_the_persisted_text():
    """The sentinels are truthy objects. If backfill() checked them after the
    length test rather than before, len(signal.strip()) would raise; if it
    checked truthiness only, a signal would be stored as a description."""
    report = backfill([_row()], budget=10,
                      fetchers={"smartrecruiters": lambda url: DETAIL_TRANSIENT})
    assert report.results[0].text is None
    assert report.results[0].state == "failed"


# --- politeness delay -------------------------------------------------------

def test_the_documented_default_delay_is_half_a_second():
    assert DETAIL_REQUEST_DELAY_SECONDS == 0.5


def test_each_detail_request_is_preceded_by_the_default_delay(monkeypatch):
    slept = []
    monkeypatch.delenv("JOB_INTEL_TEXT_BACKFILL_DELAY_SECONDS", raising=False)
    monkeypatch.setattr(ats_sources.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(ats_sources, "_http_get",
                        _responder(_Resp(200, payload={"id": "1"})))
    _detail_json(SR_URL)
    _detail_json(SR_URL)
    assert slept == [DETAIL_REQUEST_DELAY_SECONDS, DETAIL_REQUEST_DELAY_SECONDS]


def test_the_html_path_is_delayed_too(monkeypatch):
    slept = []
    monkeypatch.delenv("JOB_INTEL_TEXT_BACKFILL_DELAY_SECONDS", raising=False)
    monkeypatch.setattr(ats_sources.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(ats_sources, "_http_get", _responder(_Resp(200, text="<html/>")))
    _detail_html(TT_URL)
    assert slept == [DETAIL_REQUEST_DELAY_SECONDS]


@pytest.mark.parametrize("raw,expected", [
    ("0.25", [0.25]),
    ("2", [2.0]),
    ("0", []),                      # explicit opt-out: no sleep call at all
    ("abc", [DETAIL_REQUEST_DELAY_SECONDS]),
    ("", [DETAIL_REQUEST_DELAY_SECONDS]),
    ("   ", [DETAIL_REQUEST_DELAY_SECONDS]),
    ("-1", [DETAIL_REQUEST_DELAY_SECONDS]),
    ("nan", [DETAIL_REQUEST_DELAY_SECONDS]),
    ("999999", [60.0]),             # capped: a typo must not stall the run
    ("inf", [60.0]),
])
def test_the_delay_is_configurable_and_never_raises(monkeypatch, raw, expected):
    slept = []
    monkeypatch.setenv("JOB_INTEL_TEXT_BACKFILL_DELAY_SECONDS", raw)
    monkeypatch.setattr(ats_sources.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(ats_sources, "_http_get",
                        _responder(_Resp(200, payload={"id": "1"})))
    assert _detail_json(SR_URL) == {"id": "1"}
    assert slept == expected
