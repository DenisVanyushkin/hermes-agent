"""No-agent weekly shortlist builder and publisher with a frozen retry source."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Callable
from zoneinfo import ZoneInfo

from job_intel_shortlist_build import (
    DEFAULT_DB, DEFAULT_OUT, _head_commit, build_weekly, connect_read_only,
    digest, load_company_blacklist, render_text, role_fit_evaluation_inputs,
)
from job_intel_shortlist_cooldown import sample_cooldown_keys
from job_intel_shortlist_empty import publish_empty_release
from job_intel_shortlist_issued import issued_keys
from job_intel_shortlist_publish import _source, publish_release
from job_intel_shortlist_summaries import enrich_summaries, live_summarizer, DEFAULT_MODEL


DEFAULT_RELEASE_ROOT = Path.home() / ".hermes" / "job_intel" / "shortlist-releases"
DEFAULT_REPO = Path.home() / ".hermes" / "hermes-agent"
DEFAULT_CHANNEL = "C0B4MM6D52A"
MAX_SHADOW_AGE = timedelta(hours=24)


def _previous_week(now: datetime) -> tuple[str, Any]:
    if now.tzinfo is None:
        raise ValueError("weekly tick needs a timezone-aware timestamp")
    today = now.astimezone(ZoneInfo("Europe/Berlin")).date()
    week_start = today - timedelta(days=today.weekday() + 7)
    iso = week_start.isocalendar()
    return f"shortlist-{iso.year}-W{iso.week:02d}", week_start


def _freeze_source(source_dir: Path, artifact: dict[str, Any]) -> None:
    source_root = source_dir.parent
    staging = source_root / f".{source_dir.name}.{os.getpid()}.preparing"
    if staging.exists():
        if staging.is_symlink() or not staging.is_dir():
            raise ValueError("unsafe weekly source staging path")
        shutil.rmtree(staging)
    staging.mkdir()
    sha = digest(artifact)
    for name, payload in (
        ("shortlist.json", json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n"),
        ("shortlist.txt", render_text(artifact, sha) + "\n"),
        ("SHA256SUMS", f"{sha}  canonical\n"),
    ):
        with (staging / name).open("x", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    os.replace(staging, source_dir)
    fd = os.open(source_root, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _require_fresh_shadow_collection(connection: Any, now: datetime) -> None:
    row = connection.execute(
        """SELECT id, finished_at FROM runs
           WHERE mode = 'daily' AND run_type = 'shadow' AND status = 'ok'
           ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    finished = None
    if row and row[1]:
        try:
            finished = datetime.fromisoformat(row[1])
        except ValueError:
            pass
    if (finished is None or finished.tzinfo is None
            or not timedelta(0) <= now.astimezone(timezone.utc) - finished.astimezone(timezone.utc) <= MAX_SHADOW_AGE):
        raise ValueError("shadow collection is stale: no successful daily shadow run in the last 24 hours")
    if not connection.execute(
        "SELECT EXISTS(SELECT 1 FROM vacancy_observability WHERE run_id = ?)", (row[0],)
    ).fetchone()[0]:
        raise ValueError("shadow collection is stale: latest successful daily shadow run has no observations")


def _send_stale_alert(message: str, *, command: list[str] | None = None) -> None:
    sender = command or [
        str(DEFAULT_REPO / "venv" / "bin" / "python"),
        "-m", "hermes_cli.main", "send", "--to", "telegram",
    ]
    result = subprocess.run([*sender, message], capture_output=True, text=True, timeout=45, check=False)
    if result.returncode:
        raise RuntimeError(f"stale shortlist alert command failed with exit {result.returncode}")


