#!/usr/bin/env python3
"""Keep the privileged browser bootstrap and its profile lock in the foreground."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen


def _notify(message: str) -> None:
    address = os.environ.get("NOTIFY_SOCKET", "")
    if not address:
        raise RuntimeError("NOTIFY_SOCKET is not set")
    if address.startswith("@"):  # systemd's abstract namespace notation
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify_socket:
        notify_socket.sendto(message.encode(), address)


def _cdp_ready(cdp_url: str) -> bool:
    try:
        with urlopen(f"{cdp_url.rstrip('/')}/json/version", timeout=1) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def _terminate(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def _stop_profile(profile: Path) -> int:
    stop_script = Path(__file__).with_name("browser-desktop-stop.sh")
    return subprocess.run(
        ["bash", str(stop_script), "--profile", profile.name],
        check=False,
    ).returncode


def _lock_holder_pids(lock_path: Path) -> list[int]:
    lock_script = Path(__file__).with_name("job_intel_profile_lock.sh")
    expected_tail = (str(lock_script), "--path", str(lock_path))
    holder_pids: list[int] = []
    for process_dir in Path("/proc").iterdir():
        if not process_dir.name.isdigit():
            continue
        pid = int(process_dir.name)
        if pid == os.getpid():
            continue
        try:
            argv = [
                part.decode(errors="surrogateescape")
                for part in (process_dir / "cmdline").read_bytes().split(b"\0")
                if part
            ]
        except (OSError, ValueError):
            continue
        if len(argv) >= len(expected_tail) and tuple(argv[-len(expected_tail) :]) == expected_tail:
            holder_pids.append(pid)
    return holder_pids


def _pid_is_running(pid: int) -> bool:
    try:
        stat = (Path("/proc") / str(pid) / "stat").read_text(encoding="ascii")
    except OSError:
        return False
    try:
        state = stat.rsplit(")", 1)[1].lstrip().split(maxsplit=1)[0]
    except IndexError:
        return False
    return state != "Z"


def _terminate_lock_holder(pid: int) -> None:
    try:
        process_group = os.getpgid(pid)
    except ProcessLookupError:
        return
    try:
        if process_group == os.getpgrp():
            os.kill(pid, signal.SIGTERM)
        else:
            os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not _pid_is_running(pid):
            return
        time.sleep(0.05)
    try:
        if process_group == os.getpgrp():
            os.kill(pid, signal.SIGKILL)
        else:
            os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not _pid_is_running(pid):
            return
        time.sleep(0.05)
    raise RuntimeError(f"profile lock holder did not exit: pid={pid}")


def _release_profile_lock(lock_path: Path) -> None:
    for pid in _lock_holder_pids(lock_path):
        _terminate_lock_holder(pid)
    remaining = _lock_holder_pids(lock_path)
    if remaining:
        raise RuntimeError(
            f"profile lock holders remained after stop: {', '.join(map(str, remaining))}"
        )


def _target_for_source(source: str) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from job_intel.browser_worker import _CDP_TARGETS

    target = _CDP_TARGETS.get(source)
    if not target:
        raise RuntimeError(f"no browser target is configured for source: {source}")
    return target


def _run(args: argparse.Namespace) -> int:
    lock_script = Path(__file__).with_name("job_intel_profile_lock.sh")
    target = _target_for_source(args.source)
    default_profile = Path("/var/lib/browser-desktop/profiles") / str(target["profile"])
    profile_override = args.profile != default_profile
    if (profile_override or args.url is not None) and not args.cdp_url:
        raise RuntimeError("profile or URL overrides require explicit --cdp-url")
    cdp_url = args.cdp_url or str(target["cdp_url"])
    lock_holder: subprocess.Popen[str] | None = None
    cleanup_done = False

    def cleanup() -> None:
        nonlocal cleanup_done
        if cleanup_done:
            return
        cleanup_done = True
        _terminate(lock_holder)

    def handle_shutdown(_signum: int, _frame: object) -> None:
        cleanup()
        raise SystemExit(0)

    previous_handlers = {
        signum: signal.signal(signum, handle_shutdown)
        for signum in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        lock_holder = subprocess.Popen(
            ["bash", str(lock_script), "--path", str(args.lock_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        acquired = lock_holder.stdout.readline() if lock_holder.stdout else ""
        if lock_holder.poll() is not None or "profile lock acquired:" not in acquired:
            stderr = lock_holder.stderr.read().strip() if lock_holder.stderr else ""
            raise RuntimeError(f"profile lock was not acquired: {stderr or acquired.strip()}")

        bootstrap_command = [
            "bash",
            str(args.bootstrap_script),
            "--profile",
            args.profile.name,
            "--url",
            args.url or str(target["start_url"]),
        ]
        if args.network_namespace:
            bootstrap_command.extend(["--network-namespace", args.network_namespace])
        bootstrap_result = subprocess.run(
            bootstrap_command,
            capture_output=True,
            text=True,
            timeout=args.bootstrap_timeout,
            check=False,
        )
        if bootstrap_result.returncode != 0:
            detail = (bootstrap_result.stderr or bootstrap_result.stdout).strip()
            raise RuntimeError(f"browser bootstrap failed: {detail or bootstrap_result.returncode}")

        deadline = time.monotonic() + args.startup_timeout
        while time.monotonic() < deadline:
            if _cdp_ready(cdp_url):
                _notify("READY=1\n")
                break
            time.sleep(args.poll_interval)
        else:
            raise TimeoutError(f"CDP endpoint did not become ready: {cdp_url}")

        while True:
            time.sleep(args.monitor_interval)
            if not _cdp_ready(cdp_url):
                raise RuntimeError(f"CDP endpoint stopped responding: {cdp_url}")
    finally:
        cleanup()
        for signum, previous_handler in previous_handlers.items():
            signal.signal(signum, previous_handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--cdp-url", help="explicit CDP endpoint; required for profile or URL overrides (otherwise resolved from --source)")
    parser.add_argument("--url")
    parser.add_argument("--lock-path", type=Path, default=Path("/run/job-intel/linkedin-profile.lock"))
    parser.add_argument("--startup-timeout", type=float, default=120.0)
    parser.add_argument("--poll-interval", type=float, default=0.2)
    parser.add_argument("--monitor-interval", type=float, default=5.0)
    parser.add_argument("--bootstrap-script", type=Path, default=Path(__file__).with_name("browser-desktop-bootstrap.sh"))
    parser.add_argument("--network-namespace")
    parser.add_argument("--bootstrap-timeout", type=float, default=600.0)
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.stop:
            stop_returncode = _stop_profile(args.profile)
            _release_profile_lock(args.lock_path)
            return stop_returncode
        return _run(args)
    except (OSError, RuntimeError, TimeoutError) as exc:
        print(f"browser supervisor failed: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
