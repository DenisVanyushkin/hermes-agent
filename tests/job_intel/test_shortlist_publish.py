"""Crash boundaries of the weekly Slack publisher are observable and recoverable."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import time
from threading import Event
import sys

import pytest


MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "job_intel_shortlist_publish.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("job_intel_shortlist_publish", MODULE_PATH)
assert spec and spec.loader
publish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publish)


class FakeSlack:
    def __init__(self) -> None:
        self.anchors: list[dict] = []
        self.replies: dict[str, list[dict]] = {}
        self.anchor_posts = 0
        self.file_uploads = 0
        self.crash_after_anchor = False
        self.crash_after_file = False
        self.scan_error = False

    def auth_test(self) -> dict:
        return {"ok": True, "user_id": "UBOT"}

    def conversations_history(self, **kwargs) -> dict:
        if self.scan_error:
            raise RuntimeError("history API unavailable")
        visible = [message for message in self.anchors
                   if float(message["ts"]) >= float(kwargs.get("oldest") or 0)
                   and (not kwargs.get("latest") or float(message["ts"]) <= float(kwargs["latest"]))]
        cursor = int(kwargs.get("cursor") or 0)
        messages = visible[cursor:cursor + 1]
        next_cursor = str(cursor + 1) if cursor + 1 < len(visible) else ""
        return {"ok": True, "messages": messages, "response_metadata": {"next_cursor": next_cursor}}

    def conversations_replies(self, **kwargs) -> dict:
        if self.scan_error:
            raise RuntimeError("replies API unavailable")
        messages = self.replies.get(kwargs["ts"], [])
        cursor = int(kwargs.get("cursor") or 0)
        page = messages[cursor:cursor + 1]
        next_cursor = str(cursor + 1) if cursor + 1 < len(messages) else ""
        return {"ok": True, "messages": page, "response_metadata": {"next_cursor": next_cursor}}

    def chat_postMessage(self, *, channel: str, text: str) -> dict:
        self.anchor_posts += 1
        ts = f"1790161201.{self.anchor_posts:06d}"
        self.anchors.append({"ts": ts, "text": text, "user": "UBOT"})
        self.replies[ts] = [{"ts": ts, "text": text, "user": "UBOT"}]
        if self.crash_after_anchor:
            self.crash_after_anchor = False
            raise RuntimeError("process died after Slack accepted anchor")
        return {"ok": True, "ts": ts}

    def files_upload_v2(self, *, channel: str, thread_ts: str, file: str, filename: str,
                        title: str, initial_comment: str) -> dict:
        self.file_uploads += 1
        file_id = f"F{self.file_uploads}"
        self.replies[thread_ts].append({
            "ts": f"1790161202.{self.file_uploads:06d}", "text": initial_comment,
            "user": "UBOT", "files": [{"id": file_id, "name": filename, "title": title}],
        })
        if self.crash_after_file:
            self.crash_after_file = False
            raise RuntimeError("process died after Slack accepted file")
        return {"ok": True, "file": {"id": file_id}}


def artifact_dir(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    item = {
        "vacancy_key": "key-1", "company": "Example", "title": "VP Product",
        "location": "Remote", "source": "ATS", "url": "https://example.org/job/1",
        "role_fit_verdict": "accept", "rule_ids": ["seniority"],
        "ruleset_version": "rf1-test", "run_id": 1,
        "description": "Own product strategy, roadmap and P&L.",
    }
    artifact = {
        "artifact": "job_intel_weekly_shortlist", "version": "v2",
        "release_id": "shortlist-2026-W39", "week_start": "2026-09-21",
        "built_at": "2026-09-23T10:00:00Z", "census_sha256": "abc",
        "run_ids": [1], "commit": "abc123", "ruleset_versions": ["rf1-test"],
        "items": [item], "rejected_sample": [],
    }
    (source / "shortlist.json").write_text(json.dumps(artifact), encoding="utf-8")
    canonical = json.dumps({key: value for key, value in artifact.items() if key != "built_at"},
                           ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    (source / "SHA256SUMS").write_text(f"{hashlib.sha256(canonical).hexdigest()}  canonical\n")
    return source


def run(tmp_path: Path, slack: FakeSlack, source: Path) -> dict:
    return publish.publish_release(source, tmp_path / "releases", "COWNER", slack,
                                   now=datetime(2026, 9, 23, 11, tzinfo=timezone.utc))


def test_anchor_crash_adopts_existing_typed_anchor(tmp_path: Path) -> None:
    source = artifact_dir(tmp_path)
    slack = FakeSlack()
    slack.crash_after_anchor = True
    with pytest.raises(RuntimeError, match="process died"):
        run(tmp_path, slack, source)
    assert publish.load_receipt(tmp_path / "releases" / "shortlist-2026-W39" / "attempt-001")["state"] == "prepared"
    receipt = run(tmp_path, slack, source)
    assert receipt["state"] == "delivered"
    assert slack.anchor_posts == 1
    assert slack.file_uploads == 1


def test_file_crash_adopts_existing_bot_file(tmp_path: Path) -> None:
    source = artifact_dir(tmp_path)
    slack = FakeSlack()
    slack.crash_after_file = True
    with pytest.raises(RuntimeError, match="process died"):
        run(tmp_path, slack, source)
    assert publish.load_receipt(tmp_path / "releases" / "shortlist-2026-W39" / "attempt-001")["state"] == "anchored"
    receipt = run(tmp_path, slack, source)
    assert receipt["bot_file_id"] == "F1"
    assert slack.anchor_posts == slack.file_uploads == 1
    assert run(tmp_path, slack, source) == receipt


def test_ambiguous_anchor_and_failed_scan_void_before_new_post(tmp_path: Path) -> None:
    source = artifact_dir(tmp_path)
    slack = FakeSlack()
    prepared = publish.prepare_release(source, tmp_path / "releases", "COWNER",
                                       now=datetime(2026, 9, 23, 11, tzinfo=timezone.utc))
    marker = publish.anchor_marker(prepared)
    slack.anchors = [
        {"ts": "1790161201.000001", "text": marker, "user": "UBOT"},
        {"ts": "1790161201.000002", "text": marker, "user": "UBOT"},
    ]
    with pytest.raises(publish.PublishError, match="ambiguous"):
        run(tmp_path, slack, source)
    assert publish.load_receipt(tmp_path / "releases" / "shortlist-2026-W39" / "attempt-001")["state"] == "void"
    assert slack.anchor_posts == 0

    other = tmp_path / "other"
    other.mkdir()
    source2 = artifact_dir(other)
    slack2 = FakeSlack()
    slack2.scan_error = True
    with pytest.raises(publish.PublishError, match="scan"):
        run(other, slack2, source2)
    assert publish.load_receipt(other / "releases" / "shortlist-2026-W39" / "attempt-001")["state"] == "void"
    assert slack2.anchor_posts == 0


def test_delivered_receipt_binds_manifest_and_workbook(tmp_path: Path) -> None:
    receipt = run(tmp_path, FakeSlack(), artifact_dir(tmp_path))
    directory = tmp_path / "releases" / "shortlist-2026-W39" / "attempt-001"
    assert hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest() == receipt["manifest_sha256"]
    assert hashlib.sha256((directory / "review.xlsx").read_bytes()).hexdigest() == receipt["workbook_sha256"]
    assert receipt["projection_sha256"]
    assert receipt["rows"] == 1
    from sys import path as sys_path
    sys_path.insert(0, str(MODULE_PATH.parent))
    from job_intel_shortlist_issued import issued_keys
    assert issued_keys(tmp_path / "releases", require_seed=False) == frozenset({"key-1"})


def test_rejected_sample_only_still_gets_review_workbook(tmp_path: Path) -> None:
    source = artifact_dir(tmp_path)
    artifact = json.loads((source / "shortlist.json").read_text())
    sample = {**artifact["items"][0], "role_fit_verdict": "reject"}
    artifact["items"] = []
    artifact["rejected_sample"] = [sample]
    (source / "shortlist.json").write_text(json.dumps(artifact))
    canonical = json.dumps({key: value for key, value in artifact.items() if key != "built_at"},
                           ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    (source / "SHA256SUMS").write_text(f"{hashlib.sha256(canonical).hexdigest()}  canonical\n")
    receipt = run(tmp_path, FakeSlack(), source)
    assert receipt["state"] == "delivered"
    assert receipt["rows"] == 0 and receipt["rejected_sample_rows"] == 1


def test_crash_before_prepared_receipt_recovers_frozen_files(tmp_path: Path) -> None:
    source = artifact_dir(tmp_path)
    root = tmp_path / "releases"
    prepared = publish.prepare_release(source, root, "COWNER")
    attempt = root / "shortlist-2026-W39" / "attempt-001"
    (attempt / "receipt.json").unlink()
    receipt = run(tmp_path, FakeSlack(), source)
    assert receipt["state"] == "delivered"
    assert receipt["manifest_sha256"] == prepared["manifest_sha256"]
    assert receipt["workbook_sha256"] == prepared["workbook_sha256"]


def test_ambiguous_bot_files_void_anchored_attempt(tmp_path: Path) -> None:
    source = artifact_dir(tmp_path)
    slack = FakeSlack()
    slack.crash_after_file = True
    with pytest.raises(RuntimeError):
        run(tmp_path, slack, source)
    thread_ts = slack.anchors[0]["ts"]
    duplicate = dict(slack.replies[thread_ts][-1])
    duplicate["files"] = [{**duplicate["files"][0], "id": "FOTHER"}]
    slack.replies[thread_ts].append(duplicate)
    with pytest.raises(publish.PublishError, match="ambiguous Slack bot files"):
        run(tmp_path, slack, source)
    assert publish.load_receipt(tmp_path / "releases" / "shortlist-2026-W39" / "attempt-001")["state"] == "void"
    assert slack.file_uploads == 1


def test_frozen_attempt_rejects_changed_source_artifact(tmp_path: Path) -> None:
    source = artifact_dir(tmp_path)
    root = tmp_path / "releases"
    publish.prepare_release(source, root, "COWNER")
    artifact = json.loads((source / "shortlist.json").read_text())
    artifact["items"][0]["description"] = "different vacancy text"
    (source / "shortlist.json").write_text(json.dumps(artifact))
    canonical = json.dumps({key: value for key, value in artifact.items() if key != "built_at"},
                           ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    (source / "SHA256SUMS").write_text(f"{hashlib.sha256(canonical).hexdigest()}  canonical\n")
    with pytest.raises(publish.PublishError, match="source_artifact_sha256 mismatch"):
        run(tmp_path, FakeSlack(), source)


def test_concurrent_publisher_calls_share_one_release_lock(tmp_path: Path) -> None:
    source = artifact_dir(tmp_path)

    class SlowSlack(FakeSlack):
        def conversations_history(self, **kwargs) -> dict:
            time.sleep(0.05)
            return super().conversations_history(**kwargs)

    slack = SlowSlack()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(tmp_path, slack, source), range(2)))
    assert results[0] == results[1]
    assert slack.anchor_posts == slack.file_uploads == 1


def test_waiting_publisher_sees_anchor_posted_after_it_started(tmp_path: Path) -> None:
    source = artifact_dir(tmp_path)
    first_post = Event()
    second_started = Event()

    class CoordinatedSlack(FakeSlack):
        def chat_postMessage(self, *, channel: str, text: str) -> dict:
            first_post.set()
            assert second_started.wait(timeout=5)
            return super().chat_postMessage(channel=channel, text=text)

    slack = CoordinatedSlack()
    slack.crash_after_anchor = True
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run, tmp_path, slack, source)
        assert first_post.wait(timeout=5)

        def queued_retry() -> dict:
            second_started.set()
            return run(tmp_path, slack, source)

        second = pool.submit(queued_retry)
        with pytest.raises(RuntimeError, match="process died"):
            first.result()
        receipt = second.result()
    assert receipt["state"] == "delivered"
    assert slack.anchor_posts == slack.file_uploads == 1


def test_zero_selected_roles_do_not_send_a_review_workbook(tmp_path: Path) -> None:
    source = artifact_dir(tmp_path)
    artifact = json.loads((source / "shortlist.json").read_text())
    artifact["items"] = []
    (source / "shortlist.json").write_text(json.dumps(artifact))
    canonical = json.dumps({key: value for key, value in artifact.items() if key != "built_at"},
                           ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    (source / "SHA256SUMS").write_text(f"{hashlib.sha256(canonical).hexdigest()}  canonical\n")
    slack = FakeSlack()
    with pytest.raises(publish.PublishError, match="empty weekly release"):
        run(tmp_path, slack, source)
    assert slack.anchor_posts == slack.file_uploads == 0


def test_incomplete_preparation_before_receipt_can_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = artifact_dir(tmp_path)
    original = publish.write_workbook

    def interrupted(path: Path, artifact: dict, attempt_id: str) -> str:
        path.write_bytes(b"partial XLSX")
        raise RuntimeError("process died during XLSX save")

    monkeypatch.setattr(publish, "write_workbook", interrupted)
    with pytest.raises(RuntimeError, match="process died"):
        run(tmp_path, FakeSlack(), source)
    release_dir = tmp_path / "releases" / "shortlist-2026-W39"
    assert not (release_dir / "attempt-001").exists()
    monkeypatch.setattr(publish, "write_workbook", original)
    receipt = run(tmp_path, FakeSlack(), source)
    assert receipt["state"] == "delivered"
    assert not list(release_dir.glob(".attempt-001.*.preparing"))
