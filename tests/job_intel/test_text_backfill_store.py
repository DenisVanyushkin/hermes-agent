"""Backfill state must be durable, or the sweep can never resume."""
import pytest

from job_intel.models import Vacancy
from job_intel.store import JobIntelStore


@pytest.fixture()
def store(tmp_path):
    s = JobIntelStore(str(tmp_path / "t.sqlite3"))
    s.bootstrap()
    return s


# No start_run() here: upsert_vacancy() takes a dedup key, not a run id, and
# the vacancies table has no run_id column at all. Do not "restore" a
# start_run call. `url` doubles as the vacancy_key below — a test convenience
# (unique per call site here), not a mirror of production, which derives the
# key via canonical_vacancy_key.
def _insert(store, *, title="Head of Product", source="smartrecruiters",
            description="", url="https://x/1"):
    v = Vacancy(source=source, source_id="a", company="Acme", title=title,
                location="Remote", url=url, description=description)
    return store.upsert_vacancy(v, url)


def test_bootstrap_adds_the_backfill_columns(store):
    with store.connect(read_only=True) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(vacancies)")}
    assert {"text_backfill_state", "text_backfill_at"} <= cols


def test_recording_ok_persists_the_description_and_the_state(store):
    vid = _insert(store)
    store.record_text_backfill(vid, "ok", "y" * 400)
    with store.connect(read_only=True) as conn:
        row = conn.execute(
            "SELECT description, text_backfill_state, text_backfill_at "
            "FROM vacancies WHERE id = ?", (vid,)).fetchone()
    assert row[0] == "y" * 400
    assert row[1] == "ok"
    assert row[2]


def test_recording_unavailable_leaves_the_description_alone(store):
    vid = _insert(store, description="original")
    store.record_text_backfill(vid, "unavailable", None)
    with store.connect(read_only=True) as conn:
        row = conn.execute("SELECT description, text_backfill_state FROM vacancies "
                           "WHERE id = ?", (vid,)).fetchone()
    assert row[0] == "original"
    assert row[1] == "unavailable"


def test_rows_needing_text_excludes_terminal_unavailable(store):
    vid = _insert(store)
    store.record_text_backfill(vid, "unavailable", None)
    assert store.rows_needing_text(["smartrecruiters"], limit=10) == []


def test_rows_needing_text_includes_a_previous_failure(store):
    vid = _insert(store)
    store.record_text_backfill(vid, "failed", None)
    assert [r["id"] for r in store.rows_needing_text(["smartrecruiters"], limit=10)] == [vid]


def test_rows_needing_text_respects_the_limit(store):
    for i in range(5):
        _insert(store, url=f"https://x/{i}")
    assert len(store.rows_needing_text(["smartrecruiters"], limit=2)) == 2
