import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import update_openrouter_fallback as u

FIXTURE = Path(__file__).parent / "fixtures" / "free_llm_top_models.json"
FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _model(**over):
    base = {
        "rank": 1, "id": "vendor/model:free", "name": "M", "score": 1000,
        "contextLength": 262144, "supportsTools": True,
        "supportsResponseFormat": True, "supportsStructuredOutputs": True,
        "healthStatus": "passed", "latencyMs": 400,
    }
    base.update(over)
    return base


def _eval(passed, total, used_tool):
    """Minimal evalSummary mirroring the shir-man lite-agent-eval payload:
    the empirical tool-use signal lives in task_files_lite.details.usedTool."""
    return {
        "suite": "lite-agent-eval-v1",
        "passed": passed,
        "total": total,
        "tasks": [
            {"id": "task_files_lite", "details": {"usedTool": used_tool}},
        ],
    }


def test_select_on_live_fixture_shapes():
    data = json.loads(FIXTURE.read_text())
    sel = u.select_models(data)
    assert set(sel) == {"fallback", "compression", "web_extract", "title_generation"}
    assert isinstance(sel["fallback"], list) and len(sel["fallback"]) <= 2
    for mid in sel["fallback"]:
        assert mid.endswith(":free")


def test_fallback_requires_tools_and_ctx_but_not_response_format():
    # supportsResponseFormat is NOT required for a tool-calling fallback chat
    # model (tools use the `tools` param, not response_format). Only tool
    # support, adequate context, and non-broken health matter.
    models = [
        _model(rank=1, id="a:free", supportsTools=False),        # no tools -> out
        _model(rank=2, id="b:free", supportsResponseFormat=False),  # still in
        _model(rank=4, id="d:free", contextLength=32000),        # ctx too small -> out
        _model(rank=5, id="e:free", healthStatus="imperfect"),   # in
    ]
    sel = u.select_models({"models": models})
    assert sel["fallback"] == ["b:free", "e:free"]


def test_strict_admits_not_probed_but_excludes_broken_health():
    # Strict tier: not_probed is admitted; explicit error states (http_4xx/5xx,
    # failed) are not. The final chain then backfills the broken ones via the
    # relaxed tier to keep two slots (see test_plan_fallback_* below).
    models = [
        _model(rank=1, id="np:free", healthStatus="not_probed"),
        _model(rank=2, id="rate:free", healthStatus="http_429"),
        _model(rank=3, id="dead:free", healthStatus="failed"),
    ]
    plan = u.plan_fallback(models)
    assert plan["strict"] == ["np:free"]
    # only 1 strict -> relaxed backfills a broken model, less-broken first
    assert plan["chosen"] == ["np:free", "rate:free"]
    assert plan["backfill"] == ["rate:free"]


def test_fallback_ranks_by_lite_eval_score_then_rank():
    # Ordering must follow empirical agent performance (liteEvalScore), not the
    # metadata-driven overall rank / structured-output flag.
    models = [
        _model(rank=1, id="low:free", liteEvalScore=100),
        _model(rank=2, id="high:free", liteEvalScore=700),
        _model(rank=3, id="mid:free", liteEvalScore=400),
    ]
    sel = u.select_models({"models": models})
    assert sel["fallback"] == ["high:free", "mid:free"]


def test_fallback_excludes_models_that_failed_tool_use_eval():
    # A model may advertise supportsTools=True yet fail to actually call a tool
    # in the empirical eval (usedTool=False). It must not enter the fallback
    # chain even when its metadata rank is best.
    models = [
        _model(rank=1, id="pretender:free", liteEvalScore=27,
               evalSummary=_eval(0, 3, used_tool=False)),
        _model(rank=6, id="realtool:free", liteEvalScore=585,
               evalSummary=_eval(2, 3, used_tool=True)),
    ]
    sel = u.select_models({"models": models})
    assert sel["fallback"] == ["realtool:free"]


def test_fallback_on_20260716_snapshot_prefers_real_tool_users():
    # Regression guard: on the live 2026-07-16 feed the only model passing the
    # old (metadata-only) filter was google/gemma-4-26b (eval 0/3, usedTool
    # False) — the exact "fallback can't call tools" failure. The corrected
    # criteria must pick the empirically strongest tool users instead.
    data = json.loads((FIXTURE_DIR / "free_llm_top_models_2026-07-16.json").read_text())
    sel = u.select_models(data)
    assert "google/gemma-4-26b-a4b-it:free" not in sel["fallback"]
    assert sel["fallback"] == ["tencent/hy3:free", "cohere/north-mini-code:free"]


# --- degradation tiers: plan_fallback ---------------------------------------

def test_plan_fallback_two_strict_no_backfill():
    models = [
        _model(rank=1, id="a:free", liteEvalScore=700),
        _model(rank=2, id="b:free", liteEvalScore=600),
        _model(rank=3, id="c:free", liteEvalScore=500),
    ]
    plan = u.plan_fallback(models)
    assert plan["chosen"] == ["a:free", "b:free"]
    assert plan["backfill"] == []


