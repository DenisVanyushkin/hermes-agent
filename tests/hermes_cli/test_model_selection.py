from types import SimpleNamespace

from agent.chat_completion_helpers import build_api_kwargs
from hermes_cli.model_selection import select_model_policy


def test_engineer_code_task_selects_coding_high_reasoning_policy():
    selection = select_model_policy(
        selected_role="engineer",
        canonical_role="engineer",
        task_text="Implement deterministic model selection for Hermes and run pytest",
        critical_approval_required=False,
    )

    assert selection.policy_class == "coding"
    assert selection.policy_name == "coding_high_reasoning"
    assert selection.preferred_provider == "openrouter"
    assert selection.preferred_model == "xiaomi/mimo-v2.5-pro"
    assert selection.allow_fallback is True
    assert selection.fallback_chain_key == "coding_then_reasoning_then_standard"



def test_engineer_coding_policy_declares_expected_fallback_chain():
    selection = select_model_policy(
        selected_role="engineer",
        canonical_role="engineer",
        task_text="Fix the failing pytest suite",
        critical_approval_required=False,
    )

    assert selection.policy_class == "coding"
    assert selection.fallback_chain_key == "coding_then_reasoning_then_standard"


def test_webui_logs_task_selects_engineering_policy():
    selection = select_model_policy(
        selected_role="engineer",
        canonical_role="engineer",
        task_text="Check WebUI status and inspect logs",
        critical_approval_required=False,
    )

    assert selection.policy_class == "coding"
    assert selection.effective_role == "engineer"


def test_scribe_handoff_task_selects_stable_text_policy():
    selection = select_model_policy(
        selected_role="scribe",
        canonical_role="scribe",
        task_text="Write a handoff for today's Hermes role work",
        critical_approval_required=False,
    )

    assert selection.policy_class == "scribe"
    assert selection.policy_name == "scribe_stable_text"
    assert selection.preferred_model == "gpt-5.4-mini"




def test_runtime_request_selection_overrides_session_defaults(monkeypatch):
    monkeypatch.setattr("providers.get_provider_profile", lambda provider: None, raising=False)

    class DummyTransport:
        def build_kwargs(self, **kwargs):
            return kwargs

    agent = SimpleNamespace(
        tools=[],
        api_mode="chat_completions",
        provider="openai-codex",
        model="gpt-5.4-mini",
        base_url="https://chatgpt.com/backend-api/codex",
        _base_url_lower="https://chatgpt.com/backend-api/codex",
        _base_url_hostname="chatgpt.com",
        reasoning_config=None,
        request_overrides={},
        session_id="sess-1",
        max_tokens=4096,
        providers_allowed=[],
        providers_ignored=[],
        providers_order=[],
        provider_sort=None,
        provider_require_parameters=False,
        provider_data_collection=None,
        openrouter_min_coding_score=0,
        _ollama_num_ctx=None,
        _ephemeral_max_output_tokens=None,
        _get_transport=lambda: DummyTransport(),
        _prepare_messages_for_non_vision_model=lambda messages: messages,
        _resolved_api_call_timeout=lambda: 30,
        _max_tokens_param=lambda _value: None,
        _supports_reasoning_extra_body=lambda: False,
        _github_models_reasoning_extra_body=lambda: None,
        _lmstudio_reasoning_options_cached=lambda: None,
        _qwen_prepare_chat_messages=lambda messages: messages,
        _qwen_prepare_chat_messages_inplace=lambda messages: messages,
        _is_qwen_portal=lambda: False,
    )
    agent._turn_runtime_request = {
        "selected_role": "engineer",
        "selected_provider": "openrouter",
        "selected_model": "xiaomi/mimo-v2.5-pro",
        "actual_provider": "openrouter",
        "actual_model": "xiaomi/mimo-v2.5-pro",
        "actual_base_url": "https://openrouter.ai/api/v1",
        "actual_api_mode": "chat_completions",
        "policy_name": "coding_high_reasoning",
        "policy_class": "coding",
        "fallback_used": False,
        "fallback_reason": "",
    }

    kwargs = build_api_kwargs(agent, [{"role": "user", "content": "fix the bug"}])

    assert kwargs["model"] == "xiaomi/mimo-v2.5-pro"
    assert kwargs["provider_name"] == "openrouter"
    assert kwargs["base_url"] == "https://openrouter.ai/api/v1"
    assert kwargs["model_lower"] == "xiaomi/mimo-v2.5-pro"
    assert kwargs["is_openrouter"] is True
    assert kwargs["is_custom_provider"] is False


