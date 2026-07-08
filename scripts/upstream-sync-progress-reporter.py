#!/usr/bin/env python3
"""Host-side verbose progress reporter for an upstream-sync Mode B apply.

Spawned (detached) by the gateway when it queues the Mode B one-shot. Runs as
the unprivileged repo owner, so it observes ONLY the live git repo state (the
sandbox rebases the same bind-mounted repo) — the finalize state files live
under a root-0700 sandbox home it cannot read. It posts milestone + heartbeat
messages to the operator's thread via ``hermes send`` and exits on a terminal
git state (success/rollback) or timeout.

Usage:
  upstream-sync-progress-reporter.py --target slack:C..:T.. --repo /path \
      [--hermes-bin /path/to/hermes] [--heartbeat 120] [--timeout 3600]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hermes_cli.upstream_sync_progress import (  # noqa: E402
    render_progress,
    read_rebase_progress,
    classify_terminal,
    is_terminal,
)


def _git(repo: str, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True, text=True, timeout=30,
        )
        return out.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return ""


def _post(hermes_bin: str, target: str, message: str) -> None:
    try:
        subprocess.run(
            [hermes_bin, "send", "-t", target, "-q", message],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.SubprocessError, OSError):
        pass  # transient (e.g. gateway mid-restart) — best-effort


def _conflict_files(repo: str) -> list[str]:
    out = _git(repo, "diff", "--name-only", "--diff-filter=U")
    return [l for l in out.splitlines() if l.strip()]


def _new_backup_ref(repo: str, baseline: set[str]) -> str | None:
    out = _git(repo, "for-each-ref", "--format=%(refname:short)",
               "refs/heads/backup/pre-upstream-sync-*")
    for ref in out.splitlines():
        ref = ref.strip()
        if ref and ref not in baseline:
            return ref
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--hermes-bin", default="hermes")
    ap.add_argument("--heartbeat", type=int, default=120)
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--upstream-ref", default="upstream/main")
    args = ap.parse_args()

    repo, target, hb = args.repo, args.target, args.heartbeat
    # Baseline: backup refs that already existed, so we detect THIS run's new one.
    baseline = {
        l.strip() for l in _git(
            repo, "for-each-ref", "--format=%(refname:short)",
            "refs/heads/backup/pre-upstream-sync-*",
        ).splitlines() if l.strip()
    }

    last_key: str | None = None
    last_applied: int | None = None
    last_total: int | None = None
    backup_commit: str | None = None
    last_hb = 0.0
    deadline = time.monotonic() + args.timeout

    while time.monotonic() < deadline:
        applied, total, rebasing = read_rebase_progress(repo)
        if applied is not None:
            last_applied, last_total = applied, total
        backup_ref = _new_backup_ref(repo, baseline)
        if backup_ref and backup_commit is None:
            backup_commit = _git(repo, "rev-parse", backup_ref)

        finalize_status = None
        if not rebasing and backup_ref and last_applied is not None:
            head = _git(repo, "rev-parse", "HEAD")
            behind = _git(repo, "rev-list", "--count", f"HEAD..{args.upstream_ref}")
            try:
                behind_n = int(behind)
            except ValueError:
                behind_n = -1
            if backup_commit and behind_n >= 0:
                finalize_status = classify_terminal(
                    head=head, backup_commit=backup_commit, behind_upstream=behind_n
                )

        snapshot = {
            "backup_ref": backup_ref,
            "rebasing": rebasing,
            "applied": last_applied,
            "total": last_total,
            "conflict_files": _conflict_files(repo) if rebasing else [],
            "finalize_status": "ok" if finalize_status == "success"
            else ("rollback" if finalize_status == "rollback" else None),
            "finalize_requested": False,
            "pending_present": finalize_status is None,
        }

        now = time.monotonic()
        heartbeat_due = rebasing and (now - last_hb >= hb)
        msg, key = render_progress(snapshot, last_key, heartbeat_due=heartbeat_due)
        if msg:
            _post(hermes_bin=args.hermes_bin, target=target, message=msg)
            if heartbeat_due:
                last_hb = now
        last_key = key
        if is_terminal(key):
            return 0
        time.sleep(15)

    _post(args.hermes_bin, target,
          "⚠️ Upstream-sync: репортёр вышел по таймауту, проверь статус вручную.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
