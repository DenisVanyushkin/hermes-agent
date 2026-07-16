"""Regression tests: bridge.log must be bounded, not grow forever.

The WhatsApp bridge's stdout/stderr are redirected into bridge.log, opened in
append mode so restarts don't lose history. But append-only with no size
bound means unbounded growth over the bridge's crash-loop lifetime. Before
the file is (re)opened in append mode, ``_rotate_bridge_log_if_large`` renames
it to bridge.log.1 (replacing any prior generation) once it exceeds the cap,
keeping one generation of post-mortem history without unbounded growth.
"""

from plugins.platforms.whatsapp.adapter import _rotate_bridge_log_if_large


class TestRotateBridgeLogIfLarge:
    def test_small_file_is_not_rotated(self, tmp_path):
        log = tmp_path / "bridge.log"
        log.write_text("hello\n")
        _rotate_bridge_log_if_large(log, max_bytes=1024)
        assert log.read_text() == "hello\n"
        assert not (tmp_path / "bridge.log.1").exists()

    def test_large_file_is_rotated_to_dot_1(self, tmp_path):
        log = tmp_path / "bridge.log"
        log.write_text("x" * 100)
        _rotate_bridge_log_if_large(log, max_bytes=10)
        assert not log.exists()
        backup = tmp_path / "bridge.log.1"
        assert backup.read_text() == "x" * 100
        # Simulate the caller reopening in append mode after rotation.
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("fresh\n")
        assert log.read_text() == "fresh\n"

    def test_rotation_replaces_prior_dot_1(self, tmp_path):
        log = tmp_path / "bridge.log"
        (tmp_path / "bridge.log.1").write_text("old backup")
        log.write_text("y" * 100)
        _rotate_bridge_log_if_large(log, max_bytes=10)
        assert (tmp_path / "bridge.log.1").read_text() == "y" * 100

    def test_missing_file_is_noop(self, tmp_path):
        log = tmp_path / "bridge.log"
        _rotate_bridge_log_if_large(log, max_bytes=10)
        assert not log.exists()
        assert not (tmp_path / "bridge.log.1").exists()
