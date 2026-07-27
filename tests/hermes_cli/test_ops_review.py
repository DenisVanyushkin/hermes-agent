from hermes_cli.ops_review import (
    OPS_HARD_FINDING_TYPES,
    has_blocking_ops_finding,
    render_ops_review_block,
)


def test_review_block_shows_argv_and_the_original_request():
    block = render_ops_review_block(
        [{"op_id": "git_push", "risk": "mutate", "argv": ["git", "push", "origin", "main"],
          "description": "опубликовать main", "irreversible": None}],
        "запушь текущую ветку в origin",
    )
    assert "git push origin main" in block
    # Ревьюер обязан видеть дословный запрос: пересказ -- то место, где план расширяется.
    assert "запушь текущую ветку в origin" in block


def test_hard_finding_blocks_regardless_of_severity():
    assert has_blocking_ops_finding([{"type": "ops_not_requested", "severity": "low"}]) is True


def test_ordinary_finding_does_not_block_by_itself():
    assert has_blocking_ops_finding([{"type": "style", "severity": "high"}]) is False


def test_every_hard_type_is_actually_hard():
    for finding_type in OPS_HARD_FINDING_TYPES:
        assert has_blocking_ops_finding([{"type": finding_type, "severity": "info"}]) is True
