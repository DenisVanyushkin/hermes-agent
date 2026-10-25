"""Fork: stale-code guard and turn-path preload for a gateway that is deployed into live.

* Preload -- modules the turn-completion path imports lazily are imported at
  startup, so a deploy under a live gateway changes nothing until the next
  restart instead of half-loading new code into an old process (2026-07-27).
* Guard (``gateway.stale_code_guard`` in config.yaml; off when the block is
  absent -- the VPS has none): after startup the watched files are
  fingerprinted (gateway/module_skew.py); a housekeeping chore checks for skew,
  alerts the operator once per episode, and restarts the gateway through its
  supervisor once it is idle -- bounded by an hourly budget
  (gateway/stale_guard.py), fail-closed when the budget cannot be recorded, and
  disabled for the life of the process once the budget is exhausted.

Moved from the pre-v0.22.5 gateway/run.py; the bootstrap and housekeeping call
in at marked sites.
"""

from __future__ import annotations

import importlib
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.async_utils import safe_schedule_threadsafe
from gateway.module_skew import detect_module_skew, take_snapshot
from gateway.stale_guard import (
    auto_restart_allowed,
    budget_path,
    budget_writable,
    format_arm_refused_alert,
    format_budget_exhausted_alert,
    format_budget_unwritable_alert,
    format_budget_write_failed_alert,
    format_skew_alert,
    get_stale_guard_config,
    record_auto_restart,
)
from gateway.turn_error_alerts import get_alert_config, send_operator_alert

logger = logging.getLogger("gateway.run")

_TURN_PATH_PRELOAD = (
    "agent.turn_finalizer",
    "agent.local_finalizer",
    "hermes_cli.review_gate",
    # Their turn_finalizer call sites try/except the import, so a desync there does not crash a
    # turn -- it silently stops plugin hooks and kanban failure-recording for the process's life.
    "hermes_cli.plugins",
    "hermes_cli.kanban_db",
)
_STALE_GUARD_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_UNSET = object()


def preload_turn_path_modules() -> list[str]:
    """Import the lazily-loaded turn-path modules. Best-effort, never raises."""
    loaded = []
    for name in _TURN_PATH_PRELOAD:
        try:
            importlib.import_module(name)
            loaded.append(name)
        except Exception as exc:  # noqa: BLE001 -- a preload miss must not block startup
            logger.warning("turn-path preload failed for %s: %s", name, exc)
    return loaded


def _stale_guard_hermes_home() -> Path:
    from hermes_constants import get_hermes_home  # NB: hermes_constants, not hermes_cli.config

    return Path(get_hermes_home())


def _stale_guard_load_config():
    from hermes_cli.config import load_config

    return load_config() or {}


def _stale_guard_alert_config(config):
    """The operator channel for guard alerts; None when unset or unreadable (config.yaml is
    edited by hand here -- an unreadable one must mean "no alert", not an exception)."""
    try:
        return get_alert_config(config)
    except Exception:  # noqa: BLE001
        logger.warning("stale-guard: alert config unreadable", exc_info=True)
        return None


def _stale_guard_send_alert(alert_cfg, text) -> None:
    """Send an operator alert. Never raises; silent without a channel."""
    if not alert_cfg:
        return
    try:
        send_operator_alert(alert_cfg["channel"], text)
    except Exception:  # noqa: BLE001
        logger.warning("stale-guard: failed to send operator alert", exc_info=True)


def stale_guard_is_idle(runner: Any, idle_timeout_seconds: float) -> bool:
    """Scale-to-zero's idle predicate with the guard's own threshold. Counts ALL active work:
    cron jobs run outside the running-agent map, and restarting mid-job is what the guard
    exists to avoid."""
    from gateway.scale_to_zero import is_idle

    return is_idle(
        active_work_count=runner._active_work_count(),
        seconds_since_last_inbound=time.time() - runner._last_inbound_at,
        idle_timeout_seconds=idle_timeout_seconds,
        has_live_background_work=runner._scale_to_zero_has_live_background_work(),
    )


async def _stale_guard_request_restart(runner) -> None:
    """``request_restart`` ON the event loop: it latches ``_restart_task_started`` before
    ``asyncio.create_task``, which needs the loop in the CURRENT thread -- called from the
    housekeeping thread it would raise after the latch and break /restart for good."""
    runner.request_restart(via_service=True)


