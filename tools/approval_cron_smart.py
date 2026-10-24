"""Fork: ``approvals.cron_mode: smart`` for upstream's cron approval gates.

Upstream reads ``cron_mode`` as approve|deny; every other value, ``smart``
included, is deny (``tools.approval_context._binary_approval_mode``). That
stays as it is, so all of upstream's cron logic is closed by default.

Each of the three cron decision points in ``tools/approval.py`` (flagged
actions in ``_run_approval_gate``, terminal commands in ``_unattended_deny``,
``check_execute_code_guard``) calls :func:`smart_cron_allows` right before it
blocks. It returns True only when ``cron_mode`` is ``smart`` and the reviewer
(upstream's ``_smart_approve``) answered APPROVE. Anything else — another
mode, DENY, ESCALATE, an invalid answer, an exception — returns False and the
upstream block stands. A defect here can therefore only keep a gate closed.

A cron session has no human to fall back to, so uncertainty fails closed.
The reviewer is asked once per decision; reusing one answer is what keeps a
DENY from being overturned by a second, non-deterministic one.

The ``approval: smart <VERDICT> for <gate>`` log lines are what
``scripts/smart_approval_metrics.sh`` counts.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("tools.approval")

#: Cap on the text sent to the reviewer: a 200 KB script would blow the aux context.
_REVIEW_SUBJECT_MAX_CHARS = 8192


def _truncate_for_review(text: str) -> str:
    """Cap the reviewer payload. A 200 KB script would blow the aux context."""
    if len(text) <= _REVIEW_SUBJECT_MAX_CHARS:
        return text
    head = text[: _REVIEW_SUBJECT_MAX_CHARS - 200]
    return f"{head}\n\n[... truncated {len(text) - len(head)} chars ...]"


def cron_mode_is_smart() -> bool:
    """True when ``approvals.cron_mode`` is ``smart`` (case- and space-insensitive)."""
    try:
        from hermes_cli.config import cfg_get, load_config_readonly
        mode = str(cfg_get(load_config_readonly(), "approvals", "cron_mode", default="deny"))
    except Exception:
        return False
    return mode.lower().strip() == "smart"


def smart_cron_allows(gate: str, subject_text: str, description: str) -> bool:
    """True only for ``cron_mode: smart`` and an explicit reviewer APPROVE."""
    try:
        if not cron_mode_is_smart():
            return False
        from tools.approval_smart import _smart_approve
        verdict = _smart_approve(_truncate_for_review(str(subject_text or "")), description)
    except Exception as exc:  # _smart_approve already swallows; belt and braces
        logger.warning("approval: smart review raised for %s (%s) — failing closed", gate, exc)
        return False
    if verdict == "approve":
        logger.info("approval: smart APPROVE for %s: %s", gate, description)
        return True
    if verdict == "deny":
        logger.warning("approval: smart DENY for %s: %s", gate, description)
    else:
        logger.warning("approval: smart ESCALATE/unavailable for %s (verdict=%r): %s", gate, verdict, description)
    return False
