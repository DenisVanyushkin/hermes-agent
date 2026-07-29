"""Модель в спеке субагента обязана совпадать с моделью её тира в политике.

Это та сигнализация, которой не было. Миграция на gpt-5.6 (2026-07-10) обновила
`config/hermes-model-policy.yaml` и глобальный дефолт, но не тронула
`config/subagents/*.yaml`, и три поколения разъехались: gpt-5.4 у инженера и
роутера, gpt-5.5 у ревьюера и аудитора, gpt-5.6 в политике. Заметить это было
нечем -- единственные тесты, пинившие конфигурацию моделей субагентов, лежали
красными с 2026-07-04.

Вскрылось всё только 2026-07-29 и окольным путём: починка провайдера включила
переключение модели по роли, и гард identity начал блокировать каждый вызов
инженера, а тот выжигал бюджет итераций и рапортовал «tool-calling budget
exhausted».

Дублирование пока остаётся -- модель названа и в политике, и в спеке. Тест не
убирает его, а лишает возможности разъехаться молча: следующая миграция уронит
проверку в тот же день.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO_ROOT / "config" / "hermes-model-policy.yaml"
SPEC_DIR = REPO_ROOT / "config" / "subagents"


def _policy_tiers() -> dict:
    return yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))["tiers"]


def _model_entries():
    """Каждое место в спеках, где названа конкретная модель."""
    entries = []

    def visit(node, spec_name: str, path: str):
        if isinstance(node, dict):
            if "model" in node and "provider" in node:
                entries.append((spec_name, path, node))
            for key, value in node.items():
                visit(value, spec_name, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                visit(value, spec_name, f"{path}[{index}]")

    for spec_path in sorted(SPEC_DIR.glob("*.yaml")):
        visit(yaml.safe_load(spec_path.read_text(encoding="utf-8")), spec_path.name, "")
    return entries


def test_there_are_model_entries_to_check():
    # Страховка от бессодержательности: если обход перестанет что-то находить,
    # все проверки ниже станут зелёными и бесполезными.
    assert len(_model_entries()) >= 15


@pytest.mark.parametrize("spec_name,path,entry", _model_entries())
def test_every_model_entry_names_its_policy_tier(spec_name, path, entry):
    assert entry.get("tier"), (
        f"{spec_name}{path}: модель названа без тира. Тир -- единственное, что "
        "связывает спеку с политикой; без него запись снова сможет разъехаться."
    )


@pytest.mark.parametrize("spec_name,path,entry", _model_entries())
def test_every_model_entry_matches_its_tier_in_the_policy(spec_name, path, entry):
    tiers = _policy_tiers()
    tier_name = entry.get("tier")
    assert tier_name in tiers, f"{spec_name}{path}: тир {tier_name!r} отсутствует в политике"

    tier = tiers[tier_name]
    assert entry["model"] == tier["model"], (
        f"{spec_name}{path}: модель {entry['model']!r} разошлась с тиром "
        f"{tier_name!r} ({tier['model']!r}). Поменяли политику -- поменяйте спеку."
    )
    assert entry["provider"] == tier["provider"], (
        f"{spec_name}{path}: провайдер {entry['provider']!r} разошёлся с тиром "
        f"{tier_name!r} ({tier['provider']!r})"
    )


def test_a_class_always_means_the_same_tier():
    # Класс -- словарь спек, тир -- словарь политики. Пока они разные, один
    # класс обязан означать ровно один тир, иначе соответствие бессмысленно.
    by_class: dict[str, set[str]] = {}
    for spec_name, path, entry in _model_entries():
        if entry.get("class") and entry.get("tier"):
            by_class.setdefault(str(entry["class"]), set()).add(str(entry["tier"]))

    ambiguous = {cls: tiers for cls, tiers in by_class.items() if len(tiers) > 1}
    assert not ambiguous, f"класс указывает на разные тиры: {ambiguous}"


def test_the_policy_offers_a_cheap_tier_for_hot_paths():
    # Роутер и general_operator сидели на самой дешёвой модели поколения 5.4.
    # В 5.6 эквивалента mini нет, поэтому дешёвый тир заведён явно: если
    # появится модель дешевле luna, менять придётся одно место, а не пять.
    tiers = _policy_tiers()
    assert "cheap" in tiers
    assert tiers["cheap"]["model"]
