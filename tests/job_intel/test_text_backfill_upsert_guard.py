"""An upsert must not erase a description it cannot replace.

`upsert_vacancy`'s UPDATE branch wrote `description = ?` unconditionally. Since
Task 1, `_vacancy()` yields "" for smartrecruiters/headhunter/teamtailor,
because their listing endpoint carries no description at all — the text lives
behind a second request. So a row the sweep had filled with 3000 characters,
reappearing in a daily listing and falling outside that run's budget or
priority tail, had its recovered text overwritten with "".

That loss was unrecoverable: `rows_needing_text` admits only
`text_backfill_state IS NULL OR = 'failed'`, and the row's state is 'ok'. It
was also self-perpetuating — the description flipped between "" and real text
on every run, and each flip is a fresh `description_hash` change, i.e. a fresh
`material_change`, which turns the accepted one-time re-notification burst
(see test_text_backfill_notification_guard.py) into a permanent rattle.
"""
from __future__ import annotations

import pytest

from job_intel.models import Vacancy
from job_intel.store import JobIntelStore

REAL_TEXT = "You will own the P&L for the payments line. " + "x" * 300
KEY = "https://x/1"


@pytest.fixture()
def store(tmp_path):
    s = JobIntelStore(str(tmp_path / "t.sqlite3"))
    s.bootstrap()
    return s


def _vacancy(description, *, title="Head of Product", company="Acme",
             source="smartrecruiters", url=KEY):
    return Vacancy(source=source, source_id="a", company=company, title=title,
                   location="Remote", url=url, description=description)


def _stored(store, vacancy_id):
    with store.connect(read_only=True) as conn:
        row = conn.execute(
            "SELECT description, title, company, repost_count, text_backfill_state "
            "FROM vacancies WHERE id = ?", (vacancy_id,)).fetchone()
    return {"description": row[0], "title": row[1], "company": row[2],
            "repost_count": row[3], "text_backfill_state": row[4]}


# --- the production scenario, end to end ------------------------------------

def test_a_relisted_vacancy_does_not_lose_its_backfilled_text(store):
    """The exact M2 sequence: sweep fills the row, the row comes back in a
    listing that has no description, and the daily run upserts it."""
    vacancy_id = store.upsert_vacancy(_vacancy(""), KEY)
    store.record_text_backfill(vacancy_id, "ok", REAL_TEXT)
    assert _stored(store, vacancy_id)["description"] == REAL_TEXT

    # The listing returns the same vacancy with no description, as all three
    # backfill sources always do.
    store.upsert_vacancy(_vacancy(""), KEY)

    after = _stored(store, vacancy_id)
    assert after["description"] == REAL_TEXT
    # Still 'ok', which is exactly why losing the text was terminal:
    # rows_needing_text would never have offered this row again.
    assert after["text_backfill_state"] == "ok"
    assert store.rows_needing_text(["smartrecruiters"], limit=10) == []


def test_the_refusal_does_not_freeze_the_rest_of_the_row(store):
    """Narrowness check: refusing the empty description must not turn the whole
    UPDATE into a no-op. Everything else still tracks the listing."""
    vacancy_id = store.upsert_vacancy(_vacancy(REAL_TEXT), KEY)
    before = _stored(store, vacancy_id)

    store.upsert_vacancy(
        _vacancy("", title="Group Head of Product", company="Acme GmbH"), KEY)

    after = _stored(store, vacancy_id)
    assert after["description"] == REAL_TEXT
    assert after["title"] == "Group Head of Product"
    assert after["company"] == "Acme GmbH"
    assert after["repost_count"] == before["repost_count"] + 1


# --- what "empty" means -----------------------------------------------------

def test_an_empty_incoming_description_does_not_clear_stored_text(store):
    vacancy_id = store.upsert_vacancy(_vacancy(REAL_TEXT), KEY)
    store.upsert_vacancy(_vacancy(""), KEY)
    assert _stored(store, vacancy_id)["description"] == REAL_TEXT