def test_general_operator_haircut_task_selects_default_policy():
    selection = select_model_policy(
        selected_role="general_operator",
        canonical_role="general_operator",
        task_text="Book me a haircut",
        critical_approval_required=False,
    )

    assert selection.policy_class == "general_operator"
    assert selection.policy_name == "general_default"
    assert selection.preferred_model == "gpt-5.4-mini"


def test_research_complex_synthesis_selects_reasoning_model():
    selection = select_model_policy(
        selected_role="researcher",
        canonical_role="researcher",
        task_text="Perform deep research and synthesize conflicting sources into a brief",
        critical_approval_required=False,
    )

    assert selection.policy_class == "research"
    assert selection.policy_name == "research_reasoning"
    assert selection.preferred_model == "gpt-5.4"


def test_critical_approval_cloudflare_task_selects_approval_critical_policy():
    selection = select_model_policy(
        selected_role="engineer",
        canonical_role="engineer",
        task_text="Configure public Hermes WebUI access via Cloudflare Tunnel",
        critical_approval_required=True,
    )

    assert selection.policy_class == "approval_critical"
    assert selection.policy_name == "approval_critical"
    assert selection.preferred_model == "gpt-5.5"
    assert selection.allow_fallback is False
    assert selection.fallback_chain_key == "stop_and_escalate"


def test_trading_role_is_not_used_as_a_model_policy():
    selection = select_model_policy(
        selected_role="trading_observer_trader",
        canonical_role="trading_observer_trader_deferred",
        task_text="Buy BTC",
        critical_approval_required=False,
    )
    assert selection.policy_class == "general_operator"


def test_runtime_request_selection_overrides_session_defaults(monkeypatch):
    monkeypatch.setattr("providers.get_provider_profile", lambda provider: None, raising=False)

    class DummyTransport:
        def build_kwargs(self, **kwargs):
            return kwargs

    agent = SimpleNamespace(
        tools=[],
        api_mode="chat_completions",
        provider="openai-codex",
        model="gpt-5.4-mini",
        base_url="https://chatgpt.com/backend-api/codex",
        _base_url_lower="https://chatgpt.com/backend-api/codex",
        _base_url_hostname="chatgpt.com",
        reasoning_config=None,
        request_overrides={},
        session_id="sess-1",
        max_tokens=4096,
        providers_allowed=[],
        providers_ignored=[],
        providers_order=[],
        provider_sort=None,
        provider_require_parameters=False,
        provider_data_collection=None,
        openrouter_min_coding_score=0,
        _ollama_num_ctx=None,
        _ephemeral_max_output_tokens=None,
        _get_transport=lambda: DummyTransport(),
        _prepare_messages_for_non_vision_model=lambda messages: messages,
        _resolved_api_call_timeout=lambda: 30,
        _max_tokens_param=lambda _value: None,
        _supports_reasoning_extra_body=lambda: False,
        _github_models_reasoning_extra_body=lambda: None,
        _lmstudio_reasoning_options_cached=lambda: None,
        _qwen_prepare_chat_messages=lambda messages: messages,
        _qwen_prepare_chat_messages_inplace=lambda messages: messages,
        _is_qwen_portal=lambda: False,
    )
    agent._turn_runtime_request = {
        "selected_role": "engineer",
        "selected_provider": "openrouter",
        "selected_model": "xiaomi/mimo-v2.5-pro",
        "actual_provider": "openrouter",
        "actual_model": "xiaomi/mimo-v2.5-pro",
        "actual_base_url": "https://openrouter.ai/api/v1",
        "actual_api_mode": "chat_completions",
        "policy_name": "coding_high_reasoning",
        "policy_class": "coding",
        "fallback_used": False,
        "fallback_reason": "",
    }

    kwargs = build_api_kwargs(agent, [{"role": "user", "content": "fix the bug"}])

    assert kwargs["model"] == "xiaomi/mimo-v2.5-pro"
    assert kwargs["provider_name"] == "openrouter"
    assert kwargs["base_url"] == "https://openrouter.ai/api/v1"
    assert kwargs["model_lower"] == "xiaomi/mimo-v2.5-pro"
    assert kwargs["is_openrouter"] is True
    assert kwargs["is_custom_provider"] is False
