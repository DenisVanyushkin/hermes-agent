from __future__ import annotations

from types import SimpleNamespace

from agent.conversation_loop import (
    _assistant_turn_has_tool_call_named,
    _compose_turn_user_message_content,
    _should_preflight_block_for_profile_context,
    run_conversation,
)
from agent.turn_finalizer import finalize_turn
from hermes_cli.profile_context import (
    build_role_context_for_task,
    inject_role_execution_debug_header,
    RoleContextResult,
)
from hermes_cli.review_gate import ReviewGateDecision
from hermes_cli.profile_request_context import (
    approval_constraints_text,
    approval_intent_text,
    has_explicit_approval,
)


def test_role_context_is_injected_into_user_message_ephemeral_context():
    result = build_role_context_for_task("Check WebUI status and inspect logs")
    composed = _compose_turn_user_message_content(
        "Check WebUI status and inspect logs",
        role_context=result.context_text,
    )
    assert composed.startswith("Check WebUI status and inspect logs")
    assert "Engineer" in composed
    assert "Repo/code mutation is allowed" in composed


def test_role_context_is_not_injected_into_cached_system_prompt():
    agent = SimpleNamespace(_cached_system_prompt="SYSTEM PROMPT")
    result = build_role_context_for_task("Check WebUI status and inspect logs")
    composed = _compose_turn_user_message_content("Check WebUI status and inspect logs", role_context=result.context_text)
    assert agent._cached_system_prompt == "SYSTEM PROMPT"
    assert "SYSTEM PROMPT" not in composed


def test_cached_system_prompt_remains_unchanged():
    agent = SimpleNamespace(_cached_system_prompt="SYSTEM PROMPT")
    _compose_turn_user_message_content("Запиши меня на стрижку", role_context="role guidance")
    assert agent._cached_system_prompt == "SYSTEM PROMPT"


def test_model_selection_remains_unchanged():
    agent = SimpleNamespace(model="gpt-5.4-mini")
    _compose_turn_user_message_content("Check WebUI status and inspect logs", role_context="role guidance")
    assert agent.model == "gpt-5.4-mini"


def test_missing_contract_soft_falls_back_without_failing_the_turn():
    composed = _compose_turn_user_message_content("hello", role_context="")
    assert composed == "hello"


def test_general_operator_task_injects_personal_admin_context():
    result = build_role_context_for_task("Запиши меня на стрижку")
    composed = _compose_turn_user_message_content(
        "Запиши меня на стрижку",
        role_context=result.context_text,
    )
    assert result.selected_role == "general_operator"
    assert "General Operator" in composed
    assert "external commitment" in composed.lower()


def test_engineer_task_injects_engineering_context():
    result = build_role_context_for_task("Check WebUI status and inspect logs")
    composed = _compose_turn_user_message_content(
        "Check WebUI status and inspect logs",
        role_context=result.context_text,
    )
    assert result.selected_role == "engineer"
    assert "Engineer" in composed
    assert "Production/runtime mutation requires explicit approval" in composed


def test_profile_context_critical_metadata_does_not_trigger_preflight_block():
    result = build_role_context_for_task(
        "Настрой публичный доступ к Hermes WebUI через Cloudflare Tunnel и внеси необходимые изменения"
    )

    assert result.critical_approval_required is True
    assert _should_preflight_block_for_profile_context(result) is False


def test_security_auditor_and_scribe_are_not_auto_invoked_for_ordinary_tasks():
    result = build_role_context_for_task("Запиши меня на стрижку")
    composed = _compose_turn_user_message_content("Запиши меня на стрижку", role_context=result.context_text)
    assert "Security Auditor" not in composed
    assert "Scribe" not in composed


def test_role_context_module_path_is_additive_only():
    base = "Check WebUI status and inspect logs"
    role_context = "Role: Engineer\nPurpose: ..."
    composed = _compose_turn_user_message_content(base, role_context=role_context)
    assert composed == base + "\n\n" + role_context


def test_debug_header_is_injected_into_response_path_not_cached_system_prompt(monkeypatch):
    monkeypatch.setenv("HERMES_PROFILE_DEBUG_HEADER", "1")
    result = build_role_context_for_task("Зафиксируй итог сегодняшней работы по ролям Hermes")

    response = inject_role_execution_debug_header("assistant response", result)

    assert response.startswith("Hermes role: scribe")
    assert "assistant response" in response
    assert "SYSTEM PROMPT" not in response


class _DummyToolGuardrails:
    def reset_for_turn(self):
        self.reset_called = True


