"""Per-agent iteration budget — thread-safe consume/refund counter.

Each ``AIAgent`` (parent or subagent) holds its own :class:`IterationBudget`: the parent's
cap is ``max_iterations`` (default 500), each subagent's ``delegation.max_iterations``
(default 50), so total iterations across parent + subagents can exceed the parent's cap.
"""

from __future__ import annotations

import math
import threading

#: Kept identical to the _turn_exit_reason set in agent/conversation_loop.py.
BUDGET_EXHAUSTED_REASON = "budget_exhausted"


def normalize_budget_warning_ratio(value) -> float | None:
    """A finite ratio strictly between zero and one, or None (feature off)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return None
    return ratio if math.isfinite(ratio) and 0 < ratio < 1 else None


class IterationBudget:
    """Thread-safe iteration counter; ``execute_code`` (programmatic tool calling)
    iterations are refunded via :meth:`refund` so they don't eat into the budget."""

    def __init__(self, max_total: int):
        self.max_total = max_total
        self._used = 0
        self._lock = threading.Lock()

    def consume(self) -> bool:
        """Try to consume one iteration.  Returns True if allowed."""
        with self._lock:
            if self._used >= self.max_total:
                return False
            self._used += 1
            return True

    def refund(self) -> None:
        """Give back one iteration (e.g. for execute_code turns)."""
        with self._lock:
            if self._used > 0:
                self._used -= 1

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self.max_total - self._used)

    @property
    def exhausted(self) -> bool:
        with self._lock:
            return self._used >= self.max_total

    @property
    def exhausted_reason(self) -> str | None:
        """``"budget_exhausted"`` once spent, otherwise ``None``.

        The string matches the ``_turn_exit_reason`` the conversation loop
        already sets, so the budget object and the turn outcome cannot describe
        the same condition with two different words.
        """
        return BUDGET_EXHAUSTED_REASON if self.exhausted else None


__all__ = ["IterationBudget", "BUDGET_EXHAUSTED_REASON"]
