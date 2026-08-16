from __future__ import annotations

import hashlib
import importlib

import pytest

from hermes_cli.pipeline_router import RouterDecision


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


@pytest.mark.parametrize(
    "instruction",
    [
        "пусть инженер выполнит план",
        "пускай инженер исполнит задачу",
        "давай, пусть инженер приступит к выполнению плана",
        "ок: пусть инженер возьмется за реализацию",
        "инженер, приступай к реализации плана",
        "передай план инженеру на исполнение",
        "отдай инженеру задачу на реализацию",
    ],
)
def test_execution_continuation_accepts_structured_russian_forms(instruction):
    assert _module().is_engineering_execution_continuation(instruction)


@pytest.mark.parametrize(
    "instruction",
    [
        "инженер выполнит план?",
        "пусть инженер выполнит план?",
        "если инженер выполнит план, сообщи мне",
        "пусть инженер не выполняет план",
        "проверь, почему инженер выполнит старый план",
        "инженер выполняет план",
        "пусть инженер выполнит план, если его потом утвердят",
        "пусть инженер выполнит план, но не сейчас",
        "инженер, начинай расследование нового инцидента",
        "передай инженеру вопрос о плане",
        "передай план инженеру, но не на исполнение",
        "передай инженеру",
        "передай план инженеру",
        "отдай это инженеру",
    ],
)
def test_execution_continuation_rejects_questions_conditions_and_negation(
    instruction,
):
    assert not _module().is_engineering_execution_continuation(instruction)


def test_future_tense_continuation_resolves_canonical_plan():
    plan = _long_plan()
    envelope = _module().resolve_engineering_task_context(
        operator_instruction="пусть инженер выполнит план",
        history=[
            {"role": "user", "content": "пиши план для инженера"},
            {"role": "assistant", "content": plan, "id": 93308},
        ],
        session_id="session-future-tense",
        history_session_id="session-future-tense",
    )

    assert envelope.resolution_status == "resolved"
    assert envelope.source_kind == "approved_plan"
    assert envelope.task_text == plan


def test_bare_execution_authorization_resolves_only_against_canonical_plan():
    plan = _long_plan()
    envelope = _module().resolve_engineering_task_context(
        operator_instruction="выполняй",
        history=[
            {"role": "user", "content": "пиши план для инженера"},
            {"role": "assistant", "content": plan, "id": 93308},
        ],
        session_id="session-bare-authorization",
        history_session_id="session-bare-authorization",
    )

    assert envelope.resolution_status == "resolved"
    assert envelope.source_kind == "approved_plan"
    assert envelope.task_text == plan
    assert envelope.operator_instruction == "выполняй"


def test_bare_execution_authorization_without_plan_fails_closed():
    envelope = _module().resolve_engineering_task_context(
        operator_instruction="выполняй",
        history=[
            {"role": "user", "content": "что с диагностикой?"},
            {"role": "assistant", "content": "Сейчас только проверяю состояние."},
        ],
        session_id="session-bare-without-plan",
        history_session_id="session-bare-without-plan",
    )

    assert envelope.resolution_status == "missing_approved_plan"
    assert envelope.source_kind == "approved_plan"
    assert envelope.task_text is None


def test_pre_router_resolved_plan_forces_engineering_pipeline():
    run = importlib.import_module("gateway.run")
    observe = importlib.import_module("hermes_cli.pipeline_observe")
    plan = _long_plan()
    context = run._resolve_gateway_engineering_task_context(
        router_decision=None,
        operator_text="выполняй",
        enriched_message="выполняй",
        history=[
            {"role": "user", "content": "пиши план для инженера"},
            {"role": "assistant", "content": plan, "id": 93308},
        ],
        session_id="session-pre-router",
    )

    decision = observe.route_resolved_engineering_task_context(
        context=context,
        pipeline_session_id="pipeline-pre-router",
    )

    assert decision.status == "selected"
    assert decision.selected_pipeline_id == "engineering_review_pipeline"
    assert decision.fallback_pipeline_id == "default_conversation_pipeline"
    assert decision.routing_confidence_source == "typed_task_context"


