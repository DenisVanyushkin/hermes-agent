"""Fork: the Codex stream guard against text-bound tool intent (agent/local_codex_protocol.py).

Restored from the fork's tests/run_agent/test_run_agent_codex_responses.py, which upstream moved
and rewrote: only the stream-guard cases, with the helpers they use.
"""

import hashlib


import sys


import types


from types import SimpleNamespace


import pytest


sys.modules.setdefault("fire", types.SimpleNamespace(Fire=lambda *a, **k: None))


sys.modules.setdefault("firecrawl", types.SimpleNamespace(Firecrawl=object))


sys.modules.setdefault("fal_client", types.SimpleNamespace())


import run_agent


@pytest.fixture(autouse=True)
def _no_codex_backoff(monkeypatch):
    """Short-circuit retry backoff so Codex retry tests don't block on real
    wall-clock waits (5s jittered_backoff base delay + tight time.sleep loop)."""
    import time as _time
    monkeypatch.setattr("agent.retry_utils.jittered_backoff", lambda *a, **k: 0.0)
    monkeypatch.setattr(_time, "sleep", lambda *_a, **_k: None)


def _patch_agent_bootstrap(monkeypatch):
    monkeypatch.setattr(
        "model_tools.get_tool_definitions",
        lambda **kwargs: [
            {
                "type": "function",
                "function": {
                    "name": "terminal",
                    "description": "Run shell commands.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )
    monkeypatch.setattr("model_tools.check_toolset_requirements", dict)


def _build_agent(monkeypatch):
    _patch_agent_bootstrap(monkeypatch)

    agent = run_agent.AIAgent(
        model="gpt-5-codex",
        base_url="https://chatgpt.com/backend-api/codex",
        api_key="codex-token",
        quiet_mode=True,
        max_iterations=4,
        skip_context_files=True,
        skip_memory=True,
    )
    agent._cleanup_task_resources = lambda task_id: None
    agent._persist_session = lambda messages, history=None: None
    agent._save_trajectory = lambda messages, user_message, completed: None
    return agent


def _build_copilot_agent(monkeypatch, *, model="gpt-5.4"):
    _patch_agent_bootstrap(monkeypatch)

    agent = run_agent.AIAgent(
        model=model,
        provider="copilot",
        api_mode="codex_responses",
        base_url="https://api.githubcopilot.com",
        api_key="gh-token",
        quiet_mode=True,
        max_iterations=4,
        skip_context_files=True,
        skip_memory=True,
    )
    agent._cleanup_task_resources = lambda task_id: None
    agent._persist_session = lambda messages, history=None: None
    agent._save_trajectory = lambda messages, user_message, completed: None
    return agent


AZURE_FOUNDRY_BASE_URL = (
    "https://placeholder.services.ai.azure.com/api/projects/placeholder/openai/v1"
)


def _build_azure_foundry_agent(monkeypatch, *, model="gpt-5.4"):
    _patch_agent_bootstrap(monkeypatch)

    agent = run_agent.AIAgent(
        model=model,
        provider="azure-foundry",
        api_mode="codex_responses",
        base_url=AZURE_FOUNDRY_BASE_URL,
        api_key="foundry-token",
        quiet_mode=True,
        max_iterations=4,
        skip_context_files=True,
        skip_memory=True,
    )
    agent._cleanup_task_resources = lambda task_id: None
    agent._persist_session = lambda messages, history=None: None
    agent._save_trajectory = lambda messages, user_message, completed: None
    return agent


def _codex_message_response(text: str):
    return SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text=text)],
            )
        ],
        usage=SimpleNamespace(input_tokens=5, output_tokens=3, total_tokens=8),
        status="completed",
        model="gpt-5-codex",
    )


def _codex_tool_call_response():
    return SimpleNamespace(
        output=[
            SimpleNamespace(
                type="function_call",
                id="fc_1",
                call_id="call_1",
                name="terminal",
                arguments="{}",
            )
        ],
        usage=SimpleNamespace(input_tokens=12, output_tokens=4, total_tokens=16),
        status="completed",
        model="gpt-5-codex",
    )


