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


def select_models(data: dict) -> dict:
    models = [m for m in (data.get("models") or []) if m.get("id")]

    fb = [m for m in models
          if m.get("supportsTools") and m.get("supportsResponseFormat")
          and _is_healthy(m) and (m.get("contextLength") or 0) >= FALLBACK_MIN_CTX]
    fb.sort(key=lambda m: (not m.get("supportsStructuredOutputs"), _rank(m)))

    big = [m for m in models
           if _is_healthy(m) and (m.get("contextLength") or 0) >= LARGE_CTX]
    big.sort(key=_rank)
    big_id = big[0]["id"] if big else None

    title = [m for m in models if _is_healthy(m) or _health(m) == "not_probed"]
    title.sort(key=lambda m: (
        0 if _is_healthy(m) else 1,
        m["latencyMs"] if isinstance(m.get("latencyMs"), (int, float)) else 10**9,
        _rank(m),
    ))

    return {
        "fallback": [m["id"] for m in fb[:2]],
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
    if re.search(r"^\s*#", raw, flags=re.M):
        raise RuntimeError(
            f"{config_path} contains comment lines; refusing YAML round-trip. "
            "Update the config manually or extend this script."
        )
    cfg = yaml.safe_load(raw)
    if not isinstance(cfg, dict):
        raise RuntimeError(f"{config_path} did not parse to a mapping")

    backup = config_path.with_name(
        config_path.name + f".bak-orfree-{time.strftime('%Y%m%dT%H%M%S')}")
    shutil.copy2(config_path, backup)

    apply_selection(cfg, sel)
    new_text = yaml.safe_dump(cfg, sort_keys=True, allow_unicode=True,
                              default_flow_style=False)
    _atomic_write(config_path, new_text)

    try:
        reparsed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        expected = [{"provider": "openrouter", "model": mid} for mid in sel["fallback"]]
        if not isinstance(reparsed, dict) or reparsed.get("fallback_providers") != expected:
            raise RuntimeError("post-write validation failed")
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

    data = fetch_json(args.url)
    sel = select_models(data)
    result = {
        "selection": sel,
        "rankingVersion": data.get("rankingVersion"),
        "updatedAt": data.get("updatedAt"),
        "checkedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }

    if args.print_only:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    previous = None
    if state_path.exists():
        try:
            previous = json.loads(state_path.read_text(encoding="utf-8")).get("selection")
        except Exception:
            previous = None

    if previous == sel and not args.force:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(state_path, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return 0  # unchanged: stay quiet

    update_config(config_path, sel)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(state_path, json.dumps(result, ensure_ascii=False, indent=2) + "\n")

    lines = ["openrouter free-model refresh: config updated"]
    lines.append(f"fallback_providers: {', '.join(sel['fallback']) or '(empty — primary only)'}")
    for task in AUX_TASKS:
        lines.append(f"auxiliary.{task}: {sel[task] or 'primary (gpt-5.4-mini)'}")
    if previous is not None:
        lines.append(f"previous fallback: {', '.join(previous.get('fallback', [])) or '(empty)'}")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: openrouter free-model refresh failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
