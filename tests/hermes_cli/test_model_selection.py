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


def test_career_vacancy_task_selects_balanced_career_policy():
    selection = select_model_policy(
        selected_role="career_strategist",
        canonical_role="career_strategist",
        task_text="Evaluate this Head of Product vacancy and suggest an application strategy",
        critical_approval_required=False,
    )

    assert selection.policy_class == "career_strategist"
    assert selection.policy_name == "career_balanced_reasoning"


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


def test_research_btc_fee_comparison_selects_research_policy():
    selection = select_model_policy(
        selected_role="researcher",
        canonical_role="researcher",
        task_text="Compare Binance Kazakhstan vs Coinbase fees for BTC",
        critical_approval_required=False,
    )

    assert selection.policy_class == "research"
    assert selection.policy_name == "research_fast_lookup"


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

