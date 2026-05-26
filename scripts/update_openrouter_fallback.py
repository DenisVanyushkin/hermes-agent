#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

URL = "https://shir-man.com/api/free-llm/top-models"
DEFAULT_CONFIG = Path.home() / ".hermes" / "config.yaml"
DEFAULT_COMPOSE_DIR = Path.home() / ".hermes" / "hermes-agent"
DEFAULT_CACHE = Path.home() / ".hermes" / "cache" / "openrouter-fallback-last.json"


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Hermes-Agent/1.0 (+openrouter-fallback-updater)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def choose_models(data: dict, limit: int = 2) -> tuple[list[str], dict]:
    models = data.get("models") or []

    def healthy(m: dict) -> bool:
        return str(m.get("healthStatus") or "").lower() in {"passed", "imperfect"}

    def supports_agent_basics(m: dict) -> bool:
        return bool(m.get("supportsTools")) and bool(m.get("supportsResponseFormat")) and healthy(m)

    structured = [m for m in models if supports_agent_basics(m) and m.get("supportsStructuredOutputs")]
    if structured:
        chosen = structured[:max(1, limit)]
        return [m["id"] for m in chosen if m.get("id")], {
            "rule": "top healthy tools+response_format+structured_outputs",
            "selected": [
                {
                    "rank": m.get("rank"),
                    "id": m.get("id"),
                    "name": m.get("name"),
                    "healthStatus": m.get("healthStatus"),
                    "score": m.get("score"),
                }
                for m in chosen
            ],
        }

    plain = [m for m in models if supports_agent_basics(m)]
    if plain:
        chosen = plain[:max(1, limit)]
        return [m["id"] for m in chosen if m.get("id")], {
            "rule": "top healthy tools+response_format",
            "selected": [
                {
                    "rank": m.get("rank"),
                    "id": m.get("id"),
                    "name": m.get("name"),
                    "healthStatus": m.get("healthStatus"),
                    "score": m.get("score"),
                }
                for m in chosen
            ],
        }

    if models and models[0].get("id"):
        chosen = models[:max(1, limit)]
        return [m["id"] for m in chosen if m.get("id")], {
            "rule": "top ranked model fallback",
            "selected": [
                {
                    "rank": m.get("rank"),
                    "id": m.get("id"),
                    "name": m.get("name"),
                    "healthStatus": m.get("healthStatus"),
                    "score": m.get("score"),
                }
                for m in chosen
            ],
        }

    fallback_id = ((data or {}).get("fallback") or {}).get("id")
    if fallback_id:
        return [fallback_id], {
            "rule": "api fallback.id",
            "selected": [
                {
                    "rank": None,
                    "id": fallback_id,
                    "name": fallback_id,
                    "healthStatus": None,
                    "score": None,
                }
            ],
        }

    raise RuntimeError("No usable model found in API response")


def _find_block(lines: list[str], key: str) -> tuple[int | None, int | None]:
    start = None
    for i, line in enumerate(lines):
        if re.match(rf"^{re.escape(key)}:", line):
            start = i
            break
    if start is None:
        return None, None

    end = start + 1
    top_level_sequence_item = re.compile(r"^-\s")
    while end < len(lines):
        line = lines[end]
        if line.strip() == "" or line.startswith(" ") or line.startswith("\t"):
            end += 1
            continue
        # YAML permits block sequences to be written flush-left after the key
        # (for example ``toolsets:\n- hermes-cli``). Also, if a previous updater
        # produced orphaned top-level list entries after fallback_providers,
        # include them in the block so the next run repairs the config instead
        # of preserving invalid YAML. Any other flush-left line starts the next
        # top-level construct and must be preserved.
        if top_level_sequence_item.match(line):
            end += 1
            continue
        break
    return start, end


def remove_top_level_block(lines: list[str], key: str) -> bool:
    start, end = _find_block(lines, key)
    if start is None:
        return False
    del lines[start:end]
    return True


def replace_top_level_block(lines: list[str], key: str, new_block_lines: list[str]) -> bool:
    start, end = _find_block(lines, key)
    if start is None:
        return False
    lines[start:end] = new_block_lines
    return True


def insert_after_key(lines: list[str], anchor_key: str, new_block_lines: list[str]) -> None:
    start, end = _find_block(lines, anchor_key)
    if start is None:
        lines[0:0] = new_block_lines
        return
    lines[end:end] = new_block_lines