@pytest.mark.parametrize("blank", ["", "   ", "\n", " \n\t ", "\u00a0"])
def test_a_blank_incoming_description_does_not_clear_stored_text(store, blank):
    """"Empty" is empty-after-strip. A description of "  \\n " carries exactly
    as much information as "", and treating them differently would leave a
    second, narrower version of this same bug.

    None is deliberately NOT a case here, on either side. Incoming: models.py:17
    declares `description: str`, so pydantic rejects None at Vacancy
    construction. Stored: the vacancies.description column is NOT NULL
    (verified — PRAGMA table_info reports notnull=1, and forcing
    `SET description = NULL` raises IntegrityError). The `or ""` on both values
    in the fix is therefore unreachable defence against a schema change, not a
    live path, and no test can honestly exercise it.
    """
    vacancy_id = store.upsert_vacancy(_vacancy(REAL_TEXT), KEY)
    store.upsert_vacancy(_vacancy(blank), KEY)
    assert _stored(store, vacancy_id)["description"] == REAL_TEXT


# --- what is deliberately NOT protected -------------------------------------

def test_a_genuine_non_empty_update_still_applies(store):
    """The guard must not become a high-water mark. This method serves every
    source, most of which do carry a real description."""
    vacancy_id = store.upsert_vacancy(_vacancy("The original posting text."), KEY)
    store.upsert_vacancy(_vacancy(REAL_TEXT), KEY)
    assert _stored(store, vacancy_id)["description"] == REAL_TEXT


def test_a_shorter_non_empty_description_still_applies(store):
    """Deliberate scope decision, not an oversight: only EMPTY is refused.

    A shorter non-empty description is indistinguishable from a genuine
    content change (an employer trimming a posting, a re-post carrying a
    summary), and the store is a mirror of the source rather than the longest
    text ever seen. The M2 hole is closed completely by the empty guard alone,
    because these three sources' listings yield exactly "" — never a short
    non-empty string.
    """
    vacancy_id = store.upsert_vacancy(_vacancy(REAL_TEXT), KEY)
    store.upsert_vacancy(_vacancy("Shorter but real."), KEY)
    assert _stored(store, vacancy_id)["description"] == "Shorter but real."


def test_an_empty_description_is_still_stored_as_empty_on_first_insert(store):
    """The INSERT path must keep Task 1's behaviour exactly. If the guard
    leaked into it, "no text" would become unrepresentable and needs_text()
    would never fire — the entire feature depends on an absent description
    being stored as absent.
    """
    vacancy_id = store.upsert_vacancy(_vacancy(""), KEY)
    assert _stored(store, vacancy_id)["description"] == ""
    rows = store.rows_needing_text(["smartrecruiters"], limit=10)
    assert [r["id"] for r in rows] == [vacancy_id]


def test_an_empty_over_an_empty_is_still_empty(store):
    """No stored text to protect: nothing changes, and the row stays eligible."""
    vacancy_id = store.upsert_vacancy(_vacancy(""), KEY)
    store.upsert_vacancy(_vacancy(""), KEY)
    assert _stored(store, vacancy_id)["description"] == ""
    assert [r["id"] for r in store.rows_needing_text(["smartrecruiters"], limit=10)] \
        == [vacancy_id]


def test_the_guard_applies_to_every_source_not_only_the_backfill_three(store):
    """The defect is in a shared production method. A greenhouse row whose
    listing momentarily returns no description must not lose its text either."""
    key = "https://boards.greenhouse.io/acme/jobs/1"
    vacancy_id = store.upsert_vacancy(
        _vacancy(REAL_TEXT, source="greenhouse", url=key), key)
    store.upsert_vacancy(_vacancy("", source="greenhouse", url=key), key)
    assert _stored(store, vacancy_id)["description"] == REAL_TEXT
