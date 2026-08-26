"""Manifest and verification for code that runs before any import.

Python executes every ``.pth`` file and ``sitecustomize.py`` in its site
directories during interpreter startup — before ``job_intel`` is imported and
therefore before the delivery kill-switch exists. The commit pin cannot cover
them: the virtualenv is deliberately outside the repository and ignored by git,
so a tree check will never see a change there.

Usage:
    python job_intel_site_integrity.py write <manifest>
    python job_intel_site_integrity.py verify <manifest>

The manifest is a sorted ``sha256  path`` listing. Verification fails on a
changed, added, or removed entry, and prints which.
"""

from __future__ import annotations

import glob
import hashlib
import os
from pathlib import Path
import site
import sys


def _entries() -> list[tuple[str, str]]:
    seen: dict[str, str] = {}
    for directory in site.getsitepackages():
        patterns = (os.path.join(directory, "*.pth"), os.path.join(directory, "sitecustomize.py"))
        for pattern in patterns:
            for path in glob.glob(pattern):
                real = os.path.realpath(path)
                digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
                seen[real] = digest
    return sorted(seen.items())


def _render(entries: list[tuple[str, str]]) -> str:
    return "".join(f"{digest}  {path}\n" for path, digest in entries)


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in {"write", "verify"}:
        print(__doc__, file=sys.stderr)
        return 2
    action, manifest_path = argv[1], Path(argv[2])
    current = _render(_entries())

    if action == "write":
        manifest_path.write_text(current, encoding="utf-8")
        print(f"wrote {manifest_path} with {len(current.splitlines())} entries")
        return 0

    if not manifest_path.is_file():
        print(f"site manifest missing at {manifest_path}", file=sys.stderr)
        return 3
    recorded = manifest_path.read_text(encoding="utf-8")
    if recorded == current:
        print(f"site integrity OK: {len(current.splitlines())} pre-import entries match")
        return 0

    recorded_map = dict(line.split("  ", 1)[::-1] for line in recorded.splitlines() if line)
    current_map = dict(line.split("  ", 1)[::-1] for line in current.splitlines() if line)
    for path in sorted(set(recorded_map) | set(current_map)):
        was, now = recorded_map.get(path), current_map.get(path)
        if was != now:
            state = "added" if was is None else "removed" if now is None else "changed"
            print(f"pre-import code {state}: {path}", file=sys.stderr)
    return 4


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
