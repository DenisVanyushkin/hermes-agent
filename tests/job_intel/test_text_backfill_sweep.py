"""The sweep exists to improve the corpus. It must not be able to reach a user."""
from datetime import datetime, timedelta, timezone

import pytest

from job_intel.models import Vacancy
from job_intel.store import JobIntelStore

from scripts.job_intel_text_backfill_sweep import sweep


@pytest.fixture()
def store(tmp_path):
    s = JobIntelStore(str(tmp_path / "t.sqlite3"))
    s.bootstrap()
    return s


# No start_run() here: upsert_vacancy() takes a dedup key, not a run id, and
# the vacancies table has no run_id column at all. `url` doubles as the
# vacancy_key below (unique per call site), not a mirror of production, which
# derives the key via canonical_vacancy_key.
#
# upsert_vacancy() stamps last_seen_at to now on insert. The sweep excludes
# anything seen within SEEN_RECENTLY_DAYS (2) -- that's the live branch's
# territory, not the sweep's. A fixture row with a fresh last_seen_at would
# never be selected, so every fixture here is backdated past that window.
# The verified real shape of a posting's `ref` field, which is what
# fetch_smartrecruiters puts in vacancies.url on the API path -- and the only
# shape fetch_smartrecruiters_detail will address. Tests that drive the REAL
# fetcher must use it, or they exercise the URL gate rather than the transport
# taxonomy they mean to test. Tests passing an explicit `fetchers=` stub bypass
# the fetcher entirely and do not care.
def _sr_url(n: int = 1) -> str:
    return f"https://api.smartrecruiters.com/v1/companies/Acme/postings/74400014222{n:04d}"


def _insert(store, url=None):
    url = url or _sr_url(1)
    vid = store.upsert_vacancy(Vacancy(
        source="smartrecruiters", source_id="a", company="Acme",
        title="Head of Product", location="Remote", url=url, description=""), url)
    old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    with store.connect() as conn:
        conn.execute("UPDATE vacancies SET last_seen_at = ? WHERE id = ?", (old, vid))
        conn.commit()
    return vid


def test_sweep_fills_the_description(store):
    vid = _insert(store)
    sweep(store, budget=10, fetchers={"smartrecruiters": lambda url: "y" * 400})
    with store.connect(read_only=True) as conn:
        assert conn.execute("SELECT description FROM vacancies WHERE id=?",
                            (vid,)).fetchone()[0] == "y" * 400


def test_sweep_creates_no_notification(store):
    _insert(store)
    sweep(store, budget=10, fetchers={"smartrecruiters": lambda url: "y" * 400})
    with store.connect(read_only=True) as conn:
        assert conn.execute("SELECT count(*) FROM notifications").fetchone()[0] == 0


def test_sweep_creates_no_observability_row(store):
    _insert(store)
    sweep(store, budget=10, fetchers={"smartrecruiters": lambda url: "y" * 400})
    with store.connect(read_only=True) as conn:
        assert conn.execute(
            "SELECT count(*) FROM vacancy_observability").fetchone()[0] == 0


def test_sweep_module_does_not_import_scoring_or_delivery():
    """Structural, not behavioural: the sweep cannot notify because it has no
    way to. A boolean parameter would eventually be passed the wrong value."""
    import scripts.job_intel_text_backfill_sweep as mod

    source = open(mod.__file__).read()
    for forbidden in ("score_vacancy", "_deliver", "create_notification",
                      "send_message", "digest"):
        assert forbidden not in source, forbidden


def test_sweep_records_unavailable_and_does_not_retry_it(store):
    vid = _insert(store)
    # Evidence the fixture is genuinely eligible before the sweep touches it --
    # otherwise a later assert of attempted == 0 would pass for the wrong
    # reason (never selected) rather than because 'unavailable' is terminal.
    eligible = store.rows_needing_text(["smartrecruiters"], limit=10,
                                       exclude_seen_since_days=2)
    assert any(r["id"] == vid for r in eligible)

    sweep(store, budget=10, fetchers={"smartrecruiters": lambda url: None})
    with store.connect(read_only=True) as conn:
        assert conn.execute("SELECT text_backfill_state FROM vacancies WHERE id=?",
                            (vid,)).fetchone()[0] == "unavailable"
    second = sweep(store, budget=10, fetchers={"smartrecruiters": lambda url: "y" * 400})
    assert second.attempted == 0


# --- Fix round 1: the printed counter must never claim a drained backlog ---