def _codex_incomplete_message_response(text: str):
    return SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                status="in_progress",
                content=[SimpleNamespace(type="output_text", text=text)],
            )
        ],
        usage=SimpleNamespace(input_tokens=4, output_tokens=2, total_tokens=6),
        status="in_progress",
        model="gpt-5-codex",
    )


def _codex_max_output_incomplete_response(text: str = ""):
    content = []
    if text:
        content.append(SimpleNamespace(type="output_text", text=text))
    return SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                status="incomplete",
                content=content,
            )
        ],
        usage=SimpleNamespace(input_tokens=270_000, output_tokens=1, total_tokens=270_001),
        status="incomplete",
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        model="gpt-5-codex",
    )


def _codex_commentary_message_response(text: str):
    return SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                phase="commentary",
                status="completed",
                content=[SimpleNamespace(type="output_text", text=text)],
            )
        ],
        usage=SimpleNamespace(input_tokens=4, output_tokens=2, total_tokens=6),
        status="completed",
        model="gpt-5-codex",
    )


def _codex_commentary_final_tool_response(commentary: str, final_answer: str = "Done."):
    return SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                phase="commentary",
                status="completed",
                content=[SimpleNamespace(type="output_text", text=commentary)],
            ),
            SimpleNamespace(
                type="message",
                phase="final_answer",
                status="completed",
                content=[SimpleNamespace(type="output_text", text=final_answer)],
            ),
            SimpleNamespace(
                type="function_call",
                id="fc_1",
                call_id="call_1",
                name="terminal",
                arguments="{}",
            ),
        ],
        usage=SimpleNamespace(input_tokens=8, output_tokens=5, total_tokens=13),
        status="completed",
        model="gpt-5-codex",
    )


def _codex_ack_message_response(text: str):
    return SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                status="completed",
                content=[SimpleNamespace(type="output_text", text=text)],
            )
        ],
        usage=SimpleNamespace(input_tokens=4, output_tokens=2, total_tokens=6),
        status="completed",
        model="gpt-5-codex",
    )


def _codex_final_answer_with_top_level_incomplete_response(text: str):
    return SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                phase="final_answer",
                status="completed",
                content=[SimpleNamespace(type="output_text", text=text)],
            )
        ],
        usage=SimpleNamespace(input_tokens=4, output_tokens=2, total_tokens=6),
        status="incomplete",
        model="gpt-5.4",
    )


class _FakeCreateStream:
    """Iterable-only fake for ``responses.create(stream=True)`` outputs.

    The event-driven Codex path expects an iterable that yields SSE events;
    tests use this to drive it through the same code paths the wire does.
    """

    def __init__(self, events):
        self._events = list(events)
        self.closed = False

    def __iter__(self):
        return iter(self._events)

    def close(self):
        self.closed = True


def _codex_request_kwargs():
    return {
        "model": "gpt-5-codex",
        "instructions": "You are Hermes.",
        "input": [{"role": "user", "content": "Ping"}],
        "tools": None,
        "store": False,
    }


def _azure_reasoning_item():
    return {"type": "reasoning", "encrypted_content": "sealed", "summary": []}


