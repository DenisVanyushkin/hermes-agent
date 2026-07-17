#!/usr/bin/env python3
"""Nightly per-role free-model refresh from the OpenRouter free-model ranking API.

Selects free OpenRouter models for the agent fallback chain and for the
auxiliary side tasks (compression, web_extract, title_generation) and writes
them into ~/.hermes/config.yaml. Roles with no suitable free model revert to
the primary model. Silent (no output, no write) when the selection is
unchanged since the last run, so the cron job only reports real changes.
No gateway restart: hermes reads config.yaml from disk at call time.
"""
import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import yaml

URL = "https://shir-man.com/api/free-llm/top-models"
DEFAULT_CONFIG = Path.home() / ".hermes" / "config.yaml"
DEFAULT_STATE = Path.home() / ".hermes" / "cache" / "openrouter-fallback-last.json"

HEALTHY = {"passed", "imperfect"}
FALLBACK_MIN_CTX = 65536
LARGE_CTX = 131072
AUX_TASKS = ("compression", "web_extract", "title_generation")
PRIMARY = {
    "provider": "openai-codex",
    "model": "gpt-5.4-mini",
    "base_url": "https://chatgpt.com/backend-api/codex",
}


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Hermes-Agent/1.0 (+openrouter-fallback-updater)",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _health(m: dict) -> str:
    return str(m.get("healthStatus") or "").lower()


def _is_healthy(m: dict) -> bool:
    return _health(m) in HEALTHY


def _rank(m: dict) -> int:
    return m.get("rank") if isinstance(m.get("rank"), int) else 10**6


def _lite_score(m: dict) -> float:
    """Empirical agent-eval score (lite-agent-eval-v1). Higher = better tool
    user. Absent/invalid -> 0 so unproven models sort below proven ones."""
    v = m.get("liteEvalScore")
    return v if isinstance(v, (int, float)) else 0


def _fallback_health_ok(m: dict) -> bool:
    """A fallback candidate must not be in an explicit error state. `passed`
    and `imperfect` are healthy; `not_probed` is admitted (many strong tool
    users simply were not probed this cycle); anything else (http_4xx/5xx,
    failed, timeout, ...) is treated as broken."""
    return _is_healthy(m) or _health(m) == "not_probed"


def _tool_use_failed(m: dict) -> bool:
    """True only when the empirical eval proves the model does NOT invoke tools.

    `supportsTools` is a metadata claim; the ground truth is the per-task
    `details.usedTool` signal in evalSummary. If any eval task shows the model
    used a tool it is trusted; if the model was exercised on a tool task and
    never used one it is rejected; with no tool-use signal at all we stay
    agnostic (do not reject on this basis)."""
    ev = m.get("evalSummary")
    if not isinstance(ev, dict):
        return False
    saw_tool_task = False
    for task in ev.get("tasks") or []:
        used = (task.get("details") or {}).get("usedTool")
        if used is True:
            return False
        if used is False:
            saw_tool_task = True
    return saw_tool_task


def _health_penalty(m: dict) -> int:
    """Relaxed-tier ordering: prefer the least-broken model. 0 healthy
    (passed/imperfect), 1 not_probed, 2 transient error (http_4xx/5xx, timeout,
    refused), 3 anything else (failed, unknown)."""
    h = _health(m)
    if h in HEALTHY:
        return 0
    if h == "not_probed":
        return 1
    if h.startswith("http_") or "timeout" in h or "refused" in h:
        return 2
    return 3


def plan_fallback(models: list) -> dict:
    """Pick up to two tool-capable fallback models, degrading through tiers.

    Tier 1 (strict): supportsTools + ctx + non-broken health + proven tool use.
    Tier 2 (relaxed backfill): same but health ignored — admits rate-limited /
    unprobed / failed-health models to keep two slots — yet STILL excludes
    models empirically shown not to call tools (that reintroduces the original
    bug). Returns the strict list, the chosen chain, and which ids were
    backfilled so callers can alert on degradation."""
    models = [m for m in (models or []) if m.get("id")]

    def _ctx_ok(m):
        return (m.get("contextLength") or 0) >= FALLBACK_MIN_CTX

    strict = [m for m in models
              if m.get("supportsTools") and _ctx_ok(m)
              and _fallback_health_ok(m) and not _tool_use_failed(m)]
    strict.sort(key=lambda m: (-_lite_score(m), _rank(m)))
    chosen = [m["id"] for m in strict[:2]]

    backfill = []
    if len(chosen) < 2:
        relaxed = [m for m in models
                   if m.get("supportsTools") and _ctx_ok(m)
                   and not _tool_use_failed(m)]
        relaxed.sort(key=lambda m: (_health_penalty(m), -_lite_score(m), _rank(m)))
        for m in relaxed:
            if m["id"] in chosen:
                continue
            chosen.append(m["id"])
            backfill.append(m["id"])
            if len(chosen) >= 2:
                break

    return {"chosen": chosen, "strict": [m["id"] for m in strict], "backfill": backfill}