def test_sweep_reports_more_eligible_when_backlog_exceeds_budget(store):
    for i in range(3):
        _insert(store, url=f"https://x/e{i}")
    report = sweep(store, budget=2, fetchers={"smartrecruiters": lambda url: "y" * 400})
    assert report.attempted == 2
    assert report.more_eligible is True


def test_sweep_reports_no_more_eligible_when_backlog_within_budget(store):
    _insert(store)
    report = sweep(store, budget=10, fetchers={"smartrecruiters": lambda url: "y" * 400})
    assert report.more_eligible is False


def test_sweep_probe_row_is_not_fetched_or_persisted(store):
    """The (budget+1)th row exists only to answer 'is there more?' -- it must
    never reach a fetcher and must never be written to."""
    for i in range(3):
        _insert(store, url=f"https://x/p{i}")
    calls = []

    def fetcher(url):
        calls.append(url)
        return "y" * 400

    report = sweep(store, budget=2, fetchers={"smartrecruiters": fetcher})
    assert len(calls) == 2  # the third row was never handed to a fetcher
    assert report.more_eligible is True
    with store.connect(read_only=True) as conn:
        untouched = conn.execute(
            "SELECT count(*) FROM vacancies WHERE text_backfill_state IS NULL"
        ).fetchone()[0]
    assert untouched == 1  # exactly the probe row was left untouched


# --- Fix round 1: per-row persistence isolation ---

def test_sweep_persists_later_rows_after_one_persistence_failure(store, monkeypatch):
    ok_id = _insert(store, url="https://x/ok")
    bad_id = _insert(store, url="https://x/bad")
    original = store.record_text_backfill
    seen = []

    def flaky(vacancy_id, state, description):
        seen.append(vacancy_id)
        if vacancy_id == bad_id:
            raise RuntimeError("transient db error")
        return original(vacancy_id, state, description)

    monkeypatch.setattr(store, "record_text_backfill", flaky)

    # Must not raise: a persistence failure on one row must not abort the loop.
    sweep(store, budget=10, fetchers={"smartrecruiters": lambda url: "y" * 400})

    assert set(seen) == {ok_id, bad_id}  # both rows were attempted
    with store.connect(read_only=True) as conn:
        row = conn.execute("SELECT description, text_backfill_state "
                           "FROM vacancies WHERE id=?", (ok_id,)).fetchone()
    assert row[0] == "y" * 400
    assert row[1] == "ok"


# --- the taxonomy, as persisted -------------------------------------------
# These drive the REAL fetchers from a stubbed HTTP layer, so they prove the
# whole chain: status code -> detail helper -> fetcher -> backfill state ->
# the row's text_backfill_state on disk -> whether it is ever offered again.
# This is the path that writes irreversible state into the production DB.

class _Resp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.headers = {}
        self.text = ""
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture()
def no_delay(monkeypatch):
    monkeypatch.setenv("JOB_INTEL_TEXT_BACKFILL_DELAY_SECONDS", "0")


def _serve(monkeypatch, resp):
    import job_intel.ats_sources as ats
    monkeypatch.setattr(ats, "_http_get", lambda url, **kw: resp)


def _state(store, vid):
    with store.connect(read_only=True) as conn:
        return conn.execute("SELECT text_backfill_state FROM vacancies WHERE id=?",
                            (vid,)).fetchone()[0]


def test_a_rate_limited_row_stays_eligible_and_is_filled_on_the_next_sweep(
        store, monkeypatch, no_delay):
    """The defect this closes: a 429 used to write the TERMINAL `unavailable`
    state, so one rate-limit burst against a source permanently retired up to
    `budget` rows from ever being backfilled again."""
    vid = _insert(store)
    _serve(monkeypatch, _Resp(429))

    first = sweep(store, budget=10)
    assert first.failed == 1
    assert first.unavailable == 0
    assert _state(store, vid) == "failed"
    assert [r["id"] for r in store.rows_needing_text(
        ["smartrecruiters"], limit=10, exclude_seen_since_days=2)] == [vid]

    # The outage passes. The row is recovered rather than lost.
    second = sweep(store, budget=10, fetchers={"smartrecruiters": lambda url: "y" * 400})
    assert second.filled == 1
    with store.connect(read_only=True) as conn:
        row = conn.execute("SELECT description, text_backfill_state FROM vacancies "
                           "WHERE id=?", (vid,)).fetchone()
    assert row[0] == "y" * 400
    assert row[1] == "ok"


