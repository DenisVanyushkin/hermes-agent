import pytest

from hermes_cli.run_evidence import (
    PromiseItem,
    ReproductionRecord,
    render_promise_block,
    render_reproduction_block,
    unaccounted_promises,
)


def test_block_states_where_the_command_actually_ran():
    # Точный случай 2026-07-28: EXIT=0 получен в песочнице, где браузерного
    # окружения нет, и поэтому ничего не воспроизводил.
    block = render_reproduction_block(
        ReproductionRecord(
            command="python3 -m job_intel doctor",
            ran_on="sandbox",
            observed="EXIT=0",
            reproduced=False,
        )
    )

    assert "в песочнице" in block
    assert "python3 -m job_intel doctor" in block
    assert "сбой не воспроизведён" in block


def test_host_and_sandbox_are_distinguishable_in_the_rendered_block():
    on_host = render_reproduction_block(
        ReproductionRecord(command="c", ran_on="host", observed="o", reproduced=True)
    )
    assert "на хосте" in on_host
    assert "сбой воспроизведён" in on_host


def test_an_ambiguous_result_is_said_so_rather_than_rounded():
    block = render_reproduction_block(
        ReproductionRecord(command="c", ran_on="host", observed="таймаут", reproduced=None)
    )
    assert "неоднозначен" in block


def test_absent_record_is_stated_not_omitted():
    assert "не выполнялось" in render_reproduction_block(None)


@pytest.mark.parametrize("ran_on", ["somewhere", "", "HOST", None])
def test_ran_on_accepts_only_host_or_sandbox(ran_on):
    with pytest.raises(ValueError, match="invalid_ran_on"):
        ReproductionRecord(command="c", ran_on=ran_on, observed="o", reproduced=True)


def test_item_without_an_outcome_is_unaccounted():
    items = [
        PromiseItem(text="оставить nightly doctor в fast-mode", outcome="done"),
        PromiseItem(text="починить browser desktop", outcome=None),
    ]

    assert [i.text for i in unaccounted_promises(items)] == ["починить browser desktop"]


def test_skipped_with_a_reason_is_accounted_for_and_is_not_a_failure():
    items = [PromiseItem(text="запускать doctor тем же venv", outcome="skipped", note="не нашёл")]

    assert unaccounted_promises(items) == []


@pytest.mark.parametrize("outcome", ["skipped", "changed"])
def test_a_non_done_outcome_without_a_reason_stays_unaccounted(outcome):
    assert len(unaccounted_promises([PromiseItem(text="починить CDP", outcome=outcome)])) == 1


def test_done_needs_no_reason():
    assert unaccounted_promises([PromiseItem(text="x", outcome="done")]) == []


def test_block_lists_every_item_with_its_outcome_and_reason():
    block = render_promise_block([
        PromiseItem(text="вынести live-проверку", outcome="changed", note="сделал враппером"),
        PromiseItem(text="починить CDP", outcome=None),
    ])

    assert "вынести live-проверку: сделано иначе — сделал враппером" in block
    assert "починить CDP: не отчитано" in block


def test_empty_list_renders_nothing_rather_than_an_empty_heading():
    assert render_promise_block([]) == ""


def test_outcome_rejects_unknown_values():
    with pytest.raises(ValueError, match="invalid_outcome"):
        PromiseItem(text="x", outcome="probably")
