"""Task 1: pure progress state-machine for upstream-sync verbose reporting."""

from hermes_cli.upstream_sync_progress import render_progress


def _snap(**kw):
    base = dict(
        backup_ref=None, rebasing=False, applied=None, total=None,
        conflict_files=[], finalize_status=None, finalize_requested=False,
        pending_present=True,
    )
    base.update(kw)
    return base


class TestRenderProgress:
    def test_backup_created_first_message(self):
        msg, key = render_progress(_snap(backup_ref="backup/pre-upstream-sync-20260708-081343"),
                                   None, heartbeat_due=False)
        assert key == "backup"
        assert "backup" in msg.lower()
        assert "20260708-081343" in msg

    def test_rebasing_reports_progress_counts(self):
        msg, key = render_progress(_snap(backup_ref="b", rebasing=True, applied=5, total=466),
                                   "backup", heartbeat_due=False)
        assert key == "rebasing"
        assert "5/466" in msg

    def test_no_duplicate_when_key_unchanged_and_no_heartbeat(self):
        msg, key = render_progress(_snap(backup_ref="b", rebasing=True, applied=6, total=466),
                                   "rebasing", heartbeat_due=False)
        assert key == "rebasing"
        assert msg is None

    def test_heartbeat_reemits_with_fresh_counts(self):
        msg, key = render_progress(_snap(backup_ref="b", rebasing=True, applied=120, total=466),
                                   "rebasing", heartbeat_due=True)
        assert key == "rebasing"
        assert "120/466" in msg

    def test_conflict_file_mentioned_while_rebasing(self):
        msg, key = render_progress(_snap(backup_ref="b", rebasing=True, applied=200, total=466,
                                         conflict_files=["gateway/run.py"]),
                                   "backup", heartbeat_due=False)
        assert key == "rebasing"
        assert "gateway/run.py" in msg

    def test_rebase_done_transition(self):
        msg, key = render_progress(_snap(backup_ref="b", rebasing=False, applied=466, total=466,
                                         pending_present=True),
                                   "rebasing", heartbeat_due=False)
        assert key == "rebase_done"
        assert "smoketest" in msg.lower() or "rebase" in msg.lower()

    def test_finalizing_when_request_present(self):
        msg, key = render_progress(_snap(backup_ref="b", rebasing=False, finalize_requested=True),
                                   "rebase_done", heartbeat_due=False)
        assert key == "finalizing"
        assert "smoketest" in msg.lower() or "gateway" in msg.lower()

    def test_success_terminal(self):
        msg, key = render_progress(_snap(backup_ref="b", finalize_status="ok", pending_present=False),
                                   "finalizing", heartbeat_due=False)
        assert key == "success"
        assert "✅" in msg

    def test_rollback_terminal(self):
        msg, key = render_progress(_snap(backup_ref="b", finalize_status="rollback"),
                                   "finalizing", heartbeat_due=False)
        assert key == "rollback"
        assert "❌" in msg

    def test_terminal_keys_reported(self):
        from hermes_cli.upstream_sync_progress import is_terminal
        assert is_terminal("success") is True
        assert is_terminal("rollback") is True
        assert is_terminal("rebasing") is False
