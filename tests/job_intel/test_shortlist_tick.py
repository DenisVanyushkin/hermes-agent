import json
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import job_intel_shortlist_tick as tick


def _receipt(root: Path, release_id: str, state: str) -> None:
    attempt = root / release_id / "attempt-001"
    attempt.mkdir(parents=True)
    (attempt / "receipt.json").write_text(json.dumps({
        "release_id": release_id, "attempt_id": "attempt-001", "state": state,
    }))


def test_tick_polls_then_reports_imported_release(monkeypatch, tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    source = tmp_path / "source"
    labels = tmp_path / "labels.json"
    _receipt(releases, "shortlist-2026-W38", "delivered")
    _receipt(releases, "shortlist-2026-W39", "reported")
    _receipt(releases, "shortlist-20260922T084423Z", "delivered")
    calls = []

    def poll(source_dir, release_root, release_id, labels_path, uid, client, *, now):
        calls.append(("poll", release_id, uid))
        return {"release_id": release_id, "state": "imported"}

    def report(source_dir, release_root, release_id, client, *, now, db_path):
        calls.append(("report", release_id))
        return {"release_id": release_id, "state": "reported"}

    monkeypatch.setattr(tick, "poll_release", poll)
    monkeypatch.setattr(tick, "publish_report", report)
    result = tick.tick_pending(source, releases, labels, "UOWNER", object(),
                               db_path=tmp_path / "db", now=datetime(2026, 9, 23, tzinfo=timezone.utc))
    assert calls == [("poll", "shortlist-2026-W38", "UOWNER"), ("report", "shortlist-2026-W38")]
    assert result == [{"release_id": "shortlist-2026-W38", "state": "reported"}]


def test_one_failed_release_does_not_skip_next(monkeypatch, tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    _receipt(releases, "shortlist-2026-W37", "delivered")
    _receipt(releases, "shortlist-2026-W38", "delivered")
    seen = []

    def poll(source_dir, release_root, release_id, labels_path, uid, client, *, now):
        seen.append(release_id)
        if release_id.endswith("W37"):
            raise RuntimeError("Slack temporarily down")
        return {"release_id": release_id, "state": "delivered"}

    monkeypatch.setattr(tick, "poll_release", poll)
    result = tick.tick_pending(tmp_path / "source", releases, tmp_path / "labels", "UOWNER", object(),
                               db_path=tmp_path / "db", now=datetime(2026, 9, 23, tzinfo=timezone.utc))
    assert seen == ["shortlist-2026-W37", "shortlist-2026-W38"]
    assert result[0]["error"] == "Slack temporarily down"
    assert result[1] == {"release_id": "shortlist-2026-W38", "state": "delivered"}


def test_tick_recovers_prepared_empty_release(monkeypatch, tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    release_id = "shortlist-2026-W38"
    _receipt(releases, release_id, "prepared")
    receipt_path = releases / release_id / "attempt-001" / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["empty"] = True
    receipt_path.write_text(json.dumps(receipt))
    calls = []

    def empty(source_dir, release_root, channel, client, *, now):
        calls.append((source_dir.name, channel))
        return {"release_id": release_id, "state": "reported", "empty": True}

    receipt["channel"] = "COWNER"
    receipt_path.write_text(json.dumps(receipt))
    monkeypatch.setattr(tick, "publish_empty_release", empty)
    assert tick.tick_pending(tmp_path / "source", releases, tmp_path / "labels", "UOWNER", object(),
                             db_path=tmp_path / "db", now=datetime(2026, 9, 23, tzinfo=timezone.utc)) == [
                                 {"release_id": release_id, "state": "reported"}]
    assert calls == [(release_id, "COWNER")]


def test_poll_and_report_cron_stages_have_separate_work(monkeypatch, tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    _receipt(releases, "shortlist-2026-W38", "delivered")
    _receipt(releases, "shortlist-2026-W39", "imported")
    calls = []

    def poll(_source, _root, release_id, _labels, _uid, _client, *, now):
        calls.append(("poll", release_id))
        return {"release_id": release_id, "state": "delivered"}

    def report(_source, _root, release_id, _client, *, now, db_path):
        calls.append(("report", release_id))
        return {"release_id": release_id, "state": "reported"}

    monkeypatch.setattr(tick, "poll_release", poll)
    monkeypatch.setattr(tick, "publish_report", report)
    args = (tmp_path / "source", releases, tmp_path / "labels", "UOWNER", object())
    now = datetime(2026, 9, 23, tzinfo=timezone.utc)
    tick.tick_pending(*args, stage="poll", now=now)
    assert calls == [("poll", "shortlist-2026-W38")]
    calls.clear()
    tick.tick_pending(*args, stage="report", now=now)
    assert calls == [("report", "shortlist-2026-W39")]
