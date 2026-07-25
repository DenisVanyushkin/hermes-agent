"""Smart approval failures must be visible in logs."""
import logging
from unittest.mock import patch as mock_patch

from tools.approval import _smart_approve


def test_llm_failure_logs_warning(caplog):
    caplog.set_level(logging.WARNING, logger="tools.approval")
    with mock_patch("agent.auxiliary_client.call_llm", side_effect=RuntimeError("401")):
        verdict = _smart_approve("rm -rf /tmp/x", "recursive delete")
    assert verdict == "escalate"
    assert any("smart approval" in r.message.lower() for r in caplog.records)
