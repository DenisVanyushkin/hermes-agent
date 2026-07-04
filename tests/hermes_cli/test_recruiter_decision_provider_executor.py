from __future__ import annotations

import json
from typing import Any

from hermes_cli.recruiter_decision_provider_executor import (
    RecruiterDecisionProviderExecutor,
)


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, content: str) -> None:
        self.completions = _FakeCompletions(content)


class _FakeClient:
    def __init__(self, content: str) -> None:
        self.chat = _FakeChat(content)


def _executor(content: str) -> tuple[RecruiterDecisionProviderExecutor, _FakeClient]:
    client = _FakeClient(content)
    return (
        RecruiterDecisionProviderExecutor(client=client, model="test-model", provider="test"),
        client,
    )


def test_executes_module_and_extracts_confidence_and_sources(monkeypatch) -> None:
    monkeypatch.setattr(
        "hermes_cli.recruiter_decision_provider_executor._extra_body", lambda: {}
    )
    payload = {
        "recommendation": "worth_engaging",
        "confidence": "high",
        "summary": "Concrete company summary",
        "sources": ["https://example.com/press"],
    }
    executor, client = _executor(json.dumps(payload))

    execution = executor.execute(
        module_id="company_assessment",
        skill_id="company-assessment",
        module_input={"company_identity": "Example Corp"},
    )

    assert execution.errors == []
    assert execution.confidence == "high"
    assert execution.sources == ["https://example.com/press"]
    assert execution.payload["recommendation"] == "worth_engaging"
    assert execution.payload["confidence"] == "high"  # kept in payload for output validation

    call = client.chat.completions.calls[0]
    prompt = call["messages"][0]["content"]
    assert "company_assessment" in prompt
    assert "Example Corp" in prompt
    assert call["temperature"] == 0


def test_invalid_json_returns_error_not_exception(monkeypatch) -> None:
    monkeypatch.setattr(
        "hermes_cli.recruiter_decision_provider_executor._extra_body", lambda: {}
    )
    executor, _ = _executor("not json at all")

    execution = executor.execute(
        module_id="recommendation",
        skill_id="fit-recommendation",
        module_input={},
    )

    assert execution.payload == {}
    assert "decision_provider_output_invalid_json" in execution.errors
