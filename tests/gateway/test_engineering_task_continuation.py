from __future__ import annotations

import hashlib
import importlib


def _module():
    return importlib.import_module("hermes_cli.engineering_task_context")


def _long_plan() -> str:
    prefix = "# Задача для инженера\n"
    suffix = (
        "\nPOST_4000_SENTINEL: процедура дисквалификации обязательна\n"
        "Если подтверждаешь — передам именно эту версию, а не предыдущий картонный скелет."
    )
    plan = prefix + ("И" * (13_089 - len(prefix) - len(suffix))) + suffix
    assert len(plan) == 13_089
    return plan


def test_short_execution_continuation_resolves_latest_complete_engineering_plan():
    module = _module()
    plan = _long_plan()
    assert len(plan) > 4000
    history = [
        {"role": "user", "content": "ок пиши план задача для инженера"},
        {"role": "assistant", "content": "# План для инженера\nпервая неполная версия"},
        {"role": "user", "content": "план слабый, добавь lifecycle источников"},
        *({"role": "tool", "content": f"search-{index}"} for index in range(8)),
        {"role": "assistant", "content": plan, "id": 93308},
    ]

    envelope = module.resolve_engineering_task_context(
        operator_instruction="ок, пусть инженер исполняет",
        history=history,
        session_id="20260813_094938_5a0c16b9",
        history_session_id="20260813_094938_5a0c16b9",
    )

    assert envelope.resolution_status == "resolved"
    assert envelope.source_kind == "approved_plan"
    assert envelope.task_text == plan
    assert "POST_4000_SENTINEL" in envelope.task_text
    assert envelope.operator_instruction == "ок, пусть инженер исполняет"
    assert envelope.source_session_id == "20260813_094938_5a0c16b9"
    assert envelope.source_message_id == "93308"
    assert envelope.task_sha256 == hashlib.sha256(plan.encode("utf-8")).hexdigest()


def test_parent_cron_quote_is_not_selected_as_the_approved_task():
    module = _module()
    plan = _long_plan()
    enriched = (
        '[Replying to: "Cronjob Response: idle-idea-prompt ... Следующие шаги: Создай"]\n\n'
        "ок, пусть инженер исполняет"
    )
    history = [
        {"role": "user", "content": "пиши план для инженера"},
        {"role": "assistant", "content": plan},
    ]

    envelope = module.resolve_engineering_task_context(
        operator_instruction="ок, пусть инженер исполняет",
        enriched_message=enriched,
        history=history,
        session_id="session-1",
        history_session_id="session-1",
    )

    assert envelope.task_text == plan
    assert "Cronjob Response" not in envelope.task_text
    assert envelope.operator_instruction == "ок, пусть инженер исполняет"


def test_direct_concrete_engineering_request_remains_the_task():
    module = _module()
    request = "Исправь parser.py: пустой title должен возвращать ValidationError"

    envelope = module.resolve_engineering_task_context(
        operator_instruction=request,
        history=[],
        session_id="session-2",
        history_session_id="session-2",
    )

    assert envelope.resolution_status == "resolved"
    assert envelope.source_kind == "direct_request"
    assert envelope.task_text == request


def test_continuation_without_a_qualifying_plan_fails_closed():
    module = _module()

    envelope = module.resolve_engineering_task_context(
        operator_instruction="пусть инженер исполняет",
        history=[{"role": "assistant", "content": "Обсудили погоду."}],
        session_id="session-3",
        history_session_id="session-3",
    )

    assert envelope.resolution_status == "missing_approved_plan"
    assert envelope.source_kind == "approved_plan"
    assert envelope.task_text is None
    assert envelope.task_sha256 is None


def test_oversized_approved_plan_is_rejected_instead_of_truncated():
    module = _module()
    plan = (
        "# Задача для инженера\n"
        + ("X" * module.MAX_APPROVED_TASK_CHARS)
        + "\nЕсли подтверждаешь — передам эту версию."
    )
    history = [
        {"role": "user", "content": "составь план для инженера"},
        {
            "role": "assistant",
            "content": plan,
            "_engineering_task": {"status": "ready_for_approval"},
        },
    ]

    envelope = module.resolve_engineering_task_context(
        operator_instruction="пусть инженер исполняет",
        history=history,
        session_id="session-4",
        history_session_id="session-4",
    )

    assert envelope.resolution_status == "approved_task_too_large"
    assert envelope.task_text is None
    assert envelope.task_sha256 == hashlib.sha256(plan.encode("utf-8")).hexdigest()
    assert envelope.task_chars == len(plan)


