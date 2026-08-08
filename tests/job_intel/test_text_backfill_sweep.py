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
def _insert(store, url="https://x/1"):
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