class _ApprovalGateAgent:
    def __init__(self, *, api_mode: str = "anthropic_messages"):
        self.session_id = "session-approval-gate"
        self.provider = "openrouter"
        self.model = "gpt-5.4-mini"
        self.base_url = "https://example.invalid"
        self.api_key = ""
        self.api_mode = api_mode
        self.platform = "slack"
        self._memory_write_origin = "assistant_tool"
        self.max_iterations = 3
        self._compression_warning = None
        self._tool_guardrails = _DummyToolGuardrails()
        self._user_turn_count = 0
        self._turns_since_memory = 0
        self._memory_nudge_interval = 0
        self._tool_guardrail_halt_decision = None
        self._todo_store = SimpleNamespace(has_items=lambda: False)
        self._persisted = []
        self._ensure_db_session_called = False
        self._restore_primary_runtime_called = False
        self._execute_tool_calls_called = False
        self._run_codex_app_server_turn_called = False
        self._emit_interim_assistant_message_called = False
        self._requested_model = None
        self._cached_system_prompt = "SYSTEM PROMPT"
        self._memory_store = None
        self._memory_manager = None
        self._interrupt_requested = False
        self._interrupt_message = None
        self._interrupt_thread_signal_pending = False
        self.quiet_mode = True
        self.valid_tool_names = []
        self.tools = []
        self.compression_enabled = False

    def _ensure_db_session(self):
        self._ensure_db_session_called = True

    def _restore_primary_runtime(self):
        self._restore_primary_runtime_called = True

    def _persist_session(self, messages, conversation_history):
        self._persisted.append((messages, conversation_history))

    def _execute_tool_calls(self, *args, **kwargs):
        self._execute_tool_calls_called = True
        raise AssertionError("approval gate should stop before tool execution")

    def _run_codex_app_server_turn(self, **kwargs):
        self._run_codex_app_server_turn_called = True
        return {
            "final_response": "normal path reached",
            "last_reasoning": None,
            "messages": kwargs.get("messages", []),
            "api_calls": 0,
            "completed": True,
            "failed": False,
            "partial": False,
            "interrupted": False,
            "response_transformed": False,
            "response_previewed": False,
            "turn_exit_reason": "codex_app_server_stub",
            "model": self.model,
            "requested_model": self._requested_model,
            "provider": self.provider,
            "base_url": self.base_url,
            "session_id": self.session_id,
        }

    def _emit_interim_assistant_message(self, *_args, **_kwargs):
        self._emit_interim_assistant_message_called = True
        raise AssertionError("approval gate should stop before interim assistant callbacks")

    def _cleanup_dead_connections(self):
        return False

    def _emit_status(self, *_args, **_kwargs):
        return None

    def _replay_compression_warning(self):
        return None

    def _hydrate_todo_store(self, _history):
        return None

    def _safe_print(self, *_args, **_kwargs):
        return None


def test_approval_required_task_stops_before_tool_execution_and_requests_approval(monkeypatch):
    monkeypatch.setenv("HERMES_PROFILE_DEBUG_HEADER", "1")
    agent = _ApprovalGateAgent(api_mode="codex_app_server")

    result = run_conversation(
        agent,
        "Настрой публичный доступ к Hermes WebUI через Cloudflare Tunnel и внеси необходимые изменения",
        conversation_history=None,
    )

    assert agent._ensure_db_session_called is True
    assert agent._restore_primary_runtime_called is True
    assert agent._execute_tool_calls_called is False
    assert result["api_calls"] == 0
    assert result["completed"] is True
    assert result["failed"] is False
    assert result["turn_exit_reason"] == "codex_app_server_stub"
    assert result["final_response"] == "normal path reached"
    assert agent._persisted
    assert "_approval_gate" not in agent._persisted[0][0][-1]


class _FinalizeTurnAgent:
    def __init__(self):
        self.session_id = "session-review-gate"
        self.model = "xiaomi/mimo-v2.5-pro"
        self.provider = "openrouter"
        self.base_url = "https://openrouter.ai/api/v1"
        self.platform = "slack"
        self.max_iterations = 8
        self.iteration_budget = SimpleNamespace(remaining=8, used=1, max_total=8)
        self.quiet_mode = True
        self._persisted = []
        self.session_input_tokens = 0
        self.session_output_tokens = 0
        self.session_cache_read_tokens = 0
        self.session_cache_write_tokens = 0
        self.session_reasoning_tokens = 0
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0
        self.session_estimated_cost_usd = 0.0
        self.session_cost_status = "not_tracked"
        self.session_cost_source = "test"
        self.context_compressor = SimpleNamespace(last_prompt_tokens=0)
        self._tool_guardrail_halt_decision = None
        self._response_was_previewed = False
        self._interrupt_message = None
        self._stream_callback = None
        self._skill_nudge_interval = 0
        self._iters_since_skill = 0
        self.valid_tool_names = []

    def _emit_status(self, *_args, **_kwargs):
        return None

    def _safe_print(self, *_args, **_kwargs):
        return None

    def _handle_max_iterations(self, *_args, **_kwargs):
        raise AssertionError("max-iterations path not expected")

    def _save_trajectory(self, *_args, **_kwargs):
        return None

    def _cleanup_task_resources(self, *_args, **_kwargs):
        return None

    def _drop_trailing_empty_response_scaffolding(self, *_args, **_kwargs):
        return None

    def _persist_session(self, messages, conversation_history):
        self._persisted.append((messages, conversation_history))

    def _file_mutation_verifier_enabled(self):
        return False

    def _format_file_mutation_failure_footer(self, *_args, **_kwargs):
        return ""

    def _turn_completion_explainer_enabled(self):
        return False

    def _format_turn_completion_explanation(self, *_args, **_kwargs):
        return ""

    def _drain_pending_steer(self):
        return None

    def clear_interrupt(self):
        return None

    def _sync_external_memory_for_turn(self, *_args, **_kwargs):
        return None

    def _spawn_background_review(self, *_args, **_kwargs):
        return None