def update_config(config_path: Path, model_ids: list[str]) -> None:
    text = config_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    if not model_ids:
        raise RuntimeError("No fallback model IDs provided")

    new_block = ["fallback_providers:"]
    for model_id in model_ids:
        new_block.extend([
            "  - provider: openrouter",
            f"    model: {model_id}",
        ])

    remove_top_level_block(lines, "fallback_model")
    replaced = replace_top_level_block(lines, "fallback_providers", new_block)
    if not replaced:
        if any(re.match(r"^providers:", line) for line in lines):
            insert_after_key(lines, "providers", new_block)
        else:
            insert_after_key(lines, "model", new_block)

    new_text = "\n".join(lines).rstrip() + "\n"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Bind-mounted single files (common in Docker) can reject atomic rename with
    # EBUSY/EXDEV even though direct overwrite is allowed. Try atomic replace
    # first for safety, then fall back to in-place write when the mount forbids it.
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(config_path.parent), delete=False) as tmp:
        tmp.write(new_text)
        tmp_path = tmp.name
    try:
        os.replace(tmp_path, config_path)
    except OSError:
        Path(tmp_path).unlink(missing_ok=True)
        config_path.write_text(new_text, encoding="utf-8")


def write_cache(cache_path: Path, payload: dict) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def restart_gateway(compose_dir: Path) -> dict:
    env = os.environ.copy()
    env.setdefault("HERMES_UID", str(os.getuid()))
    env.setdefault("HERMES_GID", str(os.getgid()))

    restart = subprocess.run(
        ["docker", "compose", "restart", "gateway"],
        cwd=str(compose_dir),
        env=env,
        capture_output=True,
        text=True,
    )
    if restart.returncode == 0:
        return {
            "method": "restart",
            "stdout": restart.stdout.strip(),
            "stderr": restart.stderr.strip(),
        }

    up = subprocess.run(
        ["docker", "compose", "up", "-d", "gateway"],
        cwd=str(compose_dir),
        env=env,
        capture_output=True,
        text=True,
    )
    if up.returncode != 0:
        raise RuntimeError(
            "gateway restart failed\n"
            f"restart stderr: {restart.stderr.strip()}\n"
            f"up stderr: {up.stderr.strip()}"
        )
    return {
        "method": "up -d",
        "stdout": up.stdout.strip(),
        "stderr": up.stderr.strip(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Update Hermes OpenRouter fallback model from ranked free-model API")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to Hermes config.yaml")
    parser.add_argument("--compose-dir", default=str(DEFAULT_COMPOSE_DIR), help="Path to hermes-agent docker-compose directory")
    parser.add_argument("--cache", default=str(DEFAULT_CACHE), help="Where to write last-run metadata JSON")
    parser.add_argument("--write-only", action="store_true", help="Update config but do not restart gateway")
    parser.add_argument("--print-only", action="store_true", help="Only print the chosen model/result; do not modify anything")
    parser.add_argument("--url", default=URL, help="Ranking API URL")
    parser.add_argument("--count", type=int, default=2, help="How many ranked fallback models to keep")
    args = parser.parse_args()

    config_path = Path(args.config).expanduser()
    compose_dir = Path(args.compose_dir).expanduser()
    cache_path = Path(args.cache).expanduser()

    if not config_path.exists():
        raise RuntimeError(f"Config not found: {config_path}")

    data = fetch_json(args.url)
    model_ids, reason = choose_models(data, limit=max(1, args.count))

    result = {
        "chosen_model": model_ids[0] if model_ids else None,
        "chosen_models": model_ids,
        "reason": reason,
        "updatedAt": data.get("updatedAt"),
        "source": data.get("source"),
        "rankingVersion": data.get("rankingVersion"),
        "apiFallback": ((data or {}).get("fallback") or {}).get("id"),
    }

    if args.print_only:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    update_config(config_path, model_ids)

    if args.write_only:
        result["gateway_action"] = "skipped"
    else:
        gateway = restart_gateway(compose_dir)
        result["gateway_action"] = gateway["method"]
        result["gateway_stdout"] = gateway["stdout"]
        result["gateway_stderr"] = gateway["stderr"]

    write_cache(cache_path, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