def _stale_guard_arm(config=_UNSET, *, take_snapshot_now: bool = True):
    """Decide whether to arm the guard and take the snapshot. Never raises.

    Returns ``(cfg | None, boot_label | None)``; ``None`` == not armed: no snapshot, no
    ticks. The config is read HERE, inside the try: a broken config.yaml must leave the
    guard off, not the gateway down.
    """
    try:
        if config is _UNSET:
            config = _stale_guard_load_config()
        cfg = get_stale_guard_config(config)
        if not cfg:
            return None, None
        # The auto-restart is an exit 75 -- only meaningful if something brings the process back.
        from gateway.restart import is_gateway_supervisor_process

        in_container = os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")
        if not (is_gateway_supervisor_process() or in_container):
            logger.warning("Stale-code guard NOT armed: no supervisor detected — exit 75 would leave the gateway down")
            return None, None
        home = _stale_guard_hermes_home()
        if not budget_writable(home):
            logger.error("Stale-code guard NOT armed: budget file %s is not writable", budget_path(home))
            # A silent "guard did not arm" is the worst thing to learn late.
            _stale_guard_send_alert(_stale_guard_alert_config(config), format_arm_refused_alert(budget_path(home)))
            return None, None
        boot_label = datetime.now().strftime("%H:%M:%S")
        if take_snapshot_now:
            n = take_snapshot(_STALE_GUARD_PROJECT_ROOT, watch_files=cfg["watch_files"])
            logger.info("Stale-code guard armed: %d file(s) fingerprinted", n)
        return cfg, boot_label
    except Exception:  # noqa: BLE001 -- arming must never break startup
        logger.warning("stale-guard arming failed", exc_info=True)
        return None, None


def _stale_guard_tick(runner, cfg: dict, alert_state: dict, loop=None) -> None:
    """One detector poll. Never raises.

    ``alert_state`` lives as long as the process: skew does not go away by itself, so the
    alert goes once per episode. ``guard_disabled`` latches: an exhausted budget disables
    auto-restart until a human intervenes, not for a sliding hour.
    """
    try:
        if alert_state.get("guard_disabled"):
            return
        changed = detect_module_skew(_STALE_GUARD_PROJECT_ROOT)
        if not changed:
            return
        try:
            alert_cfg = get_alert_config(_stale_guard_load_config())
        except Exception:  # noqa: BLE001
            logger.warning("stale-guard: alert config unreadable", exc_info=True)
            alert_cfg = None

        if not alert_state.get("skew_alerted"):
            alert_state["skew_alerted"] = True
            logger.error("Gateway is running stale code — changed on disk: %s", ", ".join(changed[:10]))
            if alert_cfg:
                send_operator_alert(alert_cfg["channel"], format_skew_alert(changed, alert_state.get("boot_label", "старт")))
            # The alert leaves on a daemon thread; exiting now would kill it before it is sent and
            # the restart would look unexplained. Restart on the next tick -- also a window to step in.
            return

        home = _stale_guard_hermes_home()
        now = time.time()
        if not auto_restart_allowed(home, now, cfg["max_auto_restarts_per_hour"]):
            alert_state["guard_disabled"] = True
            if not alert_state.get("budget_alerted"):
                alert_state["budget_alerted"] = True
                logger.error("stale-guard: auto-restart budget exhausted — auto-restart disabled for the life of this process")
                if alert_cfg:
                    send_operator_alert(alert_cfg["channel"], format_budget_exhausted_alert(cfg["max_auto_restarts_per_hour"]))
            return

        is_idle = getattr(runner, "_stale_guard_is_idle", None)
        idle = is_idle(cfg["idle_timeout_minutes"] * 60) if callable(is_idle) else \
            stale_guard_is_idle(runner, cfg["idle_timeout_minutes"] * 60)
        if not idle:
            return

        # The budget is the only protection against a restart loop: no record, no restart.
        if not budget_writable(home):
            alert_state["guard_disabled"] = True
            if not alert_state.get("budget_write_alerted"):
                alert_state["budget_write_alerted"] = True
                if alert_cfg:
                    send_operator_alert(alert_cfg["channel"], format_budget_unwritable_alert(budget_path(home)))
            return

        fut = safe_schedule_threadsafe(
            _stale_guard_request_restart(runner), loop, logger=logger,
            log_message="stale-guard: failed to schedule restart on the event loop", log_level=logging.ERROR)
        if fut is None:
            return
        logger.warning("stale-guard: idle and stale — requesting planned restart")
        if not record_auto_restart(home, now):
            # The restart is queued but the mark did not land: automation can't be trusted any more,
            # and the human must hear it from more than the log.
            alert_state["guard_disabled"] = True
            _stale_guard_send_alert(alert_cfg, format_budget_write_failed_alert(budget_path(home)))
    except Exception:  # noqa: BLE001 -- the guard must never break housekeeping
        logger.warning("stale-guard tick failed", exc_info=True)


def stale_guard_chore(runner, loop, cfg, boot_label):
    """The housekeeping chore entry: ``(every, label, fn)`` or None when not armed."""
    if not cfg or runner is None:
        return None
    state = {"boot_label": boot_label or datetime.now().strftime("%H:%M:%S")}
    return (cfg["check_every_minutes"], "Stale-code guard", lambda: _stale_guard_tick(runner, cfg, state, loop=loop))
