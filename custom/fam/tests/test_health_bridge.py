"""Phase 6b Task 3: bridge_readiness probe tests."""
from fam import health

CONNECT = ["✓ telegram connected", "✓ whatsapp connected"]
DISCONNECT = ["✓ telegram disconnected", "✓ whatsapp disconnected", "[Whatsapp] Bridge exited"]


def _cfg(log_path):
    return {
        "gateway_log_path": str(log_path),
        "readiness_markers_connect": CONNECT,
        "readiness_markers_disconnect": DISCONNECT,
    }


def test_missing_file_is_down(tmp_path):
    log_path = tmp_path / "gateway.log"
    result = health.bridge_readiness(None, _cfg(log_path))
    assert result["status"] == "down"
    assert "отсутствует" in result["detail"]


def test_last_line_connect_is_ok(tmp_path):
    log_path = tmp_path / "gateway.log"
    log_path.write_text(
        "2026-07-14 10:00:00 ✓ telegram disconnected\n"
        "2026-07-14 10:01:00 ✓ telegram connected\n",
        encoding="utf-8",
    )
    result = health.bridge_readiness(None, _cfg(log_path))
    assert result["status"] == "ok"
    assert "telegram connected" in result["detail"]


def test_last_line_disconnect_is_down(tmp_path):
    log_path = tmp_path / "gateway.log"
    log_path.write_text(
        "2026-07-14 10:00:00 ✓ whatsapp connected\n"
        "2026-07-14 10:01:00 ✓ telegram disconnected\n",
        encoding="utf-8",
    )
    result = health.bridge_readiness(None, _cfg(log_path))
    assert result["status"] == "down"
    assert "telegram disconnected" in result["detail"]


def test_no_markers_present_is_ok(tmp_path):
    log_path = tmp_path / "gateway.log"
    log_path.write_text(
        "2026-07-14 10:00:00 some random info line\n"
        "2026-07-14 10:01:00 another unrelated line\n",
        encoding="utf-8",
    )
    result = health.bridge_readiness(None, _cfg(log_path))
    assert result["status"] == "ok"
    assert "нет свежего маркера" in result["detail"]


def test_later_connect_after_disconnect_is_down(tmp_path):
    log_path = tmp_path / "gateway.log"
    log_path.write_text(
        "2026-07-14 10:00:00 ✓ telegram connected\n"
        "2026-07-14 10:01:00 ✓ telegram disconnected\n",
        encoding="utf-8",
    )
    result = health.bridge_readiness(None, _cfg(log_path))
    assert result["status"] == "down"


def test_later_disconnect_after_connect_wait_is_ok(tmp_path):
    log_path = tmp_path / "gateway.log"
    log_path.write_text(
        "2026-07-14 10:00:00 ✓ telegram disconnected\n"
        "2026-07-14 10:01:00 ✓ telegram connected\n",
        encoding="utf-8",
    )
    result = health.bridge_readiness(None, _cfg(log_path))
    assert result["status"] == "ok"