def _patched_messages_for_review_gate():
    return [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_patch_1",
                    "type": "function",
                    "function": {
                        "name": "patch",
                        "arguments": '{"path":"hermes_cli/profile_execution.py"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_patch_1",
            "content": '{"success": true}',
        },
    ]


def test_finalize_turn_blocks_completion_when_review_gate_enforce(monkeypatch):
    monkeypatch.setattr("hermes_cli.review_gate.load_config_readonly", lambda: {"review_gate": {"mode": "enforce", "reviewer_tier": "code_review"}})
    agent = _FinalizeTurnAgent()
    result = finalize_turn(
        agent,
        final_response="Implemented the fix and tests are green.",
        api_call_count=1,
        interrupted=False,
        failed=False,
        messages=_patched_messages_for_review_gate(),
        conversation_history=[],
        effective_task_id=None,
        turn_id="turn-1",
        user_message="Fix the failing pytest suite in the repository",
        original_user_message="Fix the failing pytest suite in the repository",
        _should_review_memory=False,
        _turn_exit_reason="text_response(stop)",
        response_pre_transformed=False,
    )
    assert result["completed"] is False
    assert "final completion is blocked because automatic reviewer failed" in result["final_response"].lower()
    assert "Task summary: Fix the failing pytest suite in the repository" in result["final_response"]
    assert "Changed files:" in result["final_response"]
    assert agent._persisted
    assert "_review_gate" in agent._persisted[0][0][-1]


def test_finalize_turn_observe_keeps_completion(monkeypatch):
    monkeypatch.setattr("hermes_cli.review_gate.load_config_readonly", lambda: {"review_gate": {"mode": "observe", "reviewer_tier": "code_review"}})
    agent = _FinalizeTurnAgent()
    result = finalize_turn(
        agent,
        final_response="Implemented the fix and tests are green.",
        api_call_count=1,
        interrupted=False,
        failed=False,
        messages=_patched_messages_for_review_gate(),
        conversation_history=[],
        effective_task_id=None,
        turn_id="turn-1",
        user_message="Fix the failing pytest suite in the repository",
        original_user_message="Fix the failing pytest suite in the repository",
        _should_review_memory=False,
        _turn_exit_reason="text_response(stop)",
        response_pre_transformed=False,
    )
    assert result["completed"] is True
    assert result["final_response"] == "Implemented the fix and tests are green."


def test_finalize_turn_logs_effective_review_gate_mode(monkeypatch, caplog):
    monkeypatch.setattr("hermes_cli.review_gate.load_config_readonly", lambda: {"review_gate": {"mode": "enforce", "reviewer_tier": "code_review"}})

    def _fake_evaluate_review_gate(*_args, **_kwargs):
        return ReviewGateDecision(
            mode="enforce",
            status="approved",
            review_required=True,
            blocking=False,
            material_change_detected=True,
            reviewer_tier="code_review",
            reviewer_provider="openai-codex",
            reviewer_model="gpt-5.5",
            changed_paths=["hermes_cli/review_gate.py"],
            changed_path_count=1,
            packet={"task": "Fix logging", "operation_category": "repo_mutation"},
            packet_hash="sha256:abc",
            automatic_review_invoked=True,
            automatic_review_verdict="approved",
            reviewer_summary="looks good",
            reviewer_findings=[],
            required_changes=[],
            tests_required=[],
            approval_sensitive=False,
            user_override=False,
            review_error="",
            warning="",
        )

    monkeypatch.setattr("agent.turn_finalizer.evaluate_review_gate", _fake_evaluate_review_gate)
    agent = _FinalizeTurnAgent()
    with caplog.at_level("INFO"):
        finalize_turn(
            agent,
            final_response="Implemented the fix and tests are green.",
            api_call_count=1,
            interrupted=False,
            failed=False,
            messages=_patched_messages_for_review_gate(),
            conversation_history=[],
            effective_task_id=None,
            turn_id="turn-1",
            user_message="Fix the failing pytest suite in the repository",
            original_user_message="Fix the failing pytest suite in the repository",
            _should_review_memory=False,
            _turn_exit_reason="text_response(stop)",
            response_pre_transformed=False,
        )

    messages = [record.message for record in caplog.records if record.name == "agent.conversation_loop"]
    assert any("review gate evaluation:" in message for message in messages)
    assert any("review_gate.mode=enforce" in message for message in messages)
    assert any("automatic_review_invoked=True" in message for message in messages)
    assert not any("prompt" in message.lower() or "secret" in message.lower() for message in messages)