def test_cross_session_history_container_is_not_eligible_for_continuation():
    module = _module()
    history = [
        {"role": "user", "content": "пиши план инженеру", "session_id": "other"},
        {
            "role": "assistant",
            "content": "# План для инженера\nчужая задача",
            "session_id": "other",
        },
    ]

    envelope = module.resolve_engineering_task_context(
        operator_instruction="пусть инженер исполняет",
        history=history,
        session_id="current",
        history_session_id="other",
    )

    assert envelope.resolution_status == "history_session_mismatch"
    assert envelope.task_text is None


def test_two_plan_responses_to_one_request_are_ambiguous():
    module = _module()
    history = [
        {"role": "user", "content": "пиши план задача для инженера"},
        {
            "role": "assistant",
            "content": "# План для инженера\nвариант A",
            "_engineering_task": {"status": "ready_for_approval"},
        },
        {
            "role": "assistant",
            "content": "# План для инженера\nвариант B",
            "_engineering_task": {"status": "ready_for_approval"},
        },
    ]

    envelope = module.resolve_engineering_task_context(
        operator_instruction="пусть инженер исполняет",
        history=history,
        session_id="session-5",
        history_session_id="session-5",
    )

    assert envelope.resolution_status == "ambiguous_approved_plan"
    assert envelope.task_text is None
    assert envelope.task_sha256 is None


def test_ordinary_chat_is_not_promoted_to_a_direct_engineering_task():
    module = _module()

    envelope = module.resolve_engineering_task_context(
        operator_instruction="привет, как дела?",
        history=[],
        session_id="session-6",
        history_session_id="session-6",
    )

    assert envelope.resolution_status == "not_engineering_task"
    assert envelope.task_text is None


def test_informational_engineer_sentence_does_not_trigger_old_plan_continuation():
    module = _module()
    request = "Проверь, почему инженер выполняет старую задачу"
    history = [
        {"role": "user", "content": "пиши план для инженера"},
        {"role": "assistant", "content": _long_plan()},
    ]

    envelope = module.resolve_engineering_task_context(
        operator_instruction=request,
        history=history,
        session_id="session-7",
        history_session_id="session-7",
    )

    assert envelope.resolution_status == "resolved"
    assert envelope.source_kind == "direct_request"
    assert envelope.task_text == request


def test_negative_plan_response_is_not_an_executable_candidate():
    module = _module()
    history = [
        {"role": "user", "content": "пиши план для инженера"},
        {"role": "assistant", "content": "Задача пока не определена; план не готов."},
    ]

    envelope = module.resolve_engineering_task_context(
        operator_instruction="пусть инженер исполняет",
        history=history,
        session_id="session-8",
        history_session_id="session-8",
    )

    assert envelope.resolution_status == "missing_approved_plan"
    assert envelope.task_text is None


def test_explicit_engineering_heading_does_not_override_negative_plan_status():
    module = _module()
    history = [
        {"role": "user", "content": "пиши план для инженера"},
        {
            "role": "assistant",
            "content": "# Задача для инженера пока не определена; план не готов.",
        },
    ]

    envelope = module.resolve_engineering_task_context(
        operator_instruction="пусть инженер исполняет",
        history=history,
        session_id="session-8b",
        history_session_id="session-8b",
    )

    assert envelope.resolution_status == "missing_approved_plan"
    assert envelope.task_text is None


def test_concrete_direct_request_accepts_common_infinitive_form():
    module = _module()
    request = "Нужно исправить parser.py и добавить regression test"

    envelope = module.resolve_engineering_task_context(
        operator_instruction=request,
        history=[],
        session_id="session-8c",
        history_session_id="session-8c",
    )

    assert envelope.resolution_status == "resolved"
    assert envelope.source_kind == "direct_request"
    assert envelope.task_text == request