def _azure_post_tool_messages():
    return [
        {"role": "system", "content": "You are Hermes."},
        {"role": "user", "content": "Create a marker"},
        {
            "role": "assistant",
            "content": "",
            "codex_reasoning_items": [_azure_reasoning_item()],
            "tool_calls": [
                {
                    "id": "call_marker",
                    "type": "function",
                    "function": {"name": "terminal", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_marker", "content": "marker written"},
    ]


def _build_xai_agent_with_slash_enum_tool(monkeypatch):
    """Build an xAI agent whose tool registry has a slash-containing enum.

    Mirrors the Brave Search MCP shape that originally triggered #27907.
    """

    def _fake_get_tool_definitions(**_kwargs):
        return [
            {
                "type": "function",
                "function": {
                    "name": "brave_like",
                    "description": "Tool with slash-containing enum + pattern/format",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "accept": {
                                "type": "string",
                                "enum": ["application/json", "*/*"],
                            },
                            "match": {
                                "type": "string",
                                "pattern": "^[a-z]+$",
                                "format": "regex",
                            },
                        },
                    },
                },
            }
        ]

    monkeypatch.setattr(
        "model_tools.get_tool_definitions", _fake_get_tool_definitions)
    monkeypatch.setattr("model_tools.check_toolset_requirements", dict)

    agent = run_agent.AIAgent(
        model="grok-4.3",
        provider="xai-oauth",
        api_mode="codex_responses",
        base_url="https://api.x.ai/v1",
        api_key="xai-token",
        quiet_mode=True,
        max_iterations=4,
        skip_context_files=True,
        skip_memory=True,
    )
    agent._cleanup_task_resources = lambda task_id: None
    agent._persist_session = lambda messages, history=None: None
    agent._save_trajectory = lambda messages, user_message, completed: None
    return agent


def _consume_message_stream(
    text,
    *,
    phase,
    deltas,
    on_reasoning_delta=None,
    on_commentary_message=None,
):
    from agent.codex_runtime import _consume_codex_event_stream

    item = SimpleNamespace(
        type="message",
        phase=phase,
        status="completed",
        content=[SimpleNamespace(type="output_text", text=text)],
    )
    events = [
        SimpleNamespace(
            type="response.output_item.added",
            item=SimpleNamespace(type="message", phase=phase),
        ),
        *[
            SimpleNamespace(type="response.output_text.delta", delta=delta)
            for delta in deltas
        ],
        SimpleNamespace(type="response.output_item.done", item=item),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(status="completed"),
        ),
    ]
    return _consume_codex_event_stream(
        _FakeCreateStream(events),
        model="gpt-5-codex",
        on_reasoning_delta=on_reasoning_delta,
        on_commentary_message=on_commentary_message,
        valid_tool_names={"skill_view"},
    )


MALFORMED_SKILL_VIEW_COMMENTARY = (
    '<|start|>assistant<|channel|>commentary '
    'to=functions.skill_view<|constrain|>json\n'
    '{"name":"test-driven-development"}'
)


def _build_xai_oauth_agent(monkeypatch):
    _patch_agent_bootstrap(monkeypatch)
    agent = run_agent.AIAgent(
        model="grok-4.3",
        provider="xai-oauth",
        api_mode="codex_responses",
        base_url="https://api.x.ai/v1",
        api_key="xai-oauth-token",
        quiet_mode=True,
        max_iterations=4,
        skip_context_files=True,
        skip_memory=True,
    )
    agent._cleanup_task_resources = lambda task_id: None
    agent._persist_session = lambda messages, history=None: None
    agent._save_trajectory = lambda messages, user_message, completed: None
    return agent


def _codex_incomplete_with_reasoning(text: str, reasoning_id: str = "rs_default"):
    """Incomplete response with a reasoning item whose id/encrypted_content
    can vary independently of the visible message text."""
    return SimpleNamespace(
        output=[
            SimpleNamespace(
                type="reasoning",
                id=reasoning_id,
                encrypted_content=f"opaque_{reasoning_id}",
                summary=[SimpleNamespace(text="thinking...")],
            ),
            SimpleNamespace(
                type="message",
                status="in_progress",
                content=[SimpleNamespace(type="output_text", text=text)],
            ),
        ],
        usage=SimpleNamespace(input_tokens=4, output_tokens=2, total_tokens=6),
        status="in_progress",
        model="gpt-5-codex",
    )


def _codex_reasoning_only_response(*, encrypted_content="enc_abc123", summary_text="Thinking..."):
    """Codex response containing only reasoning items — no message text, no tool calls."""
    return SimpleNamespace(
        output=[
            SimpleNamespace(
                type="reasoning",
                id="rs_001",
                encrypted_content=encrypted_content,
                summary=[SimpleNamespace(type="summary_text", text=summary_text)],
                status="completed",
            )
        ],
        usage=SimpleNamespace(input_tokens=50, output_tokens=100, total_tokens=150),
        status="completed",
        model="gpt-5-codex",
    )



def test_consume_codex_stream_preserves_analysis_delta_streaming():
    """Ordinary analysis remains incremental after the prefix is disproven."""
    from agent.codex_runtime import _consume_codex_event_stream

    analysis_item = SimpleNamespace(
        type="message",
        phase="analysis",
        status="completed",
        content=[SimpleNamespace(type="output_text", text="Need to inspect files.")],
    )
    reasoning_streamed = []

    _consume_codex_event_stream(
        _FakeCreateStream([
            SimpleNamespace(
                type="response.output_item.added",
                item=SimpleNamespace(type="message", phase="analysis"),
            ),
            SimpleNamespace(type="response.output_text.delta", delta="Need to "),
            SimpleNamespace(type="response.output_text.delta", delta="inspect files."),
            SimpleNamespace(type="response.output_item.done", item=analysis_item),
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(status="completed"),
            ),
        ]),
        model="gpt-5-codex",
        on_reasoning_delta=reasoning_streamed.append,
    )

    assert reasoning_streamed == ["Need to ", "inspect files."]