def test_finalize_turn_auto_review_approved_allows_completion_without_manual_prompt(monkeypatch):
    monkeypatch.setattr("hermes_cli.review_gate.load_config_readonly", lambda: {"review_gate": {"mode": "enforce", "reviewer_tier": "code_review"}})

    def _fake_evaluate_review_gate(*_args, **_kwargs):
        return ReviewGateDecision(
            mode="enforce",
            status="approved",
            review_required=True,
            blocking=False,
            material_change_detected=True,
            reviewer_tier="code_review",
            reviewer_provider="openai-codex",
            reviewer_model="gpt-5.5",
            changed_paths=["hermes_cli/review_gate.py"],
            changed_path_count=1,
            packet={"task": "Fix automatic reviewer plumbing", "operation_category": "repo_mutation"},
            packet_hash="sha256:abc",
            automatic_review_invoked=True,
            automatic_review_verdict="approved",
            reviewer_summary="looks good",
            reviewer_findings=[],
            required_changes=[],
            tests_required=[],
            approval_sensitive=False,
            user_override=False,
            review_error="",
            warning="",
        )

    monkeypatch.setattr("agent.turn_finalizer.evaluate_review_gate", _fake_evaluate_review_gate)
    agent = _FinalizeTurnAgent()
    result = finalize_turn(
        agent,
        final_response="Implemented the fix and tests are green.",
        api_call_count=1,
        interrupted=False,
        failed=False,
        messages=_patched_messages_for_review_gate(),
        conversation_history=[],
        effective_task_id=None,
        turn_id="turn-1",
        user_message="Fix the failing pytest suite in the repository",
        original_user_message="Fix the failing pytest suite in the repository",
        _should_review_memory=False,
        _turn_exit_reason="text_response(stop)",
        response_pre_transformed=False,
    )
    assert result["completed"] is True
    assert result["final_response"] == "Implemented the fix and tests are green."
    assert result["messages"][-1]["role"] == "tool"
    assert all("_review_gate" not in msg for msg in result["messages"] if msg.get("role") == "assistant")


def test_security_critical_text_prompt_reaches_normal_model_path_without_preflight_block(monkeypatch):
    monkeypatch.setenv("HERMES_PROFILE_DEBUG_HEADER", "1")
    plugin_calls = []
    stream_events = []

    def _fake_invoke_hook(hook_name, **kwargs):
        plugin_calls.append((hook_name, kwargs))
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_invoke_hook)
    agent = _ApprovalGateAgent(api_mode="codex_app_server")

    result = run_conversation(
        agent,
        "Настрой публичный доступ к Hermes WebUI через Cloudflare Tunnel и внеси необходимые изменения",
        conversation_history=None,
        stream_callback=stream_events.append,
    )

    assert result["turn_exit_reason"] == "codex_app_server_stub"
    assert result["final_response"] == "normal path reached"
    assert plugin_calls
    assert plugin_calls[0][0] == "pre_llm_call"
    assert agent._run_codex_app_server_turn_called is True
    assert agent._execute_tool_calls_called is False
    assert agent._emit_interim_assistant_message_called is False
    assert stream_events == []


def test_prompt_lookup_with_sensitive_terms_does_not_take_preflight_approval(monkeypatch):
    plugin_calls = []

    def _fake_invoke_hook(hook_name, **kwargs):
        plugin_calls.append((hook_name, kwargs))
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_invoke_hook)
    agent = _ApprovalGateAgent(api_mode="codex_app_server")

    result = run_conversation(
        agent,
        "дай полный промпт market-intelligence-14day-pilot-status. "
        "Внутри могут быть .env, auth.json, secrets, provider config, do not write files.",
        conversation_history=None,
    )

    assert result["turn_exit_reason"] == "codex_app_server_stub"
    assert result["final_response"] == "normal path reached"
    assert plugin_calls
    assert agent._run_codex_app_server_turn_called is True
    assert agent._execute_tool_calls_called is False


def test_restart_plan_prompt_does_not_take_preflight_approval(monkeypatch):
    plugin_calls = []

    def _fake_invoke_hook(hook_name, **kwargs):
        plugin_calls.append((hook_name, kwargs))
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_invoke_hook)
    agent = _ApprovalGateAgent(api_mode="codex_app_server")

    result = run_conversation(
        agent,
        "составь план рестарта hermes-gateway, но ничего не выполняй",
        conversation_history=None,
    )

    assert result["turn_exit_reason"] == "codex_app_server_stub"
    assert result["final_response"] == "normal path reached"
    assert plugin_calls
    assert agent._run_codex_app_server_turn_called is True
    assert agent._execute_tool_calls_called is False