def test_approved_plan_is_preserved_and_hashed_byte_for_byte():
    module = _module()
    plan = (
        "  \n# План для инженера\n```text\nточный payload\n```\n"
        "Если подтверждаешь — передам эту версию.\n  "
    )
    history = [
        {"role": "user", "content": "подготовь план для инженера"},
        {
            "role": "assistant",
            "content": plan,
            "id": 42,
            "_engineering_task": {"status": "ready_for_approval"},
        },
    ]

    envelope = module.resolve_engineering_task_context(
        operator_instruction="пусть инженер исполняет",
        history=history,
        session_id="session-9",
        history_session_id="session-9",
    )

    assert envelope.task_text == plan
    assert envelope.task_sha256 == hashlib.sha256(plan.encode("utf-8")).hexdigest()


def test_plan_heading_after_character_1200_is_still_eligible():
    module = _module()
    plan = (
        ("вводный контекст\n" * 100)
        + "\n# План для инженера\nточная задача\n"
        + "Если подтверждаешь — передам эту версию."
    )
    assert plan.index("# План") > 1200
    history = [
        {"role": "user", "content": "подготовь план для инженера"},
        {
            "role": "assistant",
            "content": plan,
            "_engineering_task": {"status": "ready_for_approval"},
        },
    ]

    envelope = module.resolve_engineering_task_context(
        operator_instruction="пусть инженер исполняет",
        history=history,
        session_id="session-10",
        history_session_id="session-10",
    )

    assert envelope.resolution_status == "resolved"
    assert envelope.task_text == plan


def test_unapproved_plan_heading_is_not_ready_for_execution():
    module = _module()
    history = [
        {"role": "user", "content": "подготовь план для инженера"},
        {
            "role": "assistant",
            "content": "# План для инженера не утверждён; выполнять нельзя.",
        },
    ]

    envelope = module.resolve_engineering_task_context(
        operator_instruction="пусть инженер исполняет",
        history=history,
        session_id="session-11",
        history_session_id="session-11",
    )

    assert envelope.resolution_status == "missing_approved_plan"
    assert envelope.task_text is None


def test_ready_plan_may_contain_negative_edge_case_language():
    module = _module()
    plan = (
        "# План для инженера\n"
        "Если недостаточно данных, зафиксируй блокер.\n"
        "Если подтверждаешь — передам эту версию."
    )
    history = [
        {"role": "user", "content": "подготовь план для инженера"},
        {
            "role": "assistant",
            "content": plan,
            "_engineering_task": {"status": "ready_for_approval"},
        },
    ]

    envelope = module.resolve_engineering_task_context(
        operator_instruction="пусть инженер исполняет",
        history=history,
        session_id="session-12",
        history_session_id="session-12",
    )

    assert envelope.resolution_status == "resolved"
    assert envelope.task_text == plan


def test_unrelated_conditional_forwarding_is_not_handoff_ready():
    module = _module()
    history = [
        {"role": "user", "content": "подготовь план для инженера"},
        {
            "role": "assistant",
            "content": (
                "# План для инженера не утверждён; выполнять нельзя. "
                "Если подтверждаешь риск, передам отчёт в архив."
            ),
        },
    ]

    envelope = module.resolve_engineering_task_context(
        operator_instruction="пусть инженер исполняет",
        history=history,
        session_id="session-13",
        history_session_id="session-13",
    )

    assert envelope.resolution_status == "missing_approved_plan"
    assert envelope.task_text is None


def test_typed_ready_marker_does_not_require_legacy_phrase():
    module = _module()
    plan = "# План для инженера\nточная typed задача"
    history = [
        {"role": "user", "content": "подготовь план для инженера"},
        {
            "role": "assistant",
            "content": plan,
            "_engineering_task": {"status": "ready_for_approval"},
        },
    ]

    envelope = module.resolve_engineering_task_context(
        operator_instruction="пусть инженер исполняет",
        history=history,
        session_id="session-14",
        history_session_id="session-14",
    )

    assert envelope.resolution_status == "resolved"
    assert envelope.task_text == plan


def test_legacy_transfer_of_version_to_archive_is_not_handoff_ready():
    module = _module()
    history = [
        {"role": "user", "content": "подготовь план для инженера"},
        {
            "role": "assistant",
            "content": (
                "# План для инженера не утверждён; выполнять нельзя. "
                "Если подтверждаешь — передам именно эту версию в архив."
            ),
        },
    ]

    envelope = module.resolve_engineering_task_context(
        operator_instruction="пусть инженер исполняет",
        history=history,
        session_id="session-15",
        history_session_id="session-15",
    )

    assert envelope.resolution_status == "missing_approved_plan"
    assert envelope.task_text is None
