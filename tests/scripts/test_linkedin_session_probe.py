from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "linkedin_session_probe.sh"


def _body() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_script_is_syntactically_valid() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_probe_runs_inside_the_namespace() -> None:
    """exit_ip измеряет выход своего процесса. Снаружи namespace он вернёт
    адрес хоста, и точка серии будет записана с неверным происхождением."""
    assert 'ip netns exec "${NETNS}"' in _body()


def test_label_is_required() -> None:
    """Точка без метки не встраивается в серию: непонятно, к какому моменту
    относительно прогона она снята."""
    assert 'LABEL="${1:?' in _body()


def test_result_is_appended_not_overwritten() -> None:
    body = _body()
    assert "tee -a" in body
    assert "> \"${LOG}\"" not in body


def test_cookie_values_are_never_written_to_the_log() -> None:
    """В журнал попадают имя и срок, но не значение куки."""
    body = _body()
    assert '"li_at_expires"' in body
    assert "c[\"value\"]" not in body


def test_paths_do_not_depend_on_home() -> None:
    """Скрипт требует root ради `ip netns exec`, а sudo подменяет HOME на
    /root. Этот проект на том же самом ломался в rebase-скрипте: пути уезжают
    в несуществующий /root/.hermes. Venv находится от расположения самого
    скрипта, журнал — от HERMES_HOME с явным умолчанием."""
    body = _body()
    assert "${HOME}" not in body
    assert "SCRIPT_DIR" in body
    assert "HERMES_HOME:-/home/hermes/.hermes" in body