def test_constraints_only_secret_terms_do_not_take_preflight_approval(monkeypatch):
    plugin_calls = []

    def _fake_invoke_hook(hook_name, **kwargs):
        plugin_calls.append((hook_name, kwargs))
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_invoke_hook)
    agent = _ApprovalGateAgent(api_mode="codex_app_server")

    result = run_conversation(
        agent,
        "создай текст: не читай .env, не читай auth.json, не печатай secrets",
        conversation_history=None,
    )

    assert result["turn_exit_reason"] == "codex_app_server_stub"
    assert result["final_response"] == "normal path reached"
    assert plugin_calls
    assert agent._run_codex_app_server_turn_called is True
    assert agent._execute_tool_calls_called is False


def test_explicit_approval_outside_quoted_context_unblocks_pending_gate(monkeypatch):
    plugin_calls = []
    captured = {}

    def _fake_invoke_hook(hook_name, **kwargs):
        plugin_calls.append((hook_name, kwargs))
        return []

    def _capture_codex_turn(**kwargs):
        captured.update(kwargs)
        return {
            "final_response": "normal path reached",
            "last_reasoning": None,
            "messages": kwargs.get("messages", []),
            "api_calls": 0,
            "completed": True,
            "failed": False,
            "partial": False,
            "interrupted": False,
            "response_transformed": False,
            "response_previewed": False,
            "turn_exit_reason": "codex_app_server_stub",
            "model": agent.model,
            "requested_model": agent._requested_model,
            "provider": agent.provider,
            "base_url": agent.base_url,
            "session_id": agent.session_id,
        }

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_invoke_hook)
    agent = _ApprovalGateAgent(api_mode="codex_app_server")
    agent._run_codex_app_server_turn = _capture_codex_turn

    prior_task = (
        "Create ~/.hermes/skills/role-package-author/SKILL.md\n"
        "Create docs/profile-handoffs/2026-06-11-role-package-author-skill.md\n"
        "Run read-only tests"
    )
    conversation_history = [
        {"role": "user", "content": "# Task: Create a Hermes Skill for Authoring Role Packages"},
        {
            "role": "assistant",
            "content": "I need explicit approval before any mutation-capable changes.",
            "_approval_gate": {
                "required": True,
                "critical_hard_stop": True,
                "selected_role": "scribe",
                "canonical_role": "scribe",
                "operation_category": "security_critical_mutation",
                "reviewer_profile": None,
                "approval_reason": "writes under ~/.hermes require explicit approval",
                "task": prior_task,
                "model_selection": {"policy_name": "approval_critical"},
            },
        },
    ]

    approval_reply = (
        '[Replying to: "# Task: Create a Hermes Skill for Authoring Role Packages ..."]\n'
        "[denis] approve\n"
        "Proceed with the task exactly as scoped.\n"
        "Clarifications:\n"
        "- Creating ~/.hermes/skills/role-package-author/SKILL.md is approved.\n"
        "- Creating the report under docs/profile-handoffs/ is approved.\n"
        "- Running read-only verification commands and relevant tests is approved.\n"
        "- Do not read .env, auth.json, provider config, or secret files.\n"
        "- Do not run hermes role install.\n"
        "- Do not create or install any generated role package.\n"
    )

    result = run_conversation(
        agent,
        approval_reply,
        conversation_history=conversation_history,
    )

    assert result["turn_exit_reason"] == "codex_app_server_stub"
    assert result["final_response"] == "normal path reached"
    assert plugin_calls
    assert agent._run_codex_app_server_turn_called is False
    assert "Create ~/.hermes/skills/role-package-author/SKILL.md" in captured["user_message"]
    assert "Do not read .env, auth.json, provider config, or secret files." in captured["user_message"]
    assert "[Replying to:" not in captured["user_message"]
    assert captured["original_user_message"] == approval_reply


def test_quoted_approve_without_live_consent_does_not_unblock_pending_gate(monkeypatch):
    monkeypatch.setenv("HERMES_PROFILE_DEBUG_HEADER", "1")
    agent = _ApprovalGateAgent(api_mode="codex_app_server")

    conversation_history = [
        {"role": "user", "content": "Set up Cloudflare Tunnel for Hermes WebUI"},
        {
            "role": "assistant",
            "content": "I need explicit approval before any mutation-capable changes.",
            "_approval_gate": {
                "required": True,
                "critical_hard_stop": True,
                "selected_role": "engineer",
                "canonical_role": "engineer",
                "operation_category": "security_critical_mutation",
                "reviewer_profile": "security_auditor",
                "approval_reason": "Cloudflare/public exposure changes require explicit approval",
                "task": "Set up Cloudflare Tunnel for Hermes WebUI",
                "model_selection": {"policy_name": "approval_critical"},
            },
        },
    ]

    result = run_conversation(
        agent,
        '[Replying to: "approve"]\nNo, do not proceed yet.',
        conversation_history=conversation_history,
    )

    assert result["turn_exit_reason"] == "approval_required_preflight"
    assert "explicit approval" in result["final_response"].lower()
    assert agent._run_codex_app_server_turn_called is False