def test_inline_code_continuation_resolves_canonical_plan():
    plan = _long_plan()
    instruction = "`пусть инженер исполняет план`"

    envelope = _module().resolve_engineering_task_context(
        operator_instruction=instruction,
        history=[
            {"role": "user", "content": "пиши план для инженера"},
            {"role": "assistant", "content": plan, "id": 93308},
        ],
        session_id="session-inline-code",
        history_session_id="session-inline-code",
    )

    assert envelope.resolution_status == "resolved"
    assert envelope.source_kind == "approved_plan"
    assert envelope.task_text == plan
    assert envelope.operator_instruction == instruction


@pytest.mark.parametrize(
    "instruction",
    [
        (
            "в дискуссии "
            "https://vanyushkinhomelab.slack.com/archives/C0B55FPG5B7/"
            "p1786449672479599 есть план реализации сервиса получения информации "
            "для генерации идей. Найди его и пусть инженер реализует этот план"
        ),
        (
            "реализуй план из "
            "https://vanyushkinhomelab.slack.com/archives/C0B55FPG5B7/"
            "p1786449672479599"
        ),
    ],
)
def test_slack_referenced_engineering_task_requires_context_acquisition(instruction):
    envelope = _module().resolve_engineering_task_context(
        operator_instruction=instruction,
        history=[],
        session_id="session-cross-thread-plan",
        history_session_id="session-cross-thread-plan",
    )

    assert envelope.resolution_status == "external_context_required"
    assert envelope.source_kind == "external_reference"
    assert envelope.task_text is None
    assert envelope.operator_instruction == instruction
    assert (
        envelope.source_message_id
        == "slack:vanyushkinhomelab:C0B55FPG5B7:1786449672.479599"
    )


def test_slack_reply_permalink_uses_thread_root_for_context_acquisition():
    instruction = (
        "реализуй план из "
        "https://vanyushkinhomelab.slack.com/archives/C0B55FPG5B7/"
        "p1786685188838539?thread_ts=1786449672.479599&cid=C0B55FPG5B7"
    )

    envelope = _module().resolve_engineering_task_context(
        operator_instruction=instruction,
        history=[],
        session_id="session-cross-thread-reply",
        history_session_id="session-cross-thread-reply",
    )

    assert envelope.resolution_status == "external_context_required"
    assert (
        envelope.source_message_id
        == "slack:vanyushkinhomelab:C0B55FPG5B7:1786449672.479599"
    )


def test_external_reference_context_promotes_to_immutable_task_envelope():
    module = _module()
    instruction = (
        "реализуй план из "
        "https://vanyushkinhomelab.slack.com/archives/C0B55FPG5B7/"
        "p1786449672479599"
    )
    unresolved = module.resolve_engineering_task_context(
        operator_instruction=instruction,
        history=[],
        session_id="session-external-promotion",
        history_session_id="session-external-promotion",
    )

    promoted = module.promote_external_engineering_task_context(
        unresolved,
        reference_context="[Thread context]\nPLAN SENTINEL\n[End of thread context]",
    )

    assert promoted.resolution_status == "resolved"
    assert promoted.source_kind == "external_reference"
    assert promoted.source_message_id == unresolved.source_message_id
    assert instruction in promoted.task_text
    assert "PLAN SENTINEL" in promoted.task_text
    validated, error = module.validate_engineering_task_context(promoted)
    assert error is None
    assert validated == promoted


@pytest.mark.parametrize(
    "instruction",
    [
        "`пусть инженер исполняет план",
        "пусть инженер исполняет план`",
        "```пусть инженер исполняет план```",
        "``пусть инженер исполняет план``",
        "`пусть инженер `исполняет` план`",
        "`пусть инженер исполняет план, если его потом утвердят`",
        "`пусть инженер исполняет план` после согласования",
    ],
)
def test_malformed_or_conditional_inline_code_does_not_authorize_plan(instruction):
    assert not _module().is_engineering_execution_continuation(instruction)


