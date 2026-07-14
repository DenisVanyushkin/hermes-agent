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
