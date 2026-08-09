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


from job_intel.linkedin_session import CHALLENGE_EMAIL_OTP, CHALLENGE_HARD
from job_intel.otp_mail import LINKEDIN_CODE_PATTERN, STATUS_OK

OTP_MAX_AGE = timedelta(minutes=10)
OTP_SENDER = "linkedin.com"
OTP_SUBJECT_HINT = "verification code"


@dataclass(frozen=True)
class RecoveryOutcome:
    attempted: bool
    resolved: bool
    reason: str


def recover_session(
    *,
    verdict: Any,
    page: Any,
    ledger: AttemptLedger,
    config: Any,
    now: datetime,
    otp_reader: Any,
    notifier: Any,
) -> RecoveryOutcome:
    """Восстановить сессию по коду из почты — или отказаться и сказать почему.

    Жёсткий челлендж проверяется первым и не приводит к попытке ни при каких
    условиях: автоматическое прохождение капчи или проверки документа — это та
    эскалация, ради избежания которой построена вся фаза C.
    """
    if verdict.state == CHALLENGE_HARD:
        notifier(
            "LinkedIn: жёсткий челлендж (капча или проверка документа). "
            "Автоматическое восстановление остановлено: hard_challenge_stop. "
            "Нужен ручной вход через docs/runbooks/linkedin-netns-verification.md"
        )
        return RecoveryOutcome(attempted=False, resolved=False, reason="hard_challenge_stop")

    if verdict.state != CHALLENGE_EMAIL_OTP:
        return RecoveryOutcome(attempted=False, resolved=False, reason="nothing_to_recover")

    if not ledger.may_attempt(now=now):
        return RecoveryOutcome(attempted=False, resolved=False, reason="attempt_limit")

    result = otp_reader(
        config=config,
        sender=OTP_SENDER,
        subject_hint=OTP_SUBJECT_HINT,
        pattern=LINKEDIN_CODE_PATTERN,
        max_age=OTP_MAX_AGE,
        now=now,
    )
    if result.status != STATUS_OK or not result.code:
        return RecoveryOutcome(attempted=False, resolved=False, reason=result.status)

    ledger.record(now=now)
    page.fill_code(result.code)
    page.submit()
    notifier(f"LinkedIn: автоматическое восстановление сессии выполнено в {now.isoformat()}")
    return RecoveryOutcome(attempted=True, resolved=True, reason="ok")
