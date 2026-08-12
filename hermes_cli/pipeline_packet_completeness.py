"""Полнота пакета ревью: у каждого изменённого файла должно быть описание.

Свойство вычислимо из git-дельты и `changes[]`. Оно вынесено из LLM-ревьюера,
потому что находка `undescribed_changed_file` стоила раунда доработки из трёх,
и три прогона подряд (2026-07-29 дважды, 2026-08-12) умерли на бумажке при
зелёных тестах.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from hermes_cli.pipeline_reviewer_packet import sanitize_change_summary

COMPLETE = "complete"
INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class PacketCompleteness:
    status: str
    undescribed_paths: list[str]
    described_paths: list[str]

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "undescribed_paths": list(self.undescribed_paths),
            "described_paths": list(self.described_paths),
        }


def _normalize_path(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/") or None


def evaluate_packet_completeness(*, changed_files: Any, changes: Any) -> PacketCompleteness:
    described: set[str] = set()
    if isinstance(changes, list):
        for item in changes:
            if not isinstance(item, Mapping):
                continue
            path = _normalize_path(item.get("path"))
            if path is None:
                continue
            if sanitize_change_summary(item.get("summary")):
                described.add(path)

    undescribed: list[str] = []
    covered: list[str] = []
    seen: set[str] = set()
    for raw in list(changed_files or []):
        path = _normalize_path(raw)
        if path is None or path in seen:
            continue
        seen.add(path)
        (covered if path in described else undescribed).append(path)

    return PacketCompleteness(
        status=COMPLETE if not undescribed else INCOMPLETE,
        undescribed_paths=sorted(undescribed),
        described_paths=sorted(covered),
    )
