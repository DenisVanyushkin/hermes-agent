import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from job_intel_shortlist_empty import publish_empty_release
from job_intel_shortlist_issued import issued_keys


class Slack:
    def __init__(self):
        self.messages = []
        self.posts = 0
        self.crash = False

    def auth_test(self):
        return {"ok": True, "user_id": "UBOT"}

    def conversations_history(self, **kwargs):
        return {"ok": True, "messages": self.messages, "response_metadata": {"next_cursor": ""}}

    def chat_postMessage(self, **kwargs):
        self.posts += 1
        message = {"ts": "1790161201.000001", "user": "UBOT", "text": kwargs["text"]}
        self.messages.append(message)
        if self.crash:
            self.crash = False
            raise RuntimeError("crash after Slack write")
        return {"ok": True, "ts": message["ts"]}


def _artifact(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    artifact = {
        "artifact": "job_intel_weekly_shortlist", "version": "v2",
        "release_id": "shortlist-2026-W39", "items": [], "rejected_sample": [],
        "census_count": 11, "census_sha256": "census", "run_ids": [1, 2],
        "suppressed_counts": {"already_issued": 4}, "excluded_counts": {"reject_not_sampled": 7},
    }
    (source / "shortlist.json").write_text(json.dumps(artifact))
    payload = json.dumps(artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    (source / "SHA256SUMS").write_text(f"{hashlib.sha256(payload).hexdigest()}  canonical\n")
    return source


def test_empty_week_is_reported_once_and_does_not_issue_keys(tmp_path):
    source = _artifact(tmp_path)
    root = tmp_path / "releases"
    slack = Slack()
    slack.crash = True
    with pytest.raises(RuntimeError, match="crash"):
        publish_empty_release(source, root, "COWNER", slack,
                              now=datetime(2026, 9, 23, tzinfo=timezone.utc))
    receipt = publish_empty_release(source, root, "COWNER", slack,
                                    now=datetime(2026, 9, 23, tzinfo=timezone.utc))
    assert receipt["state"] == "reported"
    assert receipt["empty"] is True
    assert slack.posts == 1
    assert "Новых ролей нет" in slack.messages[0]["text"]
    assert issued_keys(root, require_seed=False) == frozenset()
