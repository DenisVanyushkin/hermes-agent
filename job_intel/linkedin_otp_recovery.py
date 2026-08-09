"""Предохранители вокруг автоматического восстановления сессии LinkedIn.

Отделено от browser_sourcing.py, где уже 1739 строк и где эта логика была бы
неотличима от логики извлечения вакансий.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_LEDGER_PATH = Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser() / "state" / "linkedin_otp_attempts.json"
ATTEMPT_WINDOW = timedelta(days=1)


class AttemptLedger:
    """Журнал попыток восстановления. Отказывает закрыто.

    Нечитаемый журнал означает «неизвестно, сколько попыток уже было».
    Трактовать это как ноль значило бы снимать предохранитель ровно в тот
    момент, когда состояние потеряно.
    """

    def __init__(self, path: Path = DEFAULT_LEDGER_PATH) -> None:
        self._path = path

    def _load(self) -> list[datetime] | None:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return [datetime.fromisoformat(stamp) for stamp in raw.get("attempts", [])]
        except Exception:
            return None

    def may_attempt(self, *, now: datetime, max_per_day: int = 1) -> bool:
        attempts = self._load()
        if attempts is None:
            return False
        recent = [stamp for stamp in attempts if now - stamp < ATTEMPT_WINDOW]
        return len(recent) < max_per_day

    def record(self, *, now: datetime) -> None:
        attempts = self._load() or []
        attempts = [stamp for stamp in attempts if now - stamp < ATTEMPT_WINDOW]
        attempts.append(now)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"attempts": [stamp.isoformat() for stamp in attempts]}, indent=2),
            encoding="utf-8",
        )


def notify_operator(message: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Уведомление о каждом срабатывании восстановления.

    Переиспользует доставку через гейтвей из shadow_advisory: тот же путь, та
    же авторизация, тот же формат отчёта о неудаче доставки.
    """
    from job_intel.shadow_advisory import post_advisory

    channel = os.getenv("JOB_INTEL_LINKEDIN_ALERT_CHANNEL", "").strip() or None
    return post_advisory(message, dry_run=dry_run, channel=channel)