@pytest.mark.parametrize(
    "instruction",
    [
        "пусть инженер выполнит план, если его потом утвердят",
        "пусть инженер выполнит план, но не сейчас",
        "инженер, начинай расследование нового инцидента",
        "передай инженеру вопрос о плане",
        "передай план инженеру, но не на исполнение",
        "передай инженеру",
        "передай план инженеру",
        "отдай это инженеру",
    ],
)
def test_non_execution_phrases_do_not_select_canonical_plan(instruction):
    plan = _long_plan()
    envelope = _module().resolve_engineering_task_context(
        operator_instruction=instruction,
        history=[
            {"role": "user", "content": "пиши план для инженера"},
            {"role": "assistant", "content": plan, "id": 93308},
        ],
        session_id="session-adversarial",
        history_session_id="session-adversarial",
    )

    assert envelope.source_kind != "approved_plan"
    assert envelope.task_text != plan


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


def test_explicit_do_not_execute_text_overrides_typed_ready_marker():
    module = _module()
    history = [
        {"role": "user", "content": "подготовь план для инженера"},
        {
            "role": "assistant",
            "content": (
                "# План для инженера\n"
                "План не согласован; выполнять пока рано."
            ),
            "_engineering_task": {"status": "ready_for_approval"},
        },
    ]

    envelope = module.resolve_engineering_task_context(
        operator_instruction="пусть инженер исполняет",
        history=history,
        session_id="session-14a",
        history_session_id="session-14a",
    )

    assert envelope.resolution_status == "missing_approved_plan"
    assert envelope.task_text is None


def test_explicit_continuation_approves_untyped_plan_without_legacy_phrase():
    module = _module()
    plan = "# План для инженера\nточная новая задача\nГотов к исполнению."
    history = [
        {"role": "user", "content": "подготовь план для инженера"},
        {"role": "assistant", "content": plan},
    ]

    envelope = module.resolve_engineering_task_context(
        operator_instruction="пусть инженер исполняет, но не деплой",
        history=history,
        session_id="session-14b",
        history_session_id="session-14b",
    )

    assert envelope.resolution_status == "resolved"
    assert envelope.task_text == plan
    assert envelope.operator_instruction == "пусть инженер исполняет, но не деплой"


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


def test_gateway_builds_task_from_raw_operator_text_not_slack_parent_quote():
    run = importlib.import_module("gateway.run")
    plan = _long_plan()
    instruction = "пусть инженер выполнит план"
    decision = RouterDecision(
        pipeline_session_id="pipeline-1",
        router_subagent_id="router",
        status="selected",
        selected_pipeline_id="engineering_review_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.98,
        reasoning_summary="engineering continuation",
        fallback_safe=False,
    )

    context = run._resolve_gateway_engineering_task_context(
        router_decision=decision,
        operator_text=instruction,
        enriched_message=(
            '[Replying to: "Cronjob Response ... Следующие шаги: Создай"]\n\n'
            + instruction
        ),
        history=[
            {"role": "user", "content": "пиши план для инженера"},
            {"role": "assistant", "content": plan, "id": 93308},
        ],
        session_id="session-live",
    )

    assert context["task_text"] == plan
    assert context["operator_instruction"] == instruction
    assert context["source_message_id"] == "93308"
    assert context["task_sha256"] == hashlib.sha256(plan.encode("utf-8")).hexdigest()
    assert "Cronjob Response" not in context["task_text"]


def test_gateway_does_not_build_engineering_context_for_other_pipeline():
    run = importlib.import_module("gateway.run")
    decision = RouterDecision(
        pipeline_session_id="pipeline-2",
        router_subagent_id="router",
        status="selected",
        selected_pipeline_id="recruiter_decision_support_pipeline",
        fallback_pipeline_id="default_conversation_pipeline",
        confidence=0.98,
        reasoning_summary="recruiter request",
        fallback_safe=False,
    )

    context = run._resolve_gateway_engineering_task_context(
        router_decision=decision,
        operator_text="оцени вакансию",
        enriched_message="оцени вакансию",
        history=[],
        session_id="session-recruiter",
    )

    assert context is None


