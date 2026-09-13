from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]


def _load_script(name: str):
    path = ROOT / "scripts" / name
    assert path.exists(), f"watchdog script is missing: {path}"
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_source_db(path: Path, statuses: list[tuple[str, int]]) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY,
            mode TEXT NOT NULL,
            started_at TEXT NOT NULL
        );
        CREATE TABLE source_kpi_run (
            run_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            source_status TEXT,
            found_count INTEGER,
            enabled INTEGER DEFAULT 1,
            skip_reason TEXT
        );
        """
    )
    for run_id, (status, found_count) in enumerate(statuses, start=1):
        connection.execute(
            "INSERT INTO runs(id, mode, started_at) VALUES (?, 'daily', ?)",
            (run_id, f"2026-09-13T00:{run_id:02d}:00+00:00"),
        )
        connection.execute(
            "INSERT INTO source_kpi_run(run_id, source, source_status, found_count) "
            "VALUES (?, 'linkedin', ?, ?)",
            (run_id, status, found_count),
        )
    connection.commit()
    connection.close()


def test_ddns_does_not_set_endpoint_when_host_resolution_fails(tmp_path: Path) -> None:
    watchdog = _load_script("job_intel_ddns_watchdog.py")
    state = tmp_path / "linkedin-ddns.json"
    previous = '{"endpoint":"213.211.78.39:3785"}\n'
    state.write_text(previous, encoding="utf-8")

    result = watchdog.resolve_and_store(
        config_text=(
            "[Interface]\nPrivateKey = ignored\n[Peer]\n"
            "PublicKey = peer-key\nEndpoint = router.example:3785\n"
        ),
        resolve=lambda _host: [],
        state_path=state,
        now="2026-09-13T17:00:00+00:00",
    )

    assert result.status == "resolution_failed"
    assert state.read_text(encoding="utf-8") == previous


def test_ddns_sets_endpoint_only_when_resolved_address_differs() -> None:
    watchdog = _load_script("job_intel_ddns_watchdog.py")
    commands: list[list[str]] = []

    result = watchdog.apply_endpoint(
        state_text=json.dumps(
            {
                "peer_key": "peer-key",
                "endpoint_port": "3785",
                "resolved_addresses": ["213.211.78.39"],
            }
        ),
        run=lambda command: commands.append(list(command)),
        current_endpoint=lambda _interface, _peer: "95.56.123.201:3785",
        interface="wg0-ln",
    )

    assert result.status == "updated"
    assert commands == [
        [
            "wg",
            "set",
            "wg0-ln",
            "peer",
            "peer-key",
            "endpoint",
            "213.211.78.39:3785",
        ]
    ]


def test_source_alert_sends_once_after_three_bad_runs_and_resets_on_recovery(tmp_path: Path) -> None:
    watchdog = _load_script("job_intel_source_alert.py")
    db_path = tmp_path / "state.sqlite3"
    state_path = tmp_path / "alert-state.json"
    _create_source_db(db_path, [("error", 0), ("blocked", 0), ("empty", 0)])
    messages: list[tuple[str, str]] = []

    def deliver(message: str, channel: str):
        messages.append((message, channel))
        return type("Delivery", (), {"success": True, "status": "sent", "error": None})()

    first = watchdog.scan_and_alert(
        db_path=db_path,
        state_path=state_path,
        threshold=3,
        channel="executive_search_report",
        deliver=deliver,
    )
    second = watchdog.scan_and_alert(
        db_path=db_path,
        state_path=state_path,
        threshold=3,
        channel="executive_search_report",
        deliver=deliver,
    )

    assert first["alert_sent"] is True
    assert second["alert_sent"] is False
    assert len(messages) == 1
    assert messages[0][1] == "executive_search_report"
    assert "consecutive_bad_runs=3" in messages[0][0]

    connection = sqlite3.connect(db_path)
    connection.execute(
        "INSERT INTO runs(id, mode, started_at) VALUES (4, 'daily', '2026-09-13T00:04:00+00:00')"
    )
    connection.execute(
        "INSERT INTO source_kpi_run(run_id, source, source_status, found_count) "
        "VALUES (4, 'linkedin', 'ok', 7)"
    )
    connection.commit()
    connection.close()

    recovered = watchdog.scan_and_alert(
        db_path=db_path,
        state_path=state_path,
        threshold=3,
        channel="executive_search_report",
        deliver=deliver,
    )

    assert recovered["alert_sent"] is False
    assert recovered["consecutive_bad_runs"] == 0
    assert json.loads(state_path.read_text())["alert_sent"] is False


def test_source_alert_delivery_failure_is_visible_and_not_marked_sent(tmp_path: Path) -> None:
    watchdog = _load_script("job_intel_source_alert.py")
    db_path = tmp_path / "state.sqlite3"
    state_path = tmp_path / "alert-state.json"
    _create_source_db(db_path, [("error", 0), ("error", 0), ("error", 0)])

    def deliver(_message: str, _channel: str):
        return type("Delivery", (), {"success": False, "status": "failed", "error": "test transport down"})()

    with pytest.raises(RuntimeError, match="test transport down"):
        watchdog.scan_and_alert(
            db_path=db_path,
            state_path=state_path,
            threshold=3,
            channel="executive_search_report",
            deliver=deliver,
        )

    state = json.loads(state_path.read_text())
    assert state["alert_sent"] is False


def test_production_watchdog_units_declare_scoped_recovery_guards() -> None:
    ddns_unit = ROOT / "deploy/systemd/job-intel-linkedin-ddns-watchdog.service"
    browser_unit = ROOT / "deploy/systemd/job-intel-linkedin-browser-supervisor.service"
    alert_unit = ROOT / "deploy/systemd/job-intel-source-alert.service"
    for path in (ddns_unit, browser_unit, alert_unit):
        assert path.exists(), path

    assert "User=root" in ddns_unit.read_text()
    assert "ln-eg" in ddns_unit.read_text()
    assert "wg0-ln" in ddns_unit.read_text()
    browser_text = browser_unit.read_text()
    assert "linkedin-public-v1" in browser_text
    assert "19271" in browser_text
    assert "Restart=on-failure" in browser_text
    assert "KillMode=control-group" in browser_text
    assert "/var/lib/browser-desktop" in browser_text
    assert "/var/log/browser-desktop" not in browser_text
    alert_text = alert_unit.read_text()
    assert "executive_search_report" in alert_text
    assert "JOB_INTEL_SOURCE_ALERT_STATE" in alert_text
