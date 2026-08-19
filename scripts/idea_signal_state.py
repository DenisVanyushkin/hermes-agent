"""Shared, process-safe coordination for idea-signal state files."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
from pathlib import Path
import threading


_THREAD_LOCKS: dict[Path, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _thread_lock(path: Path) -> threading.Lock:
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(path, threading.Lock())


@contextmanager
def state_lock(state_dir: Path):
    """Serialize state load-modify-persist across both idea-signal CLIs."""
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / ".idea_signal_state.lock"
    with _thread_lock(lock_path):
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