def test_explicit_approval_unblocks_replayed_gateway_prompt_without_metadata(monkeypatch):
    plugin_calls = []
    captured = {}

    def _fake_invoke_hook(hook_name, **kwargs):
        plugin_calls.append((hook_name, kwargs))
        return []

    def _capture_codex_turn(**kwargs):
        captured.update(kwargs)
        return {
            "final_response": "normal path reached",
            "last_reasoning": None,
            "messages": kwargs.get("messages", []),
            "api_calls": 0,
            "completed": True,
            "failed": False,
            "partial": False,
            "interrupted": False,
            "response_transformed": False,
            "response_previewed": False,
            "turn_exit_reason": "codex_app_server_stub",
            "model": agent.model,
            "requested_model": agent._requested_model,
            "provider": agent.provider,
            "base_url": agent.base_url,
            "session_id": agent.session_id,
        }

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_invoke_hook)
    agent = _ApprovalGateAgent(api_mode="codex_app_server")
    agent._run_codex_app_server_turn = _capture_codex_turn

    prior_task = (
        "[denis] # Task: Create a Hermes Skill for Authoring Role Packages\n"
        "## Goal\n"
        "Create a user-owned Hermes skill that helps operators and contributors create new role package skeletons safely."
    )
    conversation_history = [
        {"role": "user", "content": prior_task},
        {
            "role": "assistant",
            "content": (
                "Hermes role: scribe\n"
                "Reviewer: security_auditor\n"
                "Approval: required\n"
                "Operation category: security_critical_mutation\n\n"
                "I need explicit approval before any mutation-capable changes.\n\n"
                "Planned action:\n"
                f"- {prior_task}\n\n"
                "I will stop here before file writes, runtime changes, or external system mutations.\n"
                "Reply with explicit approve if you want me to proceed, or adjust the scope."
            ),
        },
    ]

    approval_reply = (
        '[Replying to: "# Task: Create a Hermes Skill for Authoring Role Packages\n'
        "\n"
        "## Goal\n"
        "\n"
        "Create a user-owned Hermes skill that helps operators and contributors create new role package skeletons safely.\n"
        "\n"
        "The skill should guide role package authoring, generate a valid role-package.yaml, create optional skill scaffolds, and produce validation/install commands.\n"
        "\n"
        "The skill must not install packages automatically.\n"
        "\n"
        "## Skill Name\n"
        "\n"
        "Use:\n"
        "\n"
        "text role-package-author\n"
        "\n"
        "Alternative acceptable name:\n"
        "\n"
        "text create-role-package\n"
        "\n"
        'Prefe"]\n'
        "\n"
        "[denis] approve\n"
        "\n"
        "Proceed with the task exactly as scoped.\n"
        "\n"
        "Clarifications:\n"
        "- Creating ~/.hermes/skills/role-package-author/SKILL.md is approved.\n"
        "- Creating the report under docs/profile-handoffs/ is approved.\n"
        "- Running read-only verification commands and relevant tests is approved.\n"
        "- Do not read .env, auth.json, provider config, or secret files.\n"
        "- Do not run hermes role install.\n"
        "- Do not create or install any generated role package.\n"
        "- Do not modify built-in roles.\n"
        "- Do not enable enforcement or package routing.\n"
        "- Do not print secrets.\n"
    )

    result = run_conversation(
        agent,
        approval_reply,
        conversation_history=conversation_history,
    )

    assert result["turn_exit_reason"] == "codex_app_server_stub"
    assert result["final_response"] == "normal path reached"
    assert plugin_calls
    assert "Create a Hermes Skill for Authoring Role Packages" in captured["user_message"]
    assert "Do not read .env, auth.json, provider config, or secret files." in captured["user_message"]
    assert "[Replying to:" not in captured["user_message"]


def test_multiline_slack_reply_quote_preserves_approval_and_constraints():
    approval_reply = (
        '[Replying to: "# Task: Create a Hermes Skill for Authoring Role Packages\n'
        "\n"
        "## Goal\n"
        "\n"
        "Create a user-owned Hermes skill that helps operators and contributors create new role package skeletons safely.\n"
        "\n"
        "The skill should guide role package authoring, generate a valid role-package.yaml, create optional skill scaffolds, and produce validation/install commands.\n"
        "\n"
        "The skill must not install packages automatically.\n"
        "\n"
        "## Skill Name\n"
        "\n"
        "Use:\n"
        "\n"
        "text role-package-author\n"
        "\n"
        "Alternative acceptable name:\n"
        "\n"
        "text create-role-package\n"
        "\n"
        'Prefe"]\n'
        "\n"
        "[denis] approve\n"
        "\n"
        "Proceed with the task exactly as scoped.\n"
        "\n"
        "Clarifications:\n"
        "- Creating ~/.hermes/skills/role-package-author/SKILL.md is approved.\n"
        "- Creating the report under docs/profile-handoffs/ is approved.\n"
        "- Running read-only verification commands and relevant tests is approved.\n"
        "- Do not read .env, auth.json, provider config, or secret files.\n"
        "- Do not run hermes role install.\n"
        "- Do not create or install any generated role package.\n"
        "- Do not modify built-in roles.\n"
        "- Do not enable enforcement or package routing.\n"
        "- Do not print secrets.\n"
    )

    assert approval_intent_text(approval_reply).splitlines()[0] == "approve"
    assert has_explicit_approval(approval_reply) is True
    assert "Do not read .env, auth.json, provider config, or secret files." in approval_constraints_text(approval_reply)


