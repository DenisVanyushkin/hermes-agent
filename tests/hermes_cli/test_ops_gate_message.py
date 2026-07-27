"""Task 9: сообщение операторского гейта для плана операций."""

from hermes_cli.ops_gate_message import render_ops_approval_message


def test_message_shows_argv_cwd_and_the_original_request():
    text = render_ops_approval_message({
        "repo_path": "/home/hermes/.hermes/hermes-agent",
        "original_task": "запушь текущую ветку в origin",
        "plan": [{
            "op_id": "git_push", "risk": "mutate",
            "argv": ["git", "push", "origin", "local/customizations"],
            "description": "опубликовать local/customizations", "irreversible": None,
        }],
    })
    assert "git push origin local/customizations" in text
    assert "/home/hermes/.hermes/hermes-agent" in text
    assert "запушь текущую ветку в origin" in text
    assert "выполни" in text


def test_destroy_plan_asks_for_the_operation_id_not_a_bare_yes():
    text = render_ops_approval_message({
        "repo_path": "/repo",
        "original_task": "удали ветку",
        "plan": [{
            "op_id": "git_branch_delete", "risk": "destroy",
            "argv": ["git", "branch", "-D", "old"],
            "description": "удалить ветку old",
            "irreversible": "незамердженные коммиты останутся только в reflog",
        }],
    })
    assert "подтверждаю git_branch_delete" in text
    assert "незамердженные коммиты" in text
