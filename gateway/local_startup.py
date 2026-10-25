"""Fork: startup/shutdown wiring the gateway lost along the way.

* Memory monitor -- the periodic "[MEMORY] rss=..." line (gateway/memory_monitor.py)
  that the monitoring stack and slow-leak triage read. A 2026-05 upstream commit
  deleted its start/stop calls from start_gateway, silencing it for weeks; the
  v0.22.5 bootstrap again has no caller. Controlled by ``logging.memory_monitor``.
* Review-gate config at startup -- one INFO line with the effective review-gate
  mode/tier and whether config.yaml loaded, so "the gate did nothing" can be told
  apart from "the gate ran on fallback config" from the log alone.

Both are best-effort and never raise.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("gateway.run")


def start_memory_monitor() -> bool:
    try:
        from gateway import memory_monitor
        from hermes_cli.config import load_config

        cfg = ((load_config() or {}).get("logging") or {}).get("memory_monitor") or {}
        if not cfg.get("enabled", True):
            return False
        try:
            interval = float(cfg.get("interval_seconds", 300))
        except (TypeError, ValueError):
            interval = 300.0
        return bool(memory_monitor.start_memory_monitoring(interval_seconds=interval))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to start memory monitor: %s", exc)
        return False


def stop_memory_monitor() -> None:
    """Also emits the final "[MEMORY] shutdown rss=..." line."""
    try:
        from gateway import memory_monitor

        memory_monitor.stop_memory_monitoring(timeout=2.0)
    except Exception:  # noqa: BLE001
        pass


def log_review_gate_config() -> None:
    try:
        from hermes_cli.config import get_config_path, load_config_readonly, read_raw_config
        from hermes_cli.review_gate import build_review_gate_startup_log_fields

        loaded_ok = bool(read_raw_config())
        fields = build_review_gate_startup_log_fields(
            load_config_readonly(),
            config_path=get_config_path(),
            config_loaded_ok=loaded_ok,
            fallback_config_used=not loaded_ok,
        )
        logger.info(
            "Gateway startup review gate config: config_path=%s config_loaded_ok=%s "
            "fallback_config_used=%s review_gate.mode=%s review_gate.reviewer_tier=%s "
            "review_gate.auto_review_in_observe=%s",
            fields["config_path"],
            fields["config_loaded_ok"],
            fields["fallback_config_used"],
            fields["review_gate.mode"],
            fields["review_gate.reviewer_tier"],
            fields["review_gate.auto_review_in_observe"],
        )
    except Exception:  # noqa: BLE001
        logger.debug("review gate startup config logging failed", exc_info=True)