def test_engineering_helper_uses_resolved_task_instead_of_confirmation(monkeypatch):
    helpers = importlib.import_module("hermes_cli.pipeline_execution_helpers")
    plan = _long_plan()
    envelope = _module().resolve_engineering_task_context(
        operator_instruction="ок, пусть инженер исполняет",
        history=[
            {"role": "user", "content": "пиши план для инженера"},
            {"role": "assistant", "content": plan},
        ],
        session_id="session-helper",
        history_session_id="session-helper",
    )
    captured = {}

    def _loop(**kwargs):
        captured.update(kwargs)
        return {"status": "executed"}

    monkeypatch.setattr(helpers, "execute_bounded_rework_loop", _loop)

    result = helpers.execute_engineering_review_helper(
        config={"pipelines": {"execution": {"mode": "disabled"}}},
        session=type("Session", (), {"session_id": "session-helper"})(),
        loaded_specs=object(),
        runtime_factory=object(),
        runner=object(),
        user_message="ок, пусть инженер исполняет",
        engineering_task_context=envelope.to_dict(),
    )

    assert result == {"status": "executed"}
    assert captured["user_message"] == plan
    assert captured["operator_instruction"] == "ок, пусть инженер исполняет"
    assert "ок, пусть инженер исполняет" not in captured["user_message"]


def test_engineering_helper_executes_sealed_external_reference_task(monkeypatch):
    helpers = importlib.import_module("hermes_cli.pipeline_execution_helpers")
    module = _module()
    instruction = (
        "реализуй план из "
        "https://vanyushkinhomelab.slack.com/archives/C0B55FPG5B7/"
        "p1786449672479599"
    )
    unresolved = module.resolve_engineering_task_context(
        operator_instruction=instruction,
        history=[],
        session_id="session-external-helper",
        history_session_id="session-external-helper",
    )
    envelope = module.promote_external_engineering_task_context(
        unresolved,
        reference_context="[Thread context]\nEXTERNAL PLAN SENTINEL",
    )
    captured = {}
    monkeypatch.setattr(
        helpers,
        "execute_bounded_rework_loop",
        lambda **kwargs: captured.update(kwargs) or {"status": "executed"},
    )

    result = helpers.execute_engineering_review_helper(
        config={"pipelines": {"execution": {"mode": "disabled"}}},
        session=type("Session", (), {"session_id": "session-external-helper"})(),
        loaded_specs=object(),
        runtime_factory=object(),
        runner=object(),
        user_message=instruction,
        engineering_task_context=envelope.to_dict(),
    )

    assert result == {"status": "executed"}
    assert captured["user_message"] == envelope.task_text
    assert "EXTERNAL PLAN SENTINEL" in captured["user_message"]
    assert captured["operator_instruction"] == instruction


def test_engineering_helper_preserves_enriched_message_for_direct_request(monkeypatch):
    helpers = importlib.import_module("hermes_cli.pipeline_execution_helpers")
    raw_request = "Исправь parser.py"
    enriched_request = (
        '[Replying to: "предыдущая ошибка parser"]\n\n'
        "[The user sent a document attachment: trace.log]\n\n"
        + raw_request
    )
    envelope = _module().resolve_engineering_task_context(
        operator_instruction=raw_request,
        history=[],
        session_id="session-direct",
        history_session_id="session-direct",
    )
    captured = {}
    monkeypatch.setattr(
        helpers,
        "execute_bounded_rework_loop",
        lambda **kwargs: captured.update(kwargs) or {"status": "executed"},
    )

    helpers.execute_engineering_review_helper(
        config={"pipelines": {"execution": {"mode": "disabled"}}},
        session=type("Session", (), {"session_id": "session-direct"})(),
        loaded_specs=object(),
        runtime_factory=object(),
        runner=object(),
        user_message=enriched_request,
        engineering_task_context=envelope.to_dict(),
    )

    assert captured["user_message"] == enriched_request


