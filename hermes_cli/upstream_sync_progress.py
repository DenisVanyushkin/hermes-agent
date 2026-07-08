"""Pure state-machine for upstream-sync verbose progress reporting.

A host-side reporter polls the live rebase state and feeds snapshots here; this
module decides which milestone/heartbeat message to post (deduplicated by state
key) so the reporter itself stays I/O-only. No side effects, fully testable.
"""

from __future__ import annotations

from typing import Optional

_TERMINAL_KEYS = frozenset({"success", "rollback"})


def is_terminal(key: Optional[str]) -> bool:
    """True when ``key`` is a terminal state — the reporter should stop."""
    return key in _TERMINAL_KEYS


def _classify(snapshot: dict) -> str:
    """Derive the current state key from a raw snapshot."""
    status = snapshot.get("finalize_status")
    if status == "ok":
        return "success"
    if status in ("rollback", "failed"):
        return "rollback"
    if snapshot.get("finalize_requested"):
        return "finalizing"
    if snapshot.get("rebasing"):
        return "rebasing"
    # Not rebasing anymore but a backup exists and no finalize yet -> rebase just
    # finished (sandbox agent done, host finalizer not started).
    if snapshot.get("backup_ref") and snapshot.get("applied") is not None:
        return "rebase_done"
    if snapshot.get("backup_ref"):
        return "backup"
    return "init"


def _message_for(key: str, snapshot: dict) -> Optional[str]:
    applied = snapshot.get("applied")
    total = snapshot.get("total")
    conflict = snapshot.get("conflict_files") or []
    if key == "backup":
        return (
            f"🔄 Upstream-sync стартовал: создан backup "
            f"`{snapshot.get('backup_ref')}`, начинаю rebase."
        )
    if key == "rebasing":
        counts = f"{applied}/{total}" if applied is not None and total else "…"
        base = f"⏳ Rebasing… {counts} коммитов применено."
        if conflict:
            base += f" Разрешаю конфликт: {', '.join(conflict)}."
        return base
    if key == "rebase_done":
        n = total or applied or "?"
        return f"✅ Rebase завершён ({n} коммитов). Запускаю smoketest + рестарт gateway…"
    if key == "finalizing":
        return "🧪 Smoketest + рестарт gateway…"
    if key == "success":
        return (
            "✅ Upstream-sync готов: local/customizations синхронизирован с upstream, "
            "smoketest прошёл, gateway поднят."
        )
    if key == "rollback":
        return (
            "❌ Upstream-sync откатился (smoketest не прошёл). "
            "Репозиторий восстановлен из backup."
        )
    return None


def render_progress(
    snapshot: dict,
    last_key: Optional[str],
    *,
    heartbeat_due: bool = False,
) -> tuple[Optional[str], str]:
    """Return ``(message_or_None, state_key)`` for the current snapshot.

    A message is emitted when the state key changes (a milestone), or — while
    rebasing — when ``heartbeat_due`` so the operator sees a live pulse with
    fresh commit counts. Otherwise the message is ``None`` (no duplicate post).
    """
    key = _classify(snapshot)
    if key != last_key:
        return _message_for(key, snapshot), key
    if key == "rebasing" and heartbeat_due:
        return _message_for(key, snapshot), key
    return None, key
