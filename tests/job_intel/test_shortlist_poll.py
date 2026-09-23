"""The first valid owner reply is final, including across process crashes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys

from openpyxl import load_workbook
import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import job_intel_shortlist_poll as poll  # noqa: E402
import job_intel_shortlist_publish as publish  # noqa: E402


NOW = datetime(2026, 9, 23, 11, tzinfo=timezone.utc)
RELEASE = "shortlist-2026-W39"
OWNER = "UOWNER"


def setup(tmp_path: Path) -> tuple[Path, Path, Path, Path, dict]:
    source = tmp_path / "source"
    source.mkdir(parents=True)
    artifact = {
        "artifact": "job_intel_weekly_shortlist", "release_id": RELEASE,
        "built_at": NOW.isoformat(), "census_sha256": "abc", "run_ids": [1],
        "commit": "abc", "ruleset_versions": ["rf1-test"],
        "items": [{"vacancy_key": "k1", "company": "Example", "title": "CPO",
                   "location": "Berlin", "source": "ATS", "url": "https://example.org/k1",
                   "role_fit_verdict": "accept", "rule_ids": ["scope"],
                   "ruleset_version": "rf1-test", "run_id": 1,
                   "description": "Lead product and own roadmap."}],
        "rejected_sample": [],
    }
    (source / "shortlist.json").write_text(json.dumps(artifact), encoding="utf-8")
    canonical = json.dumps({k: v for k, v in artifact.items() if k != "built_at"},
                           ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    (source / "SHA256SUMS").write_text(f"{hashlib.sha256(canonical).hexdigest()}  canonical\n")
    releases = tmp_path / "releases"
    receipt = publish.prepare_release(source, releases, "COWNER", now=NOW)
    attempt = releases / RELEASE / "attempt-001"
    receipt.update(state="delivered", thread_ts="1790161200.000001", bot_file_id="FBOT",
                   delivered_at=NOW.isoformat())
    publish._atomic_json(attempt / "receipt.json", receipt)
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({"labels": [], "rounds": [], "label_history": []}))
    return source, releases, attempt, labels, artifact


def answer(attempt: Path, tmp_path: Path, *, decision: str = "yes", mutate: bool = False) -> bytes:
    path = tmp_path / f"answer-{len(list(tmp_path.glob('answer-*')))}.xlsx"
    path.write_bytes((attempt / "review.xlsx").read_bytes())
    book = load_workbook(path)
    book["shortlist"]["O2"] = decision
    if mutate:
        book["shortlist"]["A2"] = "foreign-key"
    book.save(path)
    return path.read_bytes()


class Slack:
    def __init__(self, replies: list[dict]) -> None:
        self.replies = replies
        self.calls = 0
        self.error = False

    def conversations_replies(self, **kwargs: object) -> dict:
        self.calls += 1
        if self.error:
            raise RuntimeError("Slack unavailable")
        start = int(kwargs.get("cursor") or 0)
        page = self.replies[start:start + 1]
        cursor = str(start + 1) if start + 1 < len(self.replies) else ""
        return {"ok": True, "messages": page, "response_metadata": {"next_cursor": cursor}}


def message(ts: str, file_id: str, *, user: str = OWNER) -> dict:
    return {"ts": ts, "user": user, "files": [{"id": file_id, "name": "review.xlsx"}]}


def run(source: Path, releases: Path, labels: Path, slack: Slack,
        files: dict[str, bytes], *, now: datetime = NOW) -> dict:
    return poll.poll_release(source, releases, RELEASE, labels, OWNER, slack,
                             fetch_file=lambda _client, file_id: files[file_id], now=now)


def test_first_valid_owner_file_after_invalid_and_foreign_replies(tmp_path: Path) -> None:
    source, releases, attempt, labels, _ = setup(tmp_path)
    good = answer(attempt, tmp_path)
    bad = answer(attempt, tmp_path, mutate=True)
    slack = Slack([
        message("1790161200.000001", "FBOT", user="UBOT"),
        message("1790161201.000001", "FFOREIGN", user="UOTHER"),
        message("1790161202.000001", "FBAD"),
        message("1790161203.000001", "FGOOD"),
        message("1790161204.000001", "FLATER"),
    ])
    receipt = run(source, releases, labels, slack,
                  {"FFOREIGN": good, "FBAD": bad, "FGOOD": good, "FLATER": good})
    assert receipt["state"] == "imported"
    assert receipt["owner_file_id"] == "FGOOD"
    assert receipt["response_uid"] == OWNER
    assert receipt["label_delta"]["history_added"] == 1
    assert slack.calls == 5
    assert json.loads(labels.read_text())["labels"][0]["verdict"] == "yes"
    before = (attempt / "receipt.json").read_bytes(), labels.read_bytes()
    assert run(source, releases, labels, Slack([]), {}) == receipt
    assert ((attempt / "receipt.json").read_bytes(), labels.read_bytes()) == before


def test_predeadline_reply_seen_after_deadline_imports_then_no_reply_expires(tmp_path: Path) -> None:
    source, releases, attempt, labels, _ = setup(tmp_path)
    good = answer(attempt, tmp_path)
    deadline = NOW + timedelta(days=21)
    slack = Slack([message(str(deadline.timestamp() - 1), "FGOOD")])
    receipt = run(source, releases, labels, slack, {"FGOOD": good}, now=deadline + timedelta(days=1))
    assert receipt["state"] == "imported"

    source2, releases2, attempt2, labels2, _ = setup(tmp_path / "empty")
    quiet = Slack([message(str(deadline.timestamp() + 1), "FLATE")])
    receipt2 = run(source2, releases2, labels2, quiet, {"FLATE": good}, now=deadline + timedelta(days=1))
    assert receipt2["state"] == "expired"
    assert quiet.calls == 1
    assert json.loads(labels2.read_text())["label_history"] == []


def test_crash_after_label_write_replays_intent_without_duplicate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, releases, attempt, labels, _ = setup(tmp_path)
    good = answer(attempt, tmp_path)
    slack = Slack([message("1790161203.000001", "FGOOD")])
    original = poll._atomic_json
    crashed = False

    def crash_on_import(path: Path, value: dict) -> None:
        nonlocal crashed
        if value.get("state") == "imported" and not crashed:
            crashed = True
            raise RuntimeError("crash after label write")
        original(path, value)

    monkeypatch.setattr(poll, "_atomic_json", crash_on_import)
    with pytest.raises(RuntimeError, match="crash after label write"):
        run(source, releases, labels, slack, {"FGOOD": good})
    assert publish.load_receipt(attempt)["import_intent"]["file_id"] == "FGOOD"
    before = labels.read_bytes()
    receipt = run(source, releases, labels, Slack([]), {})
    assert receipt["state"] == "imported"
    assert labels.read_bytes() == before
    assert len(json.loads(labels.read_text())["label_history"]) == 1


def test_slack_error_does_not_expire_or_mutate_labels(tmp_path: Path) -> None:
    source, releases, attempt, labels, _ = setup(tmp_path)
    slack = Slack([])
    slack.error = True
    before = (attempt / "receipt.json").read_bytes(), labels.read_bytes()
    with pytest.raises(RuntimeError, match="Slack unavailable"):
        run(source, releases, labels, slack, {}, now=NOW + timedelta(days=22))
    assert ((attempt / "receipt.json").read_bytes(), labels.read_bytes()) == before


def test_same_timestamp_uses_file_id_and_records_invalid_file(tmp_path: Path) -> None:
    source, releases, attempt, labels, _ = setup(tmp_path)
    good = answer(attempt, tmp_path)
    bad = answer(attempt, tmp_path, mutate=True)
    slack = Slack([message("1790161203.000001", "FB"), message("1790161203.000001", "FA")])
    receipt = run(source, releases, labels, slack, {"FA": bad, "FB": good})
    assert receipt["owner_file_id"] == "FB"
    assert receipt["rejected_files"][0]["file_id"] == "FA"
    assert receipt["decision_count"] == 1


def test_invalid_owner_file_is_durably_rejected_without_early_expiry(tmp_path: Path) -> None:
    source, releases, attempt, labels, _ = setup(tmp_path)
    bad = answer(attempt, tmp_path, mutate=True)
    receipt = run(source, releases, labels, Slack([message("1790161203.000001", "FBAD")]),
                  {"FBAD": bad})
    assert receipt["state"] == "delivered"
    assert receipt["rejected_files"][0]["file_id"] == "FBAD"
    assert publish.load_receipt(attempt)["rejected_files"] == receipt["rejected_files"]
    assert json.loads(labels.read_text())["label_history"] == []


def test_operator_uid_can_be_read_from_hermes_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    (home / ".hermes" / ".env").write_text('HERMES_OPERATOR_SLACK_UID="UOWNER"\n')
    monkeypatch.delenv("HERMES_OPERATOR_SLACK_UID", raising=False)
    monkeypatch.setattr(poll.Path, "home", lambda: home)
    assert poll.load_operator_uid() == OWNER


@pytest.mark.parametrize("permanent", [
    {"ok": False, "error": "file_not_found"},
    {"ok": False, "error": "file_deleted"},
    {"ok": True, "file": {"id": "FX", "size": 200 * 1024 * 1024,
                           "filetype": "xlsx", "url_private_download": "https://files.slack.com/x"}},
    {"ok": True, "file": {"id": "FX", "size": 10, "filetype": "xlsx",
                           "url_private_download": "https://docs.google.com/x"}},
    {"ok": True, "file": {"id": "FX", "size": 10, "filetype": "mp4",
                           "url_private_download": "https://files.slack.com/x"}},
])
def test_permanent_file_rejection_does_not_block_later_answer(tmp_path: Path,
                                                                permanent: dict) -> None:
    source, releases, attempt, labels, _ = setup(tmp_path)
    good = answer(attempt, tmp_path)

    class FilesSlack(Slack):
        def files_info(self, *, file: str) -> dict:
            return permanent if file == "FX" else {
                "ok": True, "file": {"id": file, "size": len(good), "filetype": "xlsx",
                                      "url_private_download": "https://files.slack.com/good"},
            }

    slack = FilesSlack([message("1790161202.000001", "FX"),
                        message("1790161203.000001", "FGOOD")])

    def fetch(client: FilesSlack, file_id: str) -> bytes:
        if file_id == "FX":
            return poll.download_slack_file(client, file_id)
        return good

    receipt = poll.poll_release(source, releases, RELEASE, labels, OWNER, slack,
                                fetch_file=fetch, now=NOW)
    assert receipt["state"] == "imported"
    assert receipt["owner_file_id"] == "FGOOD"
    assert receipt["rejected_files"][0]["file_id"] == "FX"


def test_transient_file_error_keeps_earlier_answer_pending(tmp_path: Path) -> None:
    source, releases, attempt, labels, _ = setup(tmp_path)
    good = answer(attempt, tmp_path)
    slack = Slack([message("1790161202.000001", "FX"),
                   message("1790161203.000001", "FGOOD")])

    def fetch(_client: Slack, file_id: str) -> bytes:
        if file_id == "FX":
            raise RuntimeError("files.info temporarily unavailable")
        return good

    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        poll.poll_release(source, releases, RELEASE, labels, OWNER, slack,
                          fetch_file=fetch, now=NOW)
    assert publish.load_receipt(attempt)["state"] == "delivered"
    assert json.loads(labels.read_text())["label_history"] == []


def test_sdk_file_deleted_exception_is_permanent_rejection(tmp_path: Path) -> None:
    source, releases, attempt, labels, _ = setup(tmp_path)
    good = answer(attempt, tmp_path)

    class SlackError(Exception):
        response = {"ok": False, "error": "file_deleted"}

    class FilesSlack(Slack):
        def files_info(self, *, file: str) -> dict:
            raise SlackError(file)

    slack = FilesSlack([message("1790161202.000001", "FX"),
                        message("1790161203.000001", "FGOOD")])

    def fetch(client: FilesSlack, file_id: str) -> bytes:
        return poll.download_slack_file(client, file_id) if file_id == "FX" else good

    receipt = poll.poll_release(source, releases, RELEASE, labels, OWNER, slack,
                                fetch_file=fetch, now=NOW)
    assert receipt["owner_file_id"] == "FGOOD"
    assert receipt["rejected_files"][0]["reason"] == "file_deleted"


def test_empty_decision_workbook_is_valid_final_partial_answer(tmp_path: Path) -> None:
    source, releases, attempt, labels, _ = setup(tmp_path)
    empty = (attempt / "review.xlsx").read_bytes()
    filled = answer(attempt, tmp_path)
    slack = Slack([message("1790161202.000001", "FEMPTY"),
                   message("1790161203.000001", "FFILLED")])
    receipt = run(source, releases, labels, slack, {"FEMPTY": empty, "FFILLED": filled})
    assert receipt["state"] == "imported"
    assert receipt["owner_file_id"] == "FEMPTY"
    assert receipt["decision_count"] == 0
    assert json.loads(labels.read_text())["label_history"] == []