@pytest.mark.parametrize(
    "xml_text",
    [
        "<tool_call><name>skill_view</name></tool_call>",
        "<TOOL_CALL><NAME>skill_view</NAME></TOOL_CALL>",
    ],
)
def test_consume_codex_stream_suppresses_fragmented_xml_protocol_prefixes(xml_text):
    """Every split inside the XML prefix remains fail-closed and case-insensitive."""
    from agent.codex_runtime import _consume_codex_event_stream

    prefix_length = len("<tool_call>")
    for split_at in range(1, prefix_length):
        analysis_item = SimpleNamespace(
            type="message",
            phase="analysis",
            status="completed",
            content=[SimpleNamespace(type="output_text", text=xml_text)],
        )
        visible = []
        response = _consume_codex_event_stream(
            _FakeCreateStream([
                SimpleNamespace(
                    type="response.output_item.added",
                    item=SimpleNamespace(type="message", phase="analysis"),
                ),
                SimpleNamespace(
                    type="response.output_text.delta",
                    delta=xml_text[:split_at],
                ),
                SimpleNamespace(
                    type="response.output_text.delta",
                    delta=xml_text[split_at:],
                ),
                SimpleNamespace(type="response.output_item.done", item=analysis_item),
                SimpleNamespace(
                    type="response.completed",
                    response=SimpleNamespace(status="completed"),
                ),
            ]),
            model="gpt-5-codex",
            on_reasoning_delta=visible.append,
            valid_tool_names={"skill_view"},
        )

        assert visible == [], f"XML leaked for split {split_at}: {xml_text!r}"
        assert response._hermes_malformed_tool_intent.tool_name == "skill_view"



def test_consume_codex_stream_does_not_duplicate_commentary_candidate_on_transition():
    """A disproven protocol prefix moves from candidate to commentary once."""
    from agent.codex_runtime import _consume_codex_event_stream

    commentary_text = "<not protocol"
    commentary_item = SimpleNamespace(
        type="message",
        phase="commentary",
        status="completed",
        content=[SimpleNamespace(type="output_text", text=commentary_text)],
    )
    delivered = []

    _consume_codex_event_stream(
        _FakeCreateStream([
            SimpleNamespace(
                type="response.output_item.added",
                item=SimpleNamespace(type="message", phase="commentary"),
            ),
            SimpleNamespace(type="response.output_text.delta", delta="<"),
            SimpleNamespace(type="response.output_text.delta", delta="not protocol"),
            SimpleNamespace(type="response.output_item.done", item=commentary_item),
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(status="completed"),
            ),
        ]),
        model="gpt-5-codex",
        on_commentary_message=delivered.append,
        valid_tool_names={"skill_view"},
    )

    assert delivered == [commentary_text]



def test_consume_codex_stream_suppresses_oversized_xml_candidate(caplog):
    """An ambiguous XML candidate at the bound remains suppressed with safe evidence."""
    from agent.malformed_tool_intent import MAX_INSPECTED_TEXT_CHARS

    sentinel = "OVERSIZED_ARGUMENT_SENTINEL"
    xml_text = (
        "<tool_call><name>skill_view</name><arguments>"
        + sentinel
        + "x" * (MAX_INSPECTED_TEXT_CHARS + 100)
        + "</arguments></tool_call>"
    )
    visible = []

    response = _consume_message_stream(
        xml_text,
        phase="analysis",
        deltas=[xml_text],
        on_reasoning_delta=visible.append,
    )

    assert visible == []
    evidence = response._hermes_malformed_tool_intent
    assert evidence is not None
    assert evidence.tool_name == "unknown"
    assert evidence.format == "oversized_protocol_candidate"
    assert evidence.fingerprint == "sha256:" + hashlib.sha256(xml_text.encode()).hexdigest()
    assert set(vars(evidence)) == {"tool_name", "source_phase", "format", "fingerprint"}
    assert sentinel not in repr(evidence)
    assert sentinel not in caplog.text
    assert not any(getattr(item, "type", None) == "function_call" for item in response.output)



