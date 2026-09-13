#!/usr/bin/env python3
"""Alert when the LinkedIn source is repeatedly blocked or empty."""

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Callable, Sequence


BAD_STATUSES = frozenset({"blocked", "error"})
DEFAULT_SOURCE = "linkedin"
DEFAULT_THRESHOLD = 3
DEFAULT_CHANNEL = "executive_search_report"
DEFAULT_STATE_PATH = Path("/var/lib/job-intel/state/linkedin-source-alert.json")


@dataclass(frozen=True)
class SourceRun:
    run_id: int
    source_status: str
    found_count: int
    started_at: str

    @property
    def is_bad(self) -> bool:
        return self.source_status in BAD_STATUSES or self.found_count == 0


def _read_recent_runs(db_path: Path, source: str, limit: int) -> list[SourceRun]:
    resolved = db_path.expanduser().resolve()
    uri = f"file:{resolved.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        rows = connection.execute(
            """
            SELECT k.run_id,
                   COALESCE(k.source_status, ''),
                   COALESCE(k.found_count, 0),
                   COALESCE(r.started_at, '')
            FROM source_kpi_run AS k
            JOIN runs AS r ON r.id = k.run_id
            WHERE k.source = ?
              AND r.mode = 'daily'
              AND COALESCE(k.enabled, 1) = 1
              AND COALESCE(k.skip_reason, '') != 'disabled_by_config'
            ORDER BY k.run_id DESC
            LIMIT ?
            """,
            (source, limit),
        ).fetchall()
    finally:
        connection.close()
    return [
        SourceRun(
            run_id=int(run_id),
            source_status=str(source_status),
            found_count=int(found_count),
            started_at=str(started_at),
        )
        for run_id, source_status, found_count, started_at in rows
    ]


def _load_state(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"alert_sent": False}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read alert state {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError(f"alert state must be an object: {path}")
    return raw


def _write_state(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = json.dumps(state, sort_keys=True, indent=2) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fchmod(handle.fileno(), 0o600)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _consecutive_bad_runs(runs: Sequence[SourceRun]) -> int:
    count = 0
    for run in runs:
        if not run.is_bad:
            break
        count += 1
    return count


def scan_and_alert(
    *,
    db_path: Path,
    state_path: Path,
    threshold: int = DEFAULT_THRESHOLD,
    source: str = DEFAULT_SOURCE,
    channel: str = DEFAULT_CHANNEL,
    deliver: Callable[[str, str], object],
    message_prefix: str = "[job-intel]",
) -> dict[str, object]:
    if threshold < 1:
        raise ValueError("threshold must be positive")
    runs = _read_recent_runs(db_path, source, threshold + 1)
    if not runs:
        raise RuntimeError(f"no enabled daily KPI row found for source {source}")

    state = _load_state(state_path)
    alert_sent = bool(state.get("alert_sent", False))
    consecutive_bad = _consecutive_bad_runs(runs)
    sent_now = False
    newest = runs[0]
    if not newest.is_bad:
        alert_sent = False
    elif consecutive_bad >= threshold and not alert_sent:
        message = (
            f"{message_prefix} LinkedIn source health alert: "
            f"consecutive_bad_runs={consecutive_bad}, threshold={threshold}, "
            f"latest_run_id={newest.run_id}, latest_status={newest.source_status}, "
            f"latest_found_count={newest.found_count}."
        )
        result = deliver(message, channel)
        if not bool(getattr(result, "success", False)):
            error = getattr(result, "error", None) or "delivery failed"
            state.update(
                {
                    "alert_sent": False,
                    "last_run_id": newest.run_id,
                    "consecutive_bad_runs": consecutive_bad,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            _write_state(state_path, state)
            raise RuntimeError(str(error))
        alert_sent = True
        sent_now = True

    state.update(
        {
            "alert_sent": alert_sent,
            "last_run_id": newest.run_id,
            "consecutive_bad_runs": consecutive_bad,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    _write_state(state_path, state)
    return {
        "source": source,
        "channel": channel,
        "threshold": threshold,
        "latest_run_id": newest.run_id,
        "latest_status": newest.source_status,
        "latest_found_count": newest.found_count,
        "consecutive_bad_runs": consecutive_bad,
        "alert_sent": sent_now,
        "state_alert_sent": alert_sent,
    }


def _default_deliver(message: str, channel: str):
    from job_intel.cli import _deliver_to_slack

    return _deliver_to_slack(message, channel)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, default=Path(os.getenv("JOB_INTEL_DB_PATH", "")) if os.getenv("JOB_INTEL_DB_PATH", "") else None)
    parser.add_argument("--state-path", type=Path, default=Path(os.getenv("JOB_INTEL_SOURCE_ALERT_STATE", str(DEFAULT_STATE_PATH))))
    parser.add_argument("--source", default=os.getenv("JOB_INTEL_SOURCE_ALERT_SOURCE", DEFAULT_SOURCE))
    parser.add_argument("--threshold", type=int, default=int(os.getenv("JOB_INTEL_SOURCE_ALERT_THRESHOLD", DEFAULT_THRESHOLD)))
    parser.add_argument("--channel", default=os.getenv("JOB_INTEL_SOURCE_ALERT_CHANNEL", DEFAULT_CHANNEL))
    parser.add_argument("--message-prefix", default=os.getenv("JOB_INTEL_SOURCE_ALERT_MESSAGE_PREFIX", "[job-intel]"))
    args = parser.parse_args(argv)

    if args.db_path is None:
        from job_intel.runtime import resolve_db_path

        db_path = resolve_db_path()
    else:
        db_path = args.db_path
    try:
        summary = scan_and_alert(
            db_path=db_path,
            state_path=args.state_path,
            threshold=args.threshold,
            source=args.source,
            channel=args.channel,
            deliver=_default_deliver,
            message_prefix=args.message_prefix,
        )
    except Exception as exc:
        print(f"source alert failed: {exc}")
        return 1
    print(json.dumps(asdict(summary) if hasattr(summary, "__dataclass_fields__") else summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
