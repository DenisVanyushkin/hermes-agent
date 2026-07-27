"""Валидация значений параметров ops-операций.

Единственное место, где решается, что строка безопасна в роли argv-аргумента.
Проверяем значение, а не текст команды: команду строит код, поэтому опасны не
метасимволы shell (их некуда положить), а аргументы, которые git и systemd
трактуют как опции -- `--upload-pack=` превращает безобидный push в исполнение
произвольной программы на своей стороне.
"""
from __future__ import annotations

import re

_BRANCH_RE = re.compile(r"[A-Za-z0-9._/-]{1,200}")

ALLOWED_UNITS = frozenset({
    "job-intel-daily.service",
    "job-intel-weekly-kpi.service",
    "job-intel-semantic-shadow.service",
    "hermes-dashboard.service",
})

ALLOWED_CONTAINERS = frozenset({
    "monitoring-grafana",
    "monitoring-prometheus",
    "monitoring-loki",
    "monitoring-alertmanager",
    "monitoring-promtail",
    "monitoring-cadvisor",
    "monitoring-job-intel-exporter",
    "blackbox",
})


class OpsParamError(ValueError):
    """Значение параметра не прошло валидацию."""


def validate_branch(value: object) -> str:
    text = str(value or "").strip()
    if not _BRANCH_RE.fullmatch(text):
        raise OpsParamError("invalid_branch")
    # Дефис в начале -- это опция, а не имя ветки. Регекс его пропускает, потому
    # что дефис легален внутри имени; отсекаем отдельно.
    if text.startswith("-"):
        raise OpsParamError("invalid_branch")
    if ".." in text or text.startswith("/") or text.endswith("/"):
        raise OpsParamError("invalid_branch")
    return text


def validate_remote(value: object) -> str:
    text = str(value or "").strip()
    if text != "origin":
        raise OpsParamError("invalid_remote")
    return text


def validate_unit(value: object) -> str:
    text = str(value or "").strip()
    if text not in ALLOWED_UNITS:
        raise OpsParamError("invalid_unit")
    return text


def validate_container(value: object) -> str:
    text = str(value or "").strip()
    if text not in ALLOWED_CONTAINERS:
        raise OpsParamError("invalid_container")
    return text
