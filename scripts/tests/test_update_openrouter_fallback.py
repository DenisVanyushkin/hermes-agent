import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import update_openrouter_fallback as u

FIXTURE = Path(__file__).parent / "fixtures" / "free_llm_top_models.json"


def _model(**over):
    base = {
        "rank": 1, "id": "vendor/model:free", "name": "M", "score": 1000,
        "contextLength": 262144, "supportsTools": True,
        "supportsResponseFormat": True, "supportsStructuredOutputs": True,
        "healthStatus": "passed", "latencyMs": 400,
    }
    base.update(over)
    return base


def test_select_on_live_fixture_shapes():
    data = json.loads(FIXTURE.read_text())
    sel = u.select_models(data)
    assert set(sel) == {"fallback", "compression", "web_extract", "title_generation"}
    assert isinstance(sel["fallback"], list) and len(sel["fallback"]) <= 2
    for mid in sel["fallback"]:
        assert mid.endswith(":free")


def test_fallback_requires_tools_response_format_health_ctx():
    models = [
        _model(rank=1, id="a:free", supportsTools=False),
        _model(rank=2, id="b:free", supportsResponseFormat=False),
        _model(rank=3, id="c:free", healthStatus="not_probed"),
        _model(rank=4, id="d:free", contextLength=32000),
        _model(rank=5, id="e:free", healthStatus="imperfect"),
    ]
    sel = u.select_models({"models": models})
    assert sel["fallback"] == ["e:free"]


def test_fallback_prefers_structured_outputs_then_rank_top2():
    models = [
        _model(rank=1, id="plain:free", supportsStructuredOutputs=False),
        _model(rank=2, id="s2:free"),
        _model(rank=3, id="s3:free"),
    ]
    sel = u.select_models({"models": models})
    assert sel["fallback"] == ["s2:free", "s3:free"]


def test_compression_needs_128k_healthy():
    models = [
        _model(rank=1, id="small:free", contextLength=131071),
        _model(rank=2, id="big:free", contextLength=131072),
        _model(rank=3, id="dead:free", healthStatus="failed"),
    ]
    sel = u.select_models({"models": models})
    assert sel["compression"] == "big:free"
    assert sel["web_extract"] == "big:free"


def test_title_allows_not_probed_prefers_healthy_low_latency():
    models = [
        _model(rank=1, id="np:free", healthStatus="not_probed", latencyMs=None,
               contextLength=8000, supportsTools=False, supportsResponseFormat=False),
        _model(rank=2, id="slow:free", latencyMs=900, contextLength=8000),
        _model(rank=3, id="fast:free", latencyMs=100, contextLength=8000),
    ]
    sel = u.select_models({"models": models})
    assert sel["title_generation"] == "fast:free"


def test_empty_feed_reverts_everything():
    sel = u.select_models({"models": []})
    assert sel == {"fallback": [], "compression": None,
                   "web_extract": None, "title_generation": None}


def test_apply_selection_writes_openrouter_blocks_and_keeps_timeout():
    cfg = {
        "fallback_providers": [{"provider": "openrouter", "model": "old:free"}],
        "auxiliary": {"compression": {"provider": "openai-codex", "model": "gpt-5.4-mini",
                                      "base_url": "https://chatgpt.com/backend-api/codex",
                                      "timeout": 120, "api_key": "", "extra_body": {}}},
    }
    sel = {"fallback": ["a:free", "b:free"], "compression": "c:free",
           "web_extract": None, "title_generation": None}
    out = u.apply_selection(copy.deepcopy(cfg), sel)
    assert out["fallback_providers"] == [
        {"provider": "openrouter", "model": "a:free"},
        {"provider": "openrouter", "model": "b:free"},
    ]
    comp = out["auxiliary"]["compression"]
    assert comp["provider"] == "openrouter" and comp["model"] == "c:free"
    assert comp["base_url"] == "" and comp["timeout"] == 120
    we = out["auxiliary"]["web_extract"]
    assert we["provider"] == "openai-codex" and we["model"] == "gpt-5.4-mini"
    assert we["base_url"] == "https://chatgpt.com/backend-api/codex"


def test_title_not_probed_requires_tools_metadata():
    models = [
        _model(rank=1, id="np-no-tools:free", healthStatus="not_probed",
               latencyMs=None, contextLength=8000, supportsTools=False,
               supportsResponseFormat=False),
    ]
    sel = u.select_models({"models": models})
    assert sel["title_generation"] is None

    models = [
        _model(rank=1, id="np-tools:free", healthStatus="not_probed",
               latencyMs=None, contextLength=8000, supportsTools=True,
               supportsResponseFormat=False),
    ]
    sel = u.select_models({"models": models})
    assert sel["title_generation"] == "np-tools:free"


def test_apply_selection_title_generation_reverts_to_primary():
    cfg = {
        "fallback_providers": [],
        "auxiliary": {
            "title_generation": {
                "provider": "openrouter", "model": "old:free",
                "base_url": "", "timeout": 45,
            },
        },
    }
    sel = {"fallback": [], "compression": None,
           "web_extract": None, "title_generation": None}
    out = u.apply_selection(copy.deepcopy(cfg), sel)
    tg = out["auxiliary"]["title_generation"]
    assert tg["provider"] == "openai-codex"
    assert tg["model"] == "gpt-5.4-mini"
    assert tg["base_url"] == "https://chatgpt.com/backend-api/codex"
    assert tg["timeout"] == 45


def test_apply_selection_empty_fallback_clears_chain():
    out = u.apply_selection({"fallback_providers": [{"provider": "openrouter", "model": "x"}]},
                            {"fallback": [], "compression": None,
                             "web_extract": None, "title_generation": None})
    assert out["fallback_providers"] == []