def _find_model(models: list, mid: str) -> dict:
    for m in models or []:
        if m.get("id") == mid:
            return m
    return {}


def _fallback_shortfall_reasons(models: list) -> str:
    tool_models = [m for m in (models or []) if m.get("supportsTools")]
    small = sum(1 for m in tool_models
                if (m.get("contextLength") or 0) < FALLBACK_MIN_CTX)
    failed = sum(1 for m in tool_models if _tool_use_failed(m))
    unhealthy = sum(1 for m in tool_models
                    if (m.get("contextLength") or 0) >= FALLBACK_MIN_CTX
                    and not _tool_use_failed(m) and not _fallback_health_ok(m))
    return (f"  strict shortfall: feed={len(models or [])}, "
            f"tool-capable={len(tool_models)}, unhealthy={unhealthy}, "
            f"failed-tool-eval={failed}, ctx<64k={small}")


def build_fallback_alert(chosen: list, strict: list, models: list,
                         source: str = "feed", note: str = "") -> str:
    """Return a Slack-bound warning when the fallback chain is degraded, else
    None. `chosen` is the applied chain, `strict` the ids that passed strict
    criteria, `models` the feed (for the shortfall breakdown), `source` one of
    feed|previous|empty."""
    if source == "feed" and len(strict) >= 2:
        return None

    note_sfx = f" ({note})" if note else ""
    if source == "empty" or not chosen:
        head = ("🚨 openrouter fallback EMPTY — primary (codex) only; "
                "no free-model safety net if codex auth fails" + note_sfx)
    elif source == "previous":
        head = ("⚠️ openrouter fallback holding PREVIOUS selection — no model "
                "passed strict or relaxed criteria this run" + note_sfx)
    elif len(chosen) == 1:
        head = "⚠️ openrouter fallback critically low — only 1 model in the chain"
    else:
        head = (f"⚠️ openrouter fallback degraded — {len(strict)} passed strict, "
                f"{len(chosen) - len(strict)} backfilled from the relaxed tier "
                "(may be rate-limited / unproven)")

    lines = [head]
    for mid in chosen:
        m = _find_model(models, mid)
        if m:
            lines.append(f"  {mid} — health={_health(m) or '?'}, "
                         f"liteEval={_lite_score(m)}")
        else:
            lines.append(f"  {mid}")
    if source == "feed":
        lines.append(_fallback_shortfall_reasons(models))
    return "\n".join(lines)


def resolve_degradation(sel: dict, plan: dict, previous, models: list):
    """Apply tier 3 (hold previous) / tier 4 (primary-only) when the feed chose
    no fallback, and compute the degradation alert. Returns
    (sel, source, alert_or_None). `sel` is copied before mutation."""
    source = "feed"
    if not sel.get("fallback"):
        if previous and previous.get("fallback"):
            sel = dict(sel)
            sel["fallback"] = list(previous["fallback"])
            source = "previous"
        else:
            source = "empty"
    alert = build_fallback_alert(sel.get("fallback") or [],
                                 plan.get("strict") or [], models, source=source)
    return sel, source, alert


def select_models(data: dict) -> dict:
    models = [m for m in (data.get("models") or []) if m.get("id")]

    fb_chosen = plan_fallback(models)["chosen"]

    big = [m for m in models
           if _is_healthy(m) and (m.get("contextLength") or 0) >= LARGE_CTX]
    big.sort(key=_rank)
    big_id = big[0]["id"] if big else None

    title = [m for m in models
             if _is_healthy(m)
             or (_health(m) == "not_probed" and m.get("supportsTools"))]
    title.sort(key=lambda m: (
        0 if _is_healthy(m) else 1,
        m["latencyMs"] if isinstance(m.get("latencyMs"), (int, float)) else 10**9,
        _rank(m),
    ))

    return {
        "fallback": fb_chosen,
        "compression": big_id,
        "web_extract": big_id,
        "title_generation": title[0]["id"] if title else None,
    }


def apply_selection(cfg: dict, sel: dict) -> dict:
    cfg["fallback_providers"] = [
        {"provider": "openrouter", "model": mid} for mid in sel["fallback"]
    ]
    aux = cfg.setdefault("auxiliary", {})
    for task in AUX_TASKS:
        block = aux.setdefault(task, {})
        if sel[task]:
            block["provider"] = "openrouter"
            block["model"] = sel[task]
            block["base_url"] = ""
        else:
            block.update(PRIMARY)
    return cfg


def _atomic_write(path: Path, text: str) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8",
                                     dir=str(path.parent), delete=False) as tmp:
        tmp.write(text)
        tmp_path = tmp.name
    try:
        os.replace(tmp_path, path)
    except OSError:
        Path(tmp_path).unlink(missing_ok=True)
        path.write_text(text, encoding="utf-8")