def test_consume_codex_stream_preserves_remainder_after_large_ordinary_analysis_delta():
    """A single ordinary delta crossing the bound is fully delivered in order."""
    from agent.malformed_tool_intent import MAX_INSPECTED_TEXT_CHARS

    text = "<not protocol " + "a" * (MAX_INSPECTED_TEXT_CHARS + 100)
    visible = []

    _consume_message_stream(
        text,
        phase="analysis",
        deltas=[text],
        on_reasoning_delta=visible.append,
    )

    assert "".join(visible) == text
    assert visible[0] == text[:MAX_INSPECTED_TEXT_CHARS]
    assert visible[1] == text[MAX_INSPECTED_TEXT_CHARS:]



def test_consume_codex_stream_preserves_long_ordinary_analysis_delta_order():
    """Long ordinary analysis keeps every original delta exactly once."""
    deltas = ["<not", " protocol", " analysis: ", "b" * 20_000, " tail"]
    visible = []

    _consume_message_stream(
        "".join(deltas),
        phase="analysis",
        deltas=deltas,
        on_reasoning_delta=visible.append,
    )

    assert visible == deltas



def test_consume_codex_stream_delivers_long_ordinary_commentary_once():
    """Long ordinary commentary crossing the bound is delivered byte-for-byte once."""
    from agent.malformed_tool_intent import MAX_INSPECTED_TEXT_CHARS

    text = "<not protocol " + "c" * (MAX_INSPECTED_TEXT_CHARS + 100)
    delivered = []

    _consume_message_stream(
        text,
        phase="commentary",
        deltas=[text],
        on_commentary_message=delivered.append,
    )

    assert delivered == [text]



def test_consume_codex_stream_suppresses_malformed_text_tool_intent(monkeypatch):
    from agent.codex_runtime import _consume_codex_event_stream

    commentary_item = SimpleNamespace(
        type="message",
        phase="commentary",
        status="completed",
        content=[
            SimpleNamespace(
                type="output_text",
                text=MALFORMED_SKILL_VIEW_COMMENTARY,
            )
        ],
    )
    delivered_commentary = []

    response = _consume_codex_event_stream(
        _FakeCreateStream([
            SimpleNamespace(
                type="response.output_item.added",
                item=SimpleNamespace(type="message", phase="commentary"),
            ),
            SimpleNamespace(
                type="response.output_text.delta",
                delta=MALFORMED_SKILL_VIEW_COMMENTARY,
            ),
            SimpleNamespace(
                type="response.output_item.done",
                item=commentary_item,
            ),
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(status="completed"),
            ),
        ]),
        model="gpt-5-codex",
        on_commentary_message=delivered_commentary.append,
        valid_tool_names={"skill_view"},
    )

    assert delivered_commentary == []
    assert response._hermes_malformed_tool_intent.tool_name == "skill_view"
    assert response._hermes_malformed_tool_intent.source_phase == "commentary"
    assert not any(
        getattr(item, "type", None) == "function_call"
        for item in response.output
    )



def test_interim_commentary_precedes_content_from_real_codex_normalization(monkeypatch):
    """Structured commentary wins over final-answer content on tool turns."""
    agent = _build_agent(monkeypatch)
    from agent.codex_responses_adapter import _normalize_codex_response

    observed = {}
    agent.interim_assistant_callback = lambda text, *, already_streamed=False: observed.update(
        {"text": text, "already_streamed": already_streamed}
    )

    normalized, finish_reason = _normalize_codex_response(
        _codex_commentary_final_tool_response("I'll inspect the repo first.")
    )
    assert finish_reason == "tool_calls"
    assert normalized.content == "Done."
    agent._emit_interim_assistant_message(
        agent._build_assistant_message(normalized, finish_reason)
    )

    assert observed == {
        "text": "I'll inspect the repo first.",
        "already_streamed": False,
    }

