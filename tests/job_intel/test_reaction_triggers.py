"""Tests for reaction-triggered vacancy tasks."""
from __future__ import annotations

from job_intel import reaction_triggers as rt


MESSAGE = {
    "title": "Head of Product",
    "company": "Acme",
    "canonical_url": "https://acme.example/jobs/1",
    "url": "https://acme.example/jobs/1#dup:42",
}


def test_classify_trigger():
    assert rt.classify_trigger("mag") == "evaluation"
    assert rt.classify_trigger("+1") == "package"
    assert rt.classify_trigger("thumbsup") == "package"
    assert rt.classify_trigger("thumbs_up") == "package"
    assert rt.classify_trigger("MAG") == "evaluation"  # case-insensitive
    assert rt.classify_trigger("-1") is None
    assert rt.classify_trigger("eyes") is None
    assert rt.classify_trigger("") is None


def test_build_evaluation_prompt_is_russian_and_has_url():
    prompt = rt.build_trigger_prompt("evaluation", MESSAGE)
    assert "https://acme.example/jobs/1" in prompt          # canonical, not #dup url
    assert "#dup:" not in prompt
    assert "Head of Product" in prompt and "Acme" in prompt
    assert "vacancy-evaluation" in prompt and "company-assessment" in prompt
    assert "русском" in prompt


def test_build_package_prompt_is_english_and_has_url():
    prompt = rt.build_trigger_prompt("package", MESSAGE)
    assert "https://acme.example/jobs/1" in prompt
    assert "application-package-orchestrator" in prompt
    assert "English" in prompt


def test_build_prompt_falls_back_to_url_when_no_canonical():
    msg = dict(MESSAGE, canonical_url=None)
    prompt = rt.build_trigger_prompt("evaluation", msg)
    assert "https://acme.example/jobs/1#dup:42" in prompt


def test_should_process_dedups(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    monkeypatch.setenv("VACANCY_REACTION_TRIGGER_STATE_FILE", str(state))
    assert rt.should_process(channel="C1", message_ts="1.1", reaction="mag") is True
    assert rt.should_process(channel="C1", message_ts="1.1", reaction="mag") is False
    # different reaction on the same message is a distinct trigger
    assert rt.should_process(channel="C1", message_ts="1.1", reaction="+1") is True
    assert state.exists()


def test_should_process_survives_corrupt_state(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    state.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("VACANCY_REACTION_TRIGGER_STATE_FILE", str(state))
    assert rt.should_process(channel="C1", message_ts="1.1", reaction="mag") is True