def update_config(config_path: Path, sel: dict) -> None:
    raw = config_path.read_text(encoding="utf-8")
    has_comments = bool(re.search(r"^\s*#", raw, flags=re.M))
    yaml_rt = None
    if has_comments:
        # A plain yaml.safe_dump round-trip would destroy comments; use
        # ruamel's comment-preserving round-trip instead.
        try:
            from ruamel.yaml import YAML
        except ImportError as exc:
            raise RuntimeError(
                f"{config_path} contains comment lines and ruamel.yaml is not "
                "installed; refusing comment-destroying YAML round-trip."
            ) from exc
        yaml_rt = YAML()
        yaml_rt.preserve_quotes = True
        yaml_rt.width = 4096
        cfg = yaml_rt.load(raw)
    else:
        cfg = yaml.safe_load(raw)
    if not isinstance(cfg, dict):
        raise RuntimeError(f"{config_path} did not parse to a mapping")

    backup = config_path.with_name(
        config_path.name + f".bak-orfree-{time.strftime('%Y%m%dT%H%M%S')}")
    shutil.copy2(config_path, backup)

    apply_selection(cfg, sel)
    if yaml_rt is not None:
        import io
        buf = io.StringIO()
        yaml_rt.dump(cfg, buf)
        new_text = buf.getvalue()
    else:
        new_text = yaml.safe_dump(cfg, sort_keys=True, allow_unicode=True,
                                  default_flow_style=False)
    _atomic_write(config_path, new_text)

    try:
        reparsed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(reparsed, dict):
            raise RuntimeError("post-write validation failed: not a mapping")

        expected = [{"provider": "openrouter", "model": mid} for mid in sel["fallback"]]
        if reparsed.get("fallback_providers") != expected:
            raise RuntimeError("post-write validation failed: fallback_providers mismatch")

        aux = reparsed.get("auxiliary")
        if not isinstance(aux, dict):
            raise RuntimeError("post-write validation failed: auxiliary missing")
        for task in AUX_TASKS:
            block = aux.get(task)
            if not isinstance(block, dict):
                raise RuntimeError(f"post-write validation failed: auxiliary.{task} missing")
            if sel[task]:
                if block.get("provider") != "openrouter" or block.get("model") != sel[task]:
                    raise RuntimeError(
                        f"post-write validation failed: auxiliary.{task} mismatch")
            else:
                if (block.get("provider") != PRIMARY["provider"]
                        or block.get("model") != PRIMARY["model"]
                        or block.get("base_url") != PRIMARY["base_url"]):
                    raise RuntimeError(
                        f"post-write validation failed: auxiliary.{task} mismatch")
    except Exception:
        shutil.copy2(backup, config_path)
        raise RuntimeError(f"config validation failed; restored backup {backup}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    parser.add_argument("--url", default=URL)
    parser.add_argument("--print-only", action="store_true",
                        help="print the selection, change nothing")
    parser.add_argument("--force", action="store_true",
                        help="write config even if the selection is unchanged")
    args = parser.parse_args()

    config_path = Path(args.config).expanduser()
    state_path = Path(args.state).expanduser()
    if not config_path.exists():
        raise RuntimeError(f"Config not found: {config_path}")

    previous = None
    if state_path.exists():
        try:
            previous = json.loads(state_path.read_text(encoding="utf-8")).get("selection")
        except Exception:
            previous = None

    # Feed unreachable (tier 3): hold the previous selection rather than crash,
    # so a transient network failure does not clear the fallback chain. With no
    # previous state there is nothing to hold — fail loudly (exit 1).
    try:
        data = fetch_json(args.url)
    except Exception as exc:
        if previous and previous.get("fallback"):
            alert = build_fallback_alert(previous["fallback"], [], [],
                                         source="previous",
                                         note=f"feed unreachable: {exc}")
            if alert:
                print(alert)
            return 0
        raise

    models = data.get("models") or []
    sel = select_models(data)
    plan = plan_fallback(models)
    sel, source, alert = resolve_degradation(sel, plan, previous, models)

    result = {
        "selection": sel,
        "rankingVersion": data.get("rankingVersion"),
        "updatedAt": data.get("updatedAt"),
        "checkedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }

    if args.print_only:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if alert:
            print(alert)
        return 0

    state_path.parent.mkdir(parents=True, exist_ok=True)
    unchanged = previous == sel and not args.force
    if not unchanged:
        update_config(config_path, sel)
    _atomic_write(state_path, json.dumps(result, ensure_ascii=False, indent=2) + "\n")

    if not unchanged:
        lines = ["openrouter free-model refresh: config updated"]
        lines.append(f"fallback_providers: {', '.join(sel['fallback']) or '(empty — primary only)'}")
        for task in AUX_TASKS:
            lines.append(f"auxiliary.{task}: {sel[task] or 'primary (gpt-5.4-mini)'}")
        if previous is not None:
            lines.append(f"previous fallback: {', '.join(previous.get('fallback', [])) or '(empty)'}")
        print("\n".join(lines))

    # Degraded state is re-reported on every run (even when unchanged) so a
    # persisting collapse stays visible; healthy + unchanged stays fully silent.
    if alert:
        print(alert)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: openrouter free-model refresh failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
