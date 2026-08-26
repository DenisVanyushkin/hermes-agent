"""Manifest and verification for the interpreter tree that runs before any import.

Python executes every ``.pth`` in its site directories at startup, and those
``.pth`` files import real modules: on this host ``_virtualenv.pth`` imports
``_virtualenv.py`` and ``00-pysqlite3-shim.pth`` imports the ``pysqlite3``
package and its extension. Hashing only the ``.pth`` files would therefore
leave the code they load unguarded, so the whole target **virtualenv** is
manifested instead — not just one ``site-packages``. That wider boundary is
not optional: ``venv/bin/python`` is itself a replaceable symlink,
``pyvenv.cfg`` decides which interpreter and which site directories are used,
and a Debian venv resolves several ``dist-packages`` directories, any of which
can be created later and will execute its ``.pth`` files.

**This script must be run by a trusted interpreter that is not the target
venv**, with ``-I -S`` so it performs no site initialisation of its own.
Running the target venv to check the target venv executes the very code under
suspicion first — a guard placed after the door.

Usage (both forms take the target site-packages explicitly):
    /usr/bin/python3.12 -I -S job_intel_site_integrity.py write <manifest> <venv-root>
    /usr/bin/python3.12 -I -S job_intel_site_integrity.py verify <manifest> <venv-root>

Manifest lines are ``sha256  mode  uid  gid  size  relpath`` for regular files,
``symlink  ->  target  relpath`` for symlinks and ``dir  mode  uid  gid  relpath``
for directories, sorted by path. Symlinks are never followed.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
import sys


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _manifest(root: Path) -> str:
    lines: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        base = Path(dirpath)
        for name in sorted(dirnames + filenames):
            path = base / name
            rel = path.relative_to(root).as_posix()
            info = path.lstat()
            if os.path.islink(path):
                lines.append(f"symlink  ->  {os.readlink(path)}  {rel}")
            elif path.is_dir():
                lines.append(f"dir  {info.st_mode:o}  {info.st_uid}  {info.st_gid}  {rel}")
            elif not stat.S_ISREG(info.st_mode):
                # A FIFO, socket or device would block or mislead an attempt to
                # hash it, so the presence of one inside the trust boundary is
                # itself the finding.
                lines.append(f"special  {stat.S_IFMT(info.st_mode):o}  {rel}")
            else:
                lines.append(
                    f"{_digest(path)}  {info.st_mode:o}  {info.st_uid}  "
                    f"{info.st_gid}  {info.st_size}  {rel}"
                )
    lines.sort(key=lambda line: line.rsplit("  ", 1)[-1])
    return "".join(line + "\n" for line in lines)


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] not in {"write", "verify"}:
        print(__doc__, file=sys.stderr)
        return 2
    action, manifest_path, site_root = argv[1], Path(argv[2]), Path(argv[3])

    if "site" in sys.modules:
        # -S was not passed: this interpreter has already run site initialisation.
        # For verify that means the guarded code may already have executed; for
        # write it is worse — a compromised tree would be blessed into the
        # manifest by the very run that executed it.
        print(f"refusing to {action} from an interpreter that ran site init", file=sys.stderr)
        return 5

    if site_root.is_symlink():
        print(f"trust root {site_root} is a symlink; refusing", file=sys.stderr)
        return 7
    if not site_root.is_dir():
        print(f"trust root not found at {site_root}", file=sys.stderr)
        return 6

    root_info = site_root.lstat()
    current = (
        f"root  {root_info.st_mode:o}  {root_info.st_uid}  {root_info.st_gid}  .\n"
        + _manifest(site_root)
    )

    if action == "write":
        manifest_path.write_text(current, encoding="utf-8")
        print(f"wrote {manifest_path}: {len(current.splitlines())} entries under {site_root}")
        return 0

    if not manifest_path.is_file():
        print(f"site manifest missing at {manifest_path}", file=sys.stderr)
        return 3
    if manifest_path.read_text(encoding="utf-8") == current:
        print(f"site integrity OK: {len(current.splitlines())} entries match")
        return 0

    recorded = {ln.rsplit("  ", 1)[-1]: ln for ln in manifest_path.read_text(encoding="utf-8").splitlines()}
    now = {ln.rsplit("  ", 1)[-1]: ln for ln in current.splitlines()}
    shown = 0
    for rel in sorted(set(recorded) | set(now)):
        if recorded.get(rel) != now.get(rel):
            state = "added" if rel not in recorded else "removed" if rel not in now else "changed"
            print(f"startup tree {state}: {rel}", file=sys.stderr)
            shown += 1
            if shown >= 10:
                print("...", file=sys.stderr)
                break
    return 4


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
