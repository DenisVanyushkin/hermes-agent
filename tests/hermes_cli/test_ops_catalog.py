import pytest

from hermes_cli.ops_catalog import (
    CATALOG,
    RISK_DESTROY,
    RISK_MUTATE,
    RISK_READ,
    OpsCatalogError,
    resolve_operation,
)


def test_git_status_resolves_to_an_exact_argv():
    op = resolve_operation("git_status", {})
    assert op.argv == ("git", "status", "--short", "--branch")
    assert op.risk == RISK_READ
    assert op.irreversible is None


def test_git_fetch_carries_the_validated_remote():
    op = resolve_operation("git_fetch", {"remote": "origin"})
    assert op.argv == ("git", "fetch", "--quiet", "origin")


def test_unknown_operation_is_refused():
    with pytest.raises(OpsCatalogError):
        resolve_operation("rm_rf", {})


def test_hostile_parameter_never_reaches_argv():
    # Каталог опирается на валидаторы: значение отклоняется до построения argv.
    with pytest.raises(OpsCatalogError):
        resolve_operation("git_log", {"branch": "--upload-pack=/bin/sh"})


def test_git_push_builds_an_explicit_argv_without_force():
    op = resolve_operation("git_push", {"remote": "origin", "branch": "local/customizations"})
    assert op.argv == ("git", "push", "origin", "local/customizations")
    assert op.risk == RISK_MUTATE
    assert "force" not in " ".join(op.argv)


def test_service_restart_uses_non_interactive_sudo():
    op = resolve_operation("service_restart", {"unit": "job-intel-daily.service"})
    assert op.argv == ("sudo", "-n", "systemctl", "restart", "job-intel-daily.service")


def test_gateway_restart_goes_through_the_cli_subcommand():
    # Не nohup и не `--replace` руками: у гейтвея есть штатный субкоманд.
    op = resolve_operation("gateway_restart", {})
    assert op.argv == ("hermes", "gateway", "restart")


def test_force_push_exists_only_with_lease_and_declares_irreversibility():
    op = resolve_operation(
        "git_push_force_with_lease", {"remote": "origin", "branch": "local/customizations"}
    )
    assert op.argv == ("git", "push", "--force-with-lease", "origin", "local/customizations")
    assert op.risk == RISK_DESTROY
    assert op.irreversible


def test_dangerous_operations_are_absent_rather_than_forbidden():
    # Отсутствие -- единственная защита, которую нельзя обойти апрувом не глядя.
    for absent in ("git_push_force", "shell", "rm", "chmod", "chown", "edit_env"):
        assert absent not in CATALOG


def test_the_only_force_push_in_the_catalog_carries_a_lease():
    forcing = [op_id for op_id in CATALOG if "force" in op_id]
    assert forcing == ["git_push_force_with_lease"]
    argv = resolve_operation("git_push_force_with_lease", {"remote": "origin", "branch": "main"}).argv
    assert "--force-with-lease" in argv
    assert "--force" not in argv


def test_every_destroy_operation_declares_what_cannot_be_undone():
    for op in CATALOG.values():
        if op.risk == RISK_DESTROY:
            assert op.irreversible, f"{op.op_id} без описания необратимости"


def test_host_path_stat_resolves_to_an_exact_argv():
    op = resolve_operation("host_path_stat", {"path": "/var/lib/browser-desktop/playwright-venv"})
    assert op.risk == RISK_READ
    assert op.argv == (
        "stat",
        "--format=%n type=%F owner=%U:%G mode=%a size=%s mtime=%y",
        "/var/lib/browser-desktop/playwright-venv",
    )
    assert op.irreversible is None


def test_host_path_stat_never_reads_file_contents():
    # Инвариант класса read: метаданные -- да, байты файла -- нет. Иначе инспекция
    # окружения становится способом прочитать .env и auth.json.
    op = resolve_operation("host_path_stat", {"path": "/etc/job-intel"})
    assert not {"cat", "head", "tail", "less", "strings", "xxd", "od"} & set(op.argv)


@pytest.mark.parametrize(
    "params",
    [
        {"path": "/root/.ssh/id_rsa"},
        {"path": "/home/hermes/.hermes/../../root/.bashrc"},
        {"path": ""},
        {},
    ],
)
def test_host_path_stat_refuses_paths_outside_the_allowlist(params):
    with pytest.raises(OpsCatalogError):
        resolve_operation("host_path_stat", params)


def test_host_listening_ports_takes_no_parameters():
    op = resolve_operation("host_listening_ports", {})
    assert op.risk == RISK_READ
    assert op.argv == ("ss", "--listening", "--tcp", "--numeric", "--processes")
    # Параметров нет вовсе: нечего валидировать, нечем управлять извне.
    assert resolve_operation("host_listening_ports", {"anything": "ignored"}).argv == op.argv


def test_venv_packages_executes_the_venv_python():
    op = resolve_operation("venv_packages", {"venv": "/var/lib/browser-desktop/playwright-venv"})
    assert op.risk == RISK_READ
    assert op.argv == (
        "/var/lib/browser-desktop/playwright-venv/bin/python",
        "-m",
        "pip",
        "list",
        "--format=json",
    )


@pytest.mark.parametrize(
    "params",
    [
        {"venv": "/tmp/attacker-venv"},
        {"venv": "/var/lib/browser-desktop"},
        {},
    ],
)
def test_venv_packages_refuses_venvs_outside_the_allowlist(params):
    with pytest.raises(OpsCatalogError):
        resolve_operation("venv_packages", params)


def test_host_inspection_operations_are_all_read_class():
    for op_id in ("host_path_stat", "host_listening_ports", "venv_packages"):
        assert CATALOG[op_id].risk == RISK_READ
        assert CATALOG[op_id].irreversible is None
