"""Сессионный /reasoning освобождает ход от пола роль-политики.

К моменту, когда ход доходит до пола, сессионный override и глобальный конфиг
неразличимы — оба уже сплющены в {"enabled": True, "effort": ...}. Поэтому факт
«уровень задан человеком для этой сессии» приходится нести отдельным признаком.
"""
from types import SimpleNamespace

from gateway.run import GatewayRunner


def _runner(overrides=None, session_key="slack:C1:U1"):
    return SimpleNamespace(
        _session_reasoning_overrides=overrides or {},
        _session_key_for_source=lambda source: session_key,
    )


def test_an_override_for_this_session_is_reported():
    runner = _runner({"slack:C1:U1": {"enabled": True, "effort": "low"}})

    active = GatewayRunner._session_reasoning_override_active(
        runner, session_key="slack:C1:U1"
    )

    assert active is True


def test_no_override_is_reported_false():
    runner = _runner()

    assert GatewayRunner._session_reasoning_override_active(
        runner, session_key="slack:C1:U1"
    ) is False


def test_an_override_for_a_different_session_does_not_leak():
    runner = _runner({"telegram:42:42": {"enabled": True, "effort": "low"}})

    assert GatewayRunner._session_reasoning_override_active(
        runner, session_key="slack:C1:U1"
    ) is False


def test_the_key_is_derived_from_the_source_when_not_given():
    runner = _runner({"slack:C1:U1": {"enabled": True, "effort": "low"}})

    assert GatewayRunner._session_reasoning_override_active(
        runner, source=object()
    ) is True


def test_a_failing_key_derivation_is_not_an_exemption():
    """Не смогли определить сессию — значит явного override не видели."""

    def _boom(source):
        raise RuntimeError("no key")

    runner = SimpleNamespace(
        _session_reasoning_overrides={"slack:C1:U1": {"effort": "low"}},
        _session_key_for_source=_boom,
    )

    assert GatewayRunner._session_reasoning_override_active(
        runner, source=object()
    ) is False


def test_every_agent_the_gateway_hands_out_gets_the_flag():
    """Признак бесполезен, если он не доехал до объекта агента."""
    import pathlib
    import re

    src = pathlib.Path("gateway/run.py").read_text()
    stamps = re.findall(r"agent\._reasoning_floor_exempt = ", src)
    assert len(stamps) == 3, f"expected 3 agent stamps, found {len(stamps)}"


def test_the_exemption_is_never_shared_state_on_the_gateway():
    """Признак — на локальной переменной хода, а не на синглтоне гейтвея.

    GatewayRunner один на процесс и обслуживает сессии параллельно, а между
    вычислением признака и штампом на агенте есть точки await. Хранение его на
    self означало бы, что соседняя сессия успевает подменить значение: чужой ход
    либо теряет своё освобождение, либо получает чужое и молча обходит пол.
    """
    import pathlib

    src = pathlib.Path("gateway/run.py").read_text()

    assert "self._reasoning_floor_exempt" not in src
    assert src.count("agent._reasoning_floor_exempt = ") == 3
    assert src.count("_floor_exempt = self._session_reasoning_override_active(") == 2
