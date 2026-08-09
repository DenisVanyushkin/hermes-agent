"""Тесты подсказки о покрытии ops-каталога.

Инцидент 2026-08-09: инженер заблокировался на задаче «включи LSP и поставь
pyright» и в next_action написал точную команду `hermes lsp install pyright`.
Операции для неё в каталоге нет, но финальное сообщение говорило «дай уточнение
и повтори» -- то есть предлагало переформулировать задачу, которая невыполнима
принципиально. Подсказка отвечает на вопрос «а есть ли вообще чем это сделать».
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def module():
    return importlib.import_module("hermes_cli.ops_capability_hint")


def test_command_absent_from_catalog_is_reported_as_gap(module) -> None:
    hints = module.analyze_commands(
        ["В активной среде Hermes выполнить `hermes lsp install pyright`."]
    )

    assert [(h.command, h.op_id) for h in hints] == [("hermes lsp install pyright", None)]
    assert module.has_capability_gap(hints) is True


def test_command_covered_by_catalog_is_matched_to_its_operation(module) -> None:
    hints = module.analyze_commands(["Нужно `hermes gateway restart` после правки."])

    assert [(h.command, h.op_id) for h in hints] == [
        ("hermes gateway restart", "gateway_restart")
    ]
    assert module.has_capability_gap(hints) is False


def test_longest_matching_signature_wins(module) -> None:
    """`git push --force-with-lease` -- destroy-операция, а не обычный git_push."""

    hints = module.analyze_commands(["`git push --force-with-lease origin local/custom`"])

    assert [h.op_id for h in hints] == ["git_push_force_with_lease"]


def test_sudo_prefix_does_not_hide_the_operation(module) -> None:
    hints = module.analyze_commands(["`sudo -n systemctl restart job-intel-daily`"])

    assert [h.op_id for h in hints] == ["service_restart"]


def test_commands_are_extracted_from_plain_text_without_backticks(module) -> None:
    hints = module.analyze_commands(
        ["Сначала hermes lsp status, затем hermes lsp install pyright"]
    )

    assert [h.command for h in hints] == ["hermes lsp status", "hermes lsp install pyright"]


def test_prose_without_commands_yields_no_hints(module) -> None:
    hints = module.analyze_commands(
        ["Нет инструмента выполнения команд в активной Hermes-среде."]
    )

    assert hints == []
    assert module.has_capability_gap(hints) is False


def test_duplicate_commands_are_reported_once(module) -> None:
    hints = module.analyze_commands(
        ["`hermes lsp install pyright`", "снова `hermes lsp install pyright`"]
    )

    assert len(hints) == 1


def test_extraction_is_capped_so_a_flood_cannot_pad_the_message(module) -> None:
    texts = [f"`git log branch{index}`" for index in range(50)]

    hints = module.analyze_commands(texts)

    assert len(hints) <= module.MAX_HINTS


def test_overlong_command_is_truncated_not_echoed_whole(module) -> None:
    hints = module.analyze_commands(["`git log " + "x" * 500 + "`"])

    assert len(hints) == 1
    assert len(hints[0].command) <= module.MAX_COMMAND_CHARS


def test_hint_lines_name_the_missing_operation_explicitly(module) -> None:
    hints = module.analyze_commands(
        ["`hermes lsp install pyright`", "`hermes gateway restart`"]
    )

    lines = module.hint_lines(hints)

    assert lines[0] == "- `hermes lsp install pyright` — нет операции в каталоге"
    assert lines[1] == "- `hermes gateway restart` — есть операция gateway_restart (mutate)"


def test_every_catalog_operation_declares_a_signature() -> None:
    """Забытая сигнатура сделала бы операцию невидимой для подсказки."""

    catalog = importlib.import_module("hermes_cli.ops_catalog")

    for op_id, operation in catalog.CATALOG.items():
        assert operation.signature, f"{op_id} без сигнатуры"
        assert all(isinstance(token, str) and token for token in operation.signature)


def test_signature_tokens_really_occur_in_the_built_argv() -> None:
    """Сигнатура, разошедшаяся с argv, врала бы о том, что именно исполнится.

    Проверка на подпоследовательность, а не на приставку: venv_packages зовёт
    pip как `<venv>/bin/python -m pip list`, так что опознавательные токены
    сигнатуры идут не подряд и не с нулевой позиции.
    """

    catalog = importlib.import_module("hermes_cli.ops_catalog")
    sample_params = {
        "branch": "main",
        "remote": "origin",
        "tag": "v1.0.0",
        "unit": "job-intel-daily.service",
        "container": "monitoring-grafana",
        "path": "/var/lib/job-intel/state",
        "venv": "/home/hermes/.hermes/hermes-agent/venv",
    }

    for op_id, operation in catalog.CATALOG.items():
        argv = list(catalog.resolve_operation(op_id, sample_params).argv)
        while argv and argv[0] in {"sudo", "-n"}:
            argv.pop(0)
        if argv:
            argv[0] = argv[0].rsplit("/", 1)[-1]
        remaining = iter(argv)
        assert all(token in remaining for token in operation.signature), op_id