def test_engineering_helper_blocks_unresolved_context_before_loop(monkeypatch):
    helpers = importlib.import_module("hermes_cli.pipeline_execution_helpers")
    called = {"value": False}

    def _loop(**_kwargs):
        called["value"] = True
        raise AssertionError("loop must not run")

    monkeypatch.setattr(helpers, "execute_bounded_rework_loop", _loop)

    result = helpers.execute_engineering_review_helper(
        config={"pipelines": {"execution": {"mode": "disabled"}}},
        session=object(),
        loaded_specs=object(),
        runtime_factory=object(),
        runner=object(),
        user_message="пусть инженер исполняет",
        engineering_task_context={
            "schema_version": "engineering_task_envelope.v1",
            "resolution_status": "missing_approved_plan",
            "source_kind": "approved_plan",
            "task_text": None,
            "operator_instruction": "пусть инженер исполняет",
            "source_session_id": "session-helper",
            "source_message_id": None,
            "task_sha256": None,
            "task_chars": 0,
        },
    )

    assert result["status"] == "blocked"
    assert result["blocked_reason"] == "engineering_task_missing_approved_plan"
    assert called["value"] is False


def test_engineering_helper_blocks_tampered_task_hash(monkeypatch):
    helpers = importlib.import_module("hermes_cli.pipeline_execution_helpers")
    monkeypatch.setattr(
        helpers,
        "execute_bounded_rework_loop",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("loop must not run")),
    )

    result = helpers.execute_engineering_review_helper(
        config={"pipelines": {"execution": {"mode": "disabled"}}},
        session=object(),
        loaded_specs=object(),
        runtime_factory=object(),
        runner=object(),
        user_message="пусть инженер исполняет",
        engineering_task_context={
            "schema_version": "engineering_task_envelope.v1",
            "resolution_status": "resolved",
            "source_kind": "approved_plan",
            "task_text": "# План для инженера\nподменён",
            "operator_instruction": "пусть инженер исполняет",
            "source_session_id": "session-helper",
            "source_message_id": "42",
            "task_sha256": "0" * 64,
            "task_chars": 30,
        },
    )

    assert result["status"] == "blocked"
    assert result["blocked_reason"] == "engineering_task_context_invalid"


def test_engineering_helper_blocks_envelope_from_another_session(monkeypatch):
    helpers = importlib.import_module("hermes_cli.pipeline_execution_helpers")
    plan = _long_plan()
    envelope = _module().resolve_engineering_task_context(
        operator_instruction="пусть инженер исполняет",
        history=[
            {"role": "user", "content": "пиши план для инженера"},
            {"role": "assistant", "content": plan},
        ],
        session_id="session-A",
        history_session_id="session-A",
    )
    monkeypatch.setattr(
        helpers,
        "execute_bounded_rework_loop",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("loop must not run")),
    )

    result = helpers.execute_engineering_review_helper(
        config={"pipelines": {"execution": {"mode": "disabled"}}},
        session=type("Session", (), {"session_id": "session-B"})(),
        loaded_specs=object(),
        runtime_factory=object(),
        runner=object(),
        user_message="пусть инженер исполняет",
        engineering_task_context=envelope.to_dict(),
    )

    assert result["status"] == "blocked"
    assert result["blocked_reason"] == "engineering_task_session_mismatch"


def test_queued_raw_operator_text_survives_slack_enrichment():
    run = importlib.import_module("gateway.run")
    raw = "ок, пусть инженер исполняет"
    enriched = '[Replying to: "Cronjob Response"]\n\n' + raw

    assert run._operator_reply_text(enriched, raw) == raw
    assert _module().is_engineering_execution_continuation(
        run._operator_reply_text(enriched, raw)
    )
