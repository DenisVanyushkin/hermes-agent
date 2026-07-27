"""Task 9: сообщение операторского гейта для плана операций."""

from pathlib import Path

from hermes_cli.ops_gate_message import render_ops_approval_message, resolve_operation_cwd


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


def test_empty_repo_path_still_renders_a_concrete_directory():
    """Пустой cwd в сообщении = операция «где-то»; исполнение при этом всё равно
    произойдёт (интерцепт берёт тот же фолбэк), поэтому показывать надо его."""
    text = render_ops_approval_message({
        "repo_path": "",
        "original_task": "запушь ветку",
        "plan": [{
            "op_id": "git_push", "risk": "mutate",
            "argv": ["git", "push", "origin", "main"],
            "description": "опубликовать main", "irreversible": None,
        }],
    })

    fallback = resolve_operation_cwd("")
    assert "cwd:  \n" not in text and not text.rstrip().endswith("cwd:")
    assert f"cwd:  {fallback}" in text
    assert Path(fallback).is_absolute()
    # Фолбэк -- корень репозитория, тот же, что подставит интерцепт.
    assert (Path(fallback) / "hermes_cli" / "ops_gate_message.py").exists()


def test_resolve_operation_cwd_prefers_the_recorded_path():
    assert resolve_operation_cwd("/repo/x") == "/repo/x"
    assert resolve_operation_cwd(None) == resolve_operation_cwd("")
