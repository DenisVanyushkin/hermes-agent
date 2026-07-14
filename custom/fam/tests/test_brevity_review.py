import json

from fam import brevity

CFG = {"brevity_model": "m", "brevity_provider": "p"}
CORPUS = {"items": [{"kind": "reminder", "raw_text": "a", "final": "b", "ts_utc": "t"}],
          "stats": {"total": 1}}


def test_review_parses_structured_json():
    def caller(prompt, cfg):
        assert cfg == CFG
        return json.dumps({
            "assessment": "многословно",
            "rewrite_gap": "переписывания уместны",
            "examples": [{"before": "a", "after": "b"}],
            "edits": ["сократить"],
        })

    result = brevity.review(CORPUS, CFG, caller=caller)
    assert result is not None
    assert result["examples"] == [{"before": "a", "after": "b"}]
    assert result["assessment"] == "многословно"


def test_review_returns_none_when_caller_fails():
    result = brevity.review(CORPUS, CFG, caller=lambda prompt, cfg: None)
    assert result is None


def test_review_returns_none_on_non_json():
    result = brevity.review(CORPUS, CFG, caller=lambda prompt, cfg: "не json вовсе")
    assert result is None


def test_review_returns_none_when_missing_examples_key():
    def caller(prompt, cfg):
        return json.dumps({"assessment": "x", "rewrite_gap": "y", "edits": []})

    result = brevity.review(CORPUS, CFG, caller=caller)
    assert result is None


def test_review_tolerates_chatter_around_json():
    def caller(prompt, cfg):
        return 'Вот результат: {"examples": [], "assessment": "x"} спасибо'

    result = brevity.review(CORPUS, CFG, caller=caller)
    assert result is not None
    assert result["examples"] == []


def test_review_prompt_includes_persona_from_soul_path(tmp_path):
    soul_file = tmp_path / "SOUL.md"
    soul_file.write_text("ТЕСТ-ПЕРСОНА-МАРКЕР", encoding="utf-8")
    cfg = dict(CFG, brevity_soul_path=str(soul_file))
    captured = {}

    def caller(prompt, cfg_):
        captured["prompt"] = prompt
        return json.dumps({"examples": [], "assessment": "x"})

    brevity.review(CORPUS, cfg, caller=caller)
    assert "ТЕСТ-ПЕРСОНА-МАРКЕР" in captured["prompt"]
    assert "НЕ рекомендуй убирать теплоту" in captured["prompt"]


def test_review_prompt_falls_back_to_embedded_persona(tmp_path):
    missing = tmp_path / "nope" / "SOUL.md"
    cfg = dict(CFG, brevity_soul_path=str(missing))
    captured = {}

    def caller(prompt, cfg_):
        captured["prompt"] = prompt
        return json.dumps({"examples": [], "assessment": "x"})

    brevity.review(CORPUS, cfg, caller=caller)
    assert "тёплый" in captured["prompt"]
    assert "1–3 предложения" in captured["prompt"]