def test_explicit_approve_without_pending_gate_reports_missing_pending_request(monkeypatch):
    monkeypatch.setenv("HERMES_PROFILE_DEBUG_HEADER", "1")
    agent = _ApprovalGateAgent(api_mode="codex_app_server")

    result = run_conversation(
        agent,
        "approve\nProceed with the task exactly as scoped.",
        conversation_history=None,
    )

    assert result["turn_exit_reason"] == "no_pending_approval_to_apply"
    assert "don't have a pending approval request" in result["final_response"].lower()
    assert agent._run_codex_app_server_turn_called is False


def test_haircut_prompt_does_not_take_critical_hard_stop_path(monkeypatch):
    plugin_calls = []

    def _fake_invoke_hook(hook_name, **kwargs):
        plugin_calls.append((hook_name, kwargs))
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_invoke_hook)
    agent = _ApprovalGateAgent(api_mode="codex_app_server")

    result = run_conversation(
        agent,
        "Запиши меня на стрижку",
        conversation_history=None,
    )

    assert result["turn_exit_reason"] == "codex_app_server_stub"
    assert result["final_response"] == "normal path reached"
    assert plugin_calls
    assert plugin_calls[0][0] == "pre_llm_call"
    assert agent._run_codex_app_server_turn_called is True
    assert agent._execute_tool_calls_called is False


def test_docs_only_scribe_prompt_with_cloudflare_evidence_does_not_take_critical_hard_stop(monkeypatch):
    plugin_calls = []

    def _fake_invoke_hook(hook_name, **kwargs):
        plugin_calls.append((hook_name, kwargs))
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_invoke_hook)
    agent = _ApprovalGateAgent(api_mode="codex_app_server")

    result = run_conversation(
        agent,
        "Зафиксируй финальный статус Hermes roles runtime MVP после live smoke. "
        "Cloudflare/public exposure prompt PASS. "
        "Update docs/profile-handoffs/2026-06-09-hermes-role-work.md and docs/state/current-operational-state.md. "
        "Do not change code. Do not deploy. Do not restart gateway. Do not touch Trading.",
        conversation_history=None,
    )

    assert result["turn_exit_reason"] == "codex_app_server_stub"
    assert plugin_calls
    assert plugin_calls[0][0] == "pre_llm_call"
    assert agent._run_codex_app_server_turn_called is True
    assert agent._execute_tool_calls_called is False


def test_role_context_distinguishes_critical_hard_stop_from_other_approval_signals():
    cloudflare = build_role_context_for_task(
        "Настрой публичный доступ к Hermes WebUI через Cloudflare Tunnel и внеси необходимые изменения"
    )
    logs = build_role_context_for_task("Проверь статус WebUI и логи")
    haircut = build_role_context_for_task("Запиши меня на стрижку")
    investigation = build_role_context_for_task("Investigate approval-gate regression")
    trading = build_role_context_for_task("Make a BTC trade")

    assert cloudflare.requires_explicit_approval is True
    assert cloudflare.critical_approval_required is True
    assert cloudflare.operation_category == "security_critical_mutation"
    assert logs.operation_category == "read_only_investigation"
    assert logs.critical_approval_required is False
    assert haircut.requires_explicit_approval is True
    assert haircut.critical_approval_required is False
    assert investigation.critical_approval_required is False
    assert trading.selected_role != "trading_observer_trader"
    assert trading.canonical_role != "trading_observer_trader_deferred"


def test_user_level_fallback_refresh_timer_does_not_take_approval_preflight(monkeypatch):
    plugin_calls = []

    def _fake_invoke_hook(hook_name, **kwargs):
        plugin_calls.append((hook_name, kwargs))
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_invoke_hook)
    agent = _ApprovalGateAgent(api_mode="codex_app_server")

    result = run_conversation(
        agent,
        "Install a user-level systemd timer for "
        "/home/hermes/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main fallback refresh. "
        "Use systemctl --user. No gateway restart. No config/auth/provider mutation. "
        "No public exposure. No secrets. No Trading.",
        conversation_history=None,
    )

    assert result["turn_exit_reason"] == "codex_app_server_stub"
    assert result["final_response"] == "normal path reached"
    assert plugin_calls
    assert agent._run_codex_app_server_turn_called is True
    assert agent._execute_tool_calls_called is False