def test_plan_fallback_backfills_one_from_relaxed_when_strict_short():
    # one strict survivor + a rate-limited (relaxed) tool user -> keep two slots
    models = [
        _model(rank=1, id="good:free", healthStatus="passed", liteEvalScore=500),
        _model(rank=2, id="rate:free", healthStatus="http_429", liteEvalScore=400),
    ]
    plan = u.plan_fallback(models)
    assert plan["strict"] == ["good:free"]
    assert plan["chosen"] == ["good:free", "rate:free"]
    assert plan["backfill"] == ["rate:free"]


def test_plan_fallback_relaxed_orders_less_broken_first():
    # no strict survivors; relaxed tier orders by health penalty
    # (not_probed < transient http_4xx < failed), then lite score.
    models = [
        _model(rank=1, id="failed:free", healthStatus="failed", liteEvalScore=900),
        _model(rank=2, id="rate:free", healthStatus="http_500", liteEvalScore=100),
        _model(rank=3, id="np:free", healthStatus="not_probed", liteEvalScore=50),
    ]
    plan = u.plan_fallback(models)
    assert plan["strict"] == ["np:free"]  # not_probed passes strict health
    assert plan["chosen"] == ["np:free", "rate:free"]  # rate(2) before failed(3)


def test_plan_fallback_relaxed_still_excludes_failed_tool_use():
    # a model proven not to call tools must never enter, even in the relaxed tier
    models = [
        _model(rank=1, id="cant-tool:free", healthStatus="http_429",
               liteEvalScore=900, evalSummary=_eval(0, 3, used_tool=False)),
        _model(rank=2, id="ok:free", healthStatus="passed", liteEvalScore=200),
    ]
    plan = u.plan_fallback(models)
    assert "cant-tool:free" not in plan["chosen"]
    assert plan["chosen"] == ["ok:free"]


def test_plan_fallback_empty_when_nothing_tool_capable():
    models = [
        _model(rank=1, id="notools:free", supportsTools=False),
        _model(rank=2, id="small:free", contextLength=1000),
    ]
    plan = u.plan_fallback(models)
    assert plan == {"chosen": [], "strict": [], "backfill": []}


# --- degradation alert: build_fallback_alert --------------------------------

def test_alert_none_when_two_strict():
    models = [_model(id="a:free"), _model(id="b:free")]
    alert = u.build_fallback_alert(["a:free", "b:free"], ["a:free", "b:free"],
                                   models, source="feed")
    assert alert is None


def test_alert_warns_when_backfilled_to_two():
    models = [_model(id="a:free"), _model(id="rate:free", healthStatus="http_429")]
    alert = u.build_fallback_alert(["a:free", "rate:free"], ["a:free"],
                                   models, source="feed")
    assert alert is not None
    assert "⚠️" in alert and "degraded" in alert
    assert "🚨" not in alert


def test_alert_critical_low_when_single_fallback():
    models = [_model(id="a:free")]
    alert = u.build_fallback_alert(["a:free"], ["a:free"], models, source="feed")
    assert alert is not None
    assert "⚠️" in alert and "only 1" in alert


def test_alert_critical_when_empty_primary_only():
    alert = u.build_fallback_alert([], [], [], source="empty")
    assert alert is not None
    assert "🚨" in alert and "EMPTY" in alert


def test_alert_warns_when_holding_previous():
    alert = u.build_fallback_alert(["old:free", "old2:free"], [], [],
                                   source="previous", note="feed unreachable")
    assert alert is not None
    assert "⚠️" in alert and "previous" in alert.lower()
    assert "feed unreachable" in alert


# --- tier 3/4 glue: resolve_degradation -------------------------------------

def test_resolve_degradation_healthy_feed_no_alert():
    sel = {"fallback": ["a:free", "b:free"]}
    plan = {"chosen": ["a:free", "b:free"], "strict": ["a:free", "b:free"],
            "backfill": []}
    out, source, alert = u.resolve_degradation(sel, plan, previous=None, models=[])
    assert source == "feed"
    assert alert is None
    assert out["fallback"] == ["a:free", "b:free"]


def test_resolve_degradation_empty_feed_holds_previous():
    sel = {"fallback": []}
    plan = {"chosen": [], "strict": [], "backfill": []}
    previous = {"fallback": ["old:free", "old2:free"]}
    out, source, alert = u.resolve_degradation(sel, plan, previous, models=[])
    assert source == "previous"
    assert out["fallback"] == ["old:free", "old2:free"]
    assert alert is not None and "⚠️" in alert


def test_resolve_degradation_empty_feed_no_previous_is_primary_only():
    sel = {"fallback": []}
    plan = {"chosen": [], "strict": [], "backfill": []}
    out, source, alert = u.resolve_degradation(sel, plan, previous=None, models=[])
    assert source == "empty"
    assert out["fallback"] == []
    assert alert is not None and "🚨" in alert


def test_resolve_degradation_backfilled_feed_alerts_but_keeps_chain():
    sel = {"fallback": ["a:free", "rate:free"]}
    plan = {"chosen": ["a:free", "rate:free"], "strict": ["a:free"],
            "backfill": ["rate:free"]}
    out, source, alert = u.resolve_degradation(sel, plan, previous=None,
                                               models=[_model(id="a:free"),
                                                       _model(id="rate:free")])
    assert source == "feed"
    assert out["fallback"] == ["a:free", "rate:free"]
    assert alert is not None and "degraded" in alert


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
