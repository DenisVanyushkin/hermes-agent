from types import SimpleNamespace

from agent.malformed_tool_intent import detect_malformed_tool_intent


VALID_TOOLS = {"skill_view", "terminal"}


def test_detects_codex_chatml_recipient_envelope():
    result = detect_malformed_tool_intent(
        '<|start|>assistant<|channel|>commentary '
        'to=functions.skill_view<|constrain|>json\n'
        '{"name":"test-driven-development"}',
        phase="commentary",
        valid_tool_names=VALID_TOOLS,
    )

    assert result is not None
    assert result.tool_name == "skill_view"
    assert result.source_phase == "commentary"
    assert result.format == "codex_chatml"
    assert result.fingerprint.startswith("sha256:")


def test_detects_generic_tool_call_xml_without_parsing_for_execution():
    result = detect_malformed_tool_intent(
        '<tool_call>{"name":"terminal","arguments":{"command":"pwd"}}'
        "</tool_call>",
        phase="analysis",
        valid_tool_names=VALID_TOOLS,
    )

    assert result is not None
    assert result.tool_name == "terminal"
    assert not hasattr(result, "arguments")


def test_rejects_unknown_tool_name():
    assert detect_malformed_tool_intent(
        '<|start|>assistant<|channel|>commentary '
        'to=functions.not_registered<|constrain|>json\n{}',
        phase="commentary",
        valid_tool_names=VALID_TOOLS,
    ) is None


def test_ignores_same_text_in_final_answer_phase():
    assert detect_malformed_tool_intent(
        '<|start|>assistant<|channel|>commentary '
        'to=functions.skill_view<|constrain|>json\n{}',
        phase="final_answer",
        valid_tool_names=VALID_TOOLS,
    ) is None


def test_ignores_documentation_code_fence_without_commentary_boundary():
    assert detect_malformed_tool_intent(
        "```xml\n<tool_call><name>terminal</name></tool_call>\n```",
        phase="commentary",
        valid_tool_names=VALID_TOOLS,
    ) is None


def test_never_returns_arguments_as_executable_mapping():
    result = detect_malformed_tool_intent(
        '<tool_call><name>terminal</name><arguments>{"command":"id"}'
        "</arguments></tool_call>",
        phase="commentary",
        valid_tool_names=VALID_TOOLS,
    )

    assert result is not None
    assert result.__dict__.keys() == {
        "tool_name",
        "source_phase",
        "format",
        "fingerprint",
    }


def test_codex_transport_exposes_only_bounded_metadata():
    from agent.codex_responses_adapter import _normalize_codex_response
    from agent.transports import get_transport

    intent = SimpleNamespace(
        tool_name="skill_view",
        source_phase="commentary",
        format="codex_chatml",
        fingerprint="sha256:" + "b" * 64,
    )
    response = SimpleNamespace(
        output=[SimpleNamespace(
            type="message",
            phase="commentary",
            status="completed",
            content=[SimpleNamespace(type="output_text", text="protocol")],
        )],
        output_text="",
        status="completed",
        _hermes_malformed_tool_intent=intent,
    )

    message, _ = _normalize_codex_response(response)
    assert message.malformed_tool_intent is intent

    normalized = get_transport("codex_responses").normalize_response(response)
    assert normalized.malformed_tool_intent == {
        "tool_name": "skill_view",
        "source_phase": "commentary",
        "format": "codex_chatml",
        "fingerprint": "sha256:" + "b" * 64,
    }