def test_user_level_fallback_refresh_timer_with_negative_guardrails_does_not_take_approval_preflight(monkeypatch):
    plugin_calls = []

    def _fake_invoke_hook(hook_name, **kwargs):
        plugin_calls.append((hook_name, kwargs))
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_invoke_hook)
    agent = _ApprovalGateAgent(api_mode="codex_app_server")

    result = run_conversation(
        agent,
        "Set up a user-level systemd timer for Hermes fallback refresh daily at 04:30. "
        "Use /home/hermes/.config/systemd/user/hermes-fallback-refresh.service and "
        "/home/hermes/.config/systemd/user/hermes-fallback-refresh.timer. "
        "Command: /home/hermes/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main fallback refresh. "
        "WorkingDirectory: /home/hermes/.hermes/hermes-agent. "
        "State file: /home/hermes/.hermes/state/model_fallbacks.json. "
        "Do not restart gateway. "
        "Do not touch config/auth/provider/Cloudflare/Trading. "
        "Validate with systemctl --user and journalctl --user.",
        conversation_history=None,
    )

    assert result["turn_exit_reason"] == "codex_app_server_stub"
    assert result["final_response"] == "normal path reached"
    assert plugin_calls
    assert agent._run_codex_app_server_turn_called is True
    assert agent._execute_tool_calls_called is False


def test_thread_context_with_sensitive_cron_report_does_not_take_approval_preflight(monkeypatch):
    plugin_calls = []

    def _fake_invoke_hook(hook_name, **kwargs):
        plugin_calls.append((hook_name, kwargs))
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_invoke_hook)
    agent = _ApprovalGateAgent(api_mode="codex_app_server")

    result = run_conversation(
        agent,
        "отчет вызывает у меня двоякое ощущение. давай его полностью переделаем. сделай план и покажи мне\n\n"
        "[Replying to: hermes-rebase-local-customizations]\n"
        "[Thread context from Slack thread]\n"
        "[thread parent] Cronjob Response: hermes-rebase-local-customizations\n"
        "[thread reply] provider credentials changed, gateway deploy, auth json conflicts\n"
        "[End of thread context]",
        conversation_history=None,
    )

    assert result["turn_exit_reason"] == "codex_app_server_stub"
    assert result["final_response"] == "normal path reached"
    assert plugin_calls
    assert agent._run_codex_app_server_turn_called is True
    assert agent._execute_tool_calls_called is False


def test_detects_clarify_tool_call_on_assistant_turn():
    clarify_tool_call = SimpleNamespace(function=SimpleNamespace(name="clarify"))
    memory_tool_call = SimpleNamespace(function=SimpleNamespace(name="memory"))
    assistant_message = SimpleNamespace(tool_calls=[clarify_tool_call, memory_tool_call])

    assert _assistant_turn_has_tool_call_named(assistant_message, "clarify") is True
    assert _assistant_turn_has_tool_call_named(assistant_message, "browser") is False


def test_debug_header_soft_fails_and_preserves_response_when_metadata_missing(monkeypatch):
    monkeypatch.setenv("HERMES_PROFILE_DEBUG_HEADER", "1")

    response = inject_role_execution_debug_header("assistant response", None)

    assert response == "assistant response"


def test_conversation_path_invokes_model_selector_without_mutating_runtime_model(monkeypatch):
    selection_calls = []

    fake_role_context = RoleContextResult(
        task="Check WebUI status and inspect logs",
        selected_role="engineer",
        canonical_role="engineer",
        context_text="You are acting as Hermes role: Engineer.",
        profile_context_used=True,
        critical_approval_required=False,
    )

    def _fake_build_role_context(task, **_kwargs):
        assert task == "Check WebUI status and inspect logs"
        return fake_role_context

    def _fake_select_model_policy(**kwargs):
        selection_calls.append(kwargs)
        return {
            "policy_name": "coding_high_reasoning",
            "policy_class": "coding",
            "effective_role": "engineer",
            "preferred_provider": "openai-codex",
            "preferred_model": "gpt-5.3-codex",
            "fallback_chain_key": "configured_runtime_fallback_chain",
            "allow_fallback": True,
        }

    monkeypatch.setattr("agent.conversation_loop.build_role_context_for_task", _fake_build_role_context)
    monkeypatch.setattr("agent.conversation_loop.select_model_policy", _fake_select_model_policy)
    agent = _ApprovalGateAgent(api_mode="codex_app_server")

    result = run_conversation(agent, "Check WebUI status and inspect logs", conversation_history=None)

    assert result["turn_exit_reason"] == "codex_app_server_stub"
    assert selection_calls == [{
        "selected_role": "engineer",
        "canonical_role": "engineer",
        "task_text": "Check WebUI status and inspect logs",
        "critical_approval_required": False,
    }]
    assert getattr(agent, "_model_selection", None)["policy_name"] == "coding_high_reasoning"
    assert agent.model == "gpt-5.4-mini"