@pytest.mark.parametrize("status", [404, 410])
def test_a_genuinely_absent_posting_is_still_retired(store, monkeypatch, no_delay,
                                                     status):
    """The other direction, and why the taxonomy has to be a distinction rather
    than "retry everything": a 404 does not become a 200, so retrying it every
    night forever would burn the budget that eligible rows need."""
    vid = _insert(store)
    _serve(monkeypatch, _Resp(status))

    report = sweep(store, budget=10)
    assert report.unavailable == 1
    assert report.failed == 0
    assert _state(store, vid) == "unavailable"
    assert store.rows_needing_text(["smartrecruiters"], limit=10,
                                   exclude_seen_since_days=2) == []


def test_a_server_error_is_retryable_not_terminal(store, monkeypatch, no_delay):
    vid = _insert(store)
    _serve(monkeypatch, _Resp(503))
    report = sweep(store, budget=10)
    assert report.failed == 1
    assert _state(store, vid) == "failed"


def test_rows_skipped_by_a_rate_limit_have_no_state_written_at_all(
        store, monkeypatch, no_delay):
    """The rows the sweep never got to must be indistinguishable from untouched:
    a persisted state for a row that was never even requested would be a verdict
    invented out of another row's failure."""
    first_id = _insert(store, url=_sr_url(1))
    second_id = _insert(store, url=_sr_url(2))
    _serve(monkeypatch, _Resp(429))

    report = sweep(store, budget=10)

    assert report.attempted == 1
    assert report.rate_limited == 1
    assert len(report.results) == 1
    attempted, skipped = ((first_id, second_id)
                          if _state(store, first_id) == "failed"
                          else (second_id, first_id))
    assert _state(store, attempted) == "failed"
    assert _state(store, skipped) is None
    # Both remain eligible: one as a recorded failure, one as untouched.
    eligible = {r["id"] for r in store.rows_needing_text(
        ["smartrecruiters"], limit=10, exclude_seen_since_days=2)}
    assert eligible == {first_id, second_id}


# --- Follow-up: priority must survive the sweep's own candidate pool, not
# just select() in isolation; never-attempted rows must not be crowded out
# by a low-id row that keeps failing. ---

def test_sweep_picks_priority_title_over_low_id_non_priority_rows(store):
    """Production finding: the first 78 eligible rows by rowid were all
    headhunter, low-priority titles -- a budget-5 sweep never reached a
    higher-id, higher-priority row at all. Seed several low-id
    non-priority rows, then one high-id executive+domain row, and confirm
    the fetcher is called for the high-id priority row, not any low-id
    one."""
    for i in range(5):
        _insert(store, url=f"https://x/low{i}")
    with store.connect() as conn:
        conn.execute(
            "UPDATE vacancies SET title = 'Warehouse Operative' "
            "WHERE url LIKE 'https://x/low%'")
        conn.commit()
    priority_id = _insert(store, url="https://x/priority")
    with store.connect() as conn:
        conn.execute(
            "UPDATE vacancies SET title = 'Head of Product Growth' "
            "WHERE id = ?", (priority_id,))
        conn.commit()

    calls = []

    def fetcher(url):
        calls.append(url)
        return "y" * 400

    sweep(store, budget=1, fetchers={"smartrecruiters": fetcher})
    assert calls == ["https://x/priority"]


def test_sweep_does_not_let_a_failed_low_id_row_crowd_out_never_attempted_rows(store):
    """Once a row gets its first attempt and flips to 'failed', it must not
    keep monopolizing every future run ahead of rows nobody has tried yet.
    All rows share the same title-priority bucket so priority alone cannot
    explain the outcome -- only the never-attempted-first rotation can."""
    failing_id = _insert(store, url="https://x/failing")
    higher_ids = [_insert(store, url=f"https://x/higher{i}") for i in range(3)]

    # First sweep: the low-id row fails and flips to 'failed'.
    sweep(store, budget=1, fetchers={"smartrecruiters": lambda url: None
                                      if url == "https://x/failing" else "y" * 400})
    with store.connect(read_only=True) as conn:
        state = conn.execute(
            "SELECT text_backfill_state FROM vacancies WHERE id = ?",
            (failing_id,)).fetchone()[0]
    assert state == "unavailable" or state == "failed"
    # Force it into 'failed' explicitly regardless of which terminal/
    # transient bucket the stub fetcher landed it in, so this test is about
    # ordering, not about the taxonomy already covered elsewhere.
    with store.connect() as conn:
        conn.execute("UPDATE vacancies SET text_backfill_state = 'failed' "
                     "WHERE id = ?", (failing_id,))
        conn.commit()

    calls = []

    def fetcher(url):
        calls.append(url)
        return "y" * 400

    second = sweep(store, budget=1, fetchers={"smartrecruiters": fetcher})
    assert len(calls) == 1
    assert calls[0] != "https://x/failing"
    assert second.attempted == 1
