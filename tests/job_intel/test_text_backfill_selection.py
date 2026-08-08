"""Selection is a budget, not a verdict on the title."""
from job_intel.text_backfill import BACKFILL_SOURCES, needs_text, select


def _row(title, description="", source="smartrecruiters", url="https://x/1"):
    return {"source": source, "title": title, "description": description, "url": url}


# --- needs_text --------------------------------------------------------------

def test_empty_description_needs_text():
    assert needs_text(_row("Head of Product", ""))


def test_description_equal_to_title_needs_text():
    assert needs_text(_row("Head of Product", "Head of Product"))


def test_short_description_needs_text():
    assert needs_text(_row("Head of Product", "x" * 199))


def test_real_description_does_not_need_text():
    assert not needs_text(_row("Head of Product", "y" * 200))


def test_source_without_a_fetcher_never_needs_text():
    assert not needs_text(_row("Head of Product", "", source="greenhouse"))


# --- select ------------------------------------------------------------------

def test_blocklisted_title_is_excluded():
    rows = [_row("Account Executive"), _row("Head of Product")]
    assert [r["title"] for r in select(rows, budget=10)] == ["Head of Product"]


def test_russian_title_is_kept():
    """A token gate would drop this for its language, not its content."""
    rows = [_row("Директор по продукту", source="headhunter")]
    assert len(select(rows, budget=10)) == 1


def test_budget_truncates():
    rows = [_row(f"Head of Product {i}") for i in range(10)]
    assert len(select(rows, budget=3)) == 3


def test_executive_titles_are_served_first_under_budget():
    rows = [_row("Warehouse Operative"), _row("Head of Product")]
    assert select(rows, budget=1)[0]["title"] == "Head of Product"


def test_rows_that_already_have_text_are_not_selected():
    assert select([_row("Head of Product", "y" * 400)], budget=10) == []


def test_backfill_sources_are_exactly_the_three_with_a_detail_api():
    assert BACKFILL_SOURCES == frozenset({"smartrecruiters", "headhunter", "teamtailor"})
