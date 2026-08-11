"""Правила автозаписи: повторяющиеся и разовые.

Матчинг ведётся в клубном времени: правило «вторник 19:00» — это вторник и
19:00 в Алматы, а не на сервере.
"""

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta

from fitness.models import ClassSlot
from fitness.store import JsonStore

RULES_FILE = "rules.json"


@dataclass(frozen=True)
class WatchRule:
    rule_id: str
    kind: str  # "recurring" | "oneshot"
    title_pattern: str
    club_id: str | None
    weekday: int | None  # 0=понедельник .. 6=воскресенье, клубное время
    at_time: time | None
    window_minutes: int
    trainer: str | None
    waitlist_ok: bool
    target_date: date | None
    expires_at: datetime | None
    active: bool


def rule_matches(rule: WatchRule, slot: ClassSlot) -> bool:
    if not rule.active:
        return False
    if rule.title_pattern.casefold() not in slot.title.casefold():
        return False
    if rule.club_id is not None and rule.club_id != slot.club_id:
        return False
    if rule.trainer is not None and rule.trainer != slot.trainer:
        return False

    local = slot.local_start
    if rule.kind == "oneshot":
        if rule.target_date is not None and local.date() != rule.target_date:
            return False
    elif rule.weekday is not None and local.weekday() != rule.weekday:
        return False

    if rule.at_time is not None:
        wanted = local.replace(
            hour=rule.at_time.hour, minute=rule.at_time.minute, second=0, microsecond=0
        )
        if abs(local - wanted) > timedelta(minutes=rule.window_minutes):
            return False
    return True


def is_expired(rule: WatchRule, now: datetime) -> bool:
    return rule.expires_at is not None and rule.expires_at <= now


def _serialize(rule: WatchRule) -> dict:
    raw = asdict(rule)
    raw["at_time"] = rule.at_time.isoformat() if rule.at_time else None
    raw["target_date"] = rule.target_date.isoformat() if rule.target_date else None
    raw["expires_at"] = rule.expires_at.isoformat() if rule.expires_at else None
    return raw


def _deserialize(raw: dict) -> WatchRule:
    return WatchRule(
        rule_id=raw["rule_id"],
        kind=raw["kind"],
        title_pattern=raw["title_pattern"],
        club_id=raw.get("club_id"),
        weekday=raw.get("weekday"),
        at_time=time.fromisoformat(raw["at_time"]) if raw.get("at_time") else None,
        window_minutes=raw.get("window_minutes", 30),
        trainer=raw.get("trainer"),
        waitlist_ok=raw.get("waitlist_ok", True),
        target_date=date.fromisoformat(raw["target_date"]) if raw.get("target_date") else None,
        expires_at=datetime.fromisoformat(raw["expires_at"]) if raw.get("expires_at") else None,
        active=raw.get("active", True),
    )


class RuleStore:
    def __init__(self) -> None:
        self._json = JsonStore(RULES_FILE)

    def load(self) -> list[WatchRule]:
        raw = self._json.read(default={"version": 1, "rules": []})
        return [_deserialize(row) for row in raw.get("rules", [])]

    def save(self, rules: list[WatchRule]) -> None:
        self._json.write({"version": 1, "rules": [_serialize(r) for r in rules]}, mode=0o644)

    def add(self, rule: WatchRule) -> None:
        rules = [r for r in self.load() if r.rule_id != rule.rule_id]
        rules.append(rule)
        self.save(rules)

    def remove(self, rule_id: str) -> bool:
        rules = self.load()
        kept = [r for r in rules if r.rule_id != rule_id]
        if len(kept) == len(rules):
            return False
        self.save(kept)
        return True