def run_weekly(db_path: Path, source_root: Path, release_root: Path,
               *, now: datetime | None = None, deliver: bool = False,
               client: Any = None, channel: str = DEFAULT_CHANNEL,
               repo: Path = DEFAULT_REPO, commit: str | None = None,
               summarizer: Callable[[str, str], str] | None = None,
               summary_model: str = "",
               stale_alert: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Freeze the preceding Berlin week once; retry publishes its same bytes."""
    now = now or datetime.now(timezone.utc)
    release_id, week_start = _previous_week(now)
    source_root.mkdir(parents=True, exist_ok=True)
    with (source_root / ".weekly.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            issued = issued_keys(release_root)
            source_dir = source_root / release_id
            if source_dir.exists():
                if source_dir.is_symlink() or not source_dir.is_dir():
                    raise ValueError("unsafe frozen weekly source path")
                artifact, sha = _source_or_empty(source_dir)
                if artifact.get("release_id") != release_id or artifact.get("week_start") != week_start.isoformat():
                    raise ValueError("frozen weekly source identity changed")
            else:
                cooled = sample_cooldown_keys(release_root, week_start)
                connection = None
                try:
                    try:
                        connection = connect_read_only(db_path)
                        _require_fresh_shadow_collection(connection, now)
                    except Exception as error:  # noqa: BLE001 - surface freshness failures to the operator
                        if stale_alert is not None:
                            detail = str(error) if isinstance(error, ValueError) else (
                                f"shadow collection freshness check failed ({type(error).__name__})"
                            )
                            message = (
                                f"⚠️ Job Intel {release_id} не опубликован: {detail}. "
                                "После восстановления сбора повторно запустите "
                                "job-intel-shortlist-weekly для этой недели."
                            )
                            try:
                                stale_alert(message)
                            except Exception as alert_error:  # noqa: BLE001 - preserve the stale refusal
                                print(f"weekly stale alert failed: {alert_error}", file=sys.stderr)
                        raise
                    artifact = build_weekly(
                        connection, week_start, commit or _head_commit(repo),
                        release_id=release_id, issued_keys=issued,
                        sample_cooldown_keys=cooled,
                        company_blacklist=load_company_blacklist(db_path, repo),
                        evaluation_inputs=role_fit_evaluation_inputs(repo), as_of=now,
                        ruleset_path=repo / "job_intel" / "product_search" / "role_fit.py",
                    )
                finally:
                    if connection is not None:
                        connection.close()
                artifact = enrich_summaries(artifact, summarizer, model_id=summary_model)
                _freeze_source(source_dir, artifact)
                sha = digest(artifact)
            result: dict[str, Any] = {"release_id": release_id, "source_sha256": sha,
                                      "delivered_count": artifact["delivered_count"],
                                      "sample_count": len(artifact["rejected_sample"])}
            if deliver:
                if client is None:
                    raise ValueError("Slack client required for delivery")
                if artifact["items"] or artifact["rejected_sample"]:
                    receipt = publish_release(source_dir, release_root, channel, client, now=now)
                else:
                    receipt = publish_empty_release(source_dir, release_root, channel, client, now=now)
                result["state"] = receipt["state"]
            else:
                result["state"] = "delivery_disabled"
            return result
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _source_or_empty(source_dir: Path) -> tuple[dict[str, Any], str]:
    from job_intel_shortlist_empty import _source_empty

    try:
        artifact = json.loads((source_dir / "shortlist.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("cannot read frozen weekly source") from error
    if artifact.get("items") or artifact.get("rejected_sample"):
        return _source(source_dir)
    return _source_empty(source_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--release-root", type=Path, default=DEFAULT_RELEASE_ROOT)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--channel", default=DEFAULT_CHANNEL)
    args = parser.parse_args()
    deliver = os.getenv("JOB_INTEL_SHORTLIST_DELIVERY_DISABLED") == "0"
    client = None
    summarizer = None
    if deliver:
        from slack_sdk import WebClient
        from job_intel_shortlist_send import load_token

        client = WebClient(token=load_token())
        if os.getenv("JOB_INTEL_SHORTLIST_SUMMARIES_ENABLED") == "1":
            summarizer = live_summarizer()
    result = run_weekly(args.db, args.source_root, args.release_root, client=client,
                        deliver=deliver, channel=args.channel, repo=args.repo,
                        summarizer=summarizer,
                        summary_model=DEFAULT_MODEL if summarizer is not None else "",
                        stale_alert=_send_stale_alert if deliver else None)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if deliver else 2


if __name__ == "__main__":
    raise SystemExit(main())
