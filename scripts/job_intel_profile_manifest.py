#!/usr/bin/env python3
"""Emit a deterministic, content-addressable manifest of a profile tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any


# Consumers compare the SHA-256 of this canonical output. A directory has no
# SHA-256 operation of its own; hashing only paths, modes, sizes, and regular
# file contents would miss replacing a directory with a symlink or changing
# ownership. The lstat metadata below makes those changes visible.
FORMAT_VERSION = "profile-manifest-v1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entry(path: Path, relative_path: str, file_type: str, metadata: os.stat_result) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "path": relative_path,
        "type": file_type,
        "mode": stat.S_IMODE(metadata.st_mode),
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "size": metadata.st_size,
    }
    if file_type == "file":
        entry["sha256"] = _sha256_file(path)
    elif file_type == "symlink":
        entry["target"] = os.readlink(path)
    return entry


def build_manifest(root: Path) -> dict[str, Any]:
    root = root.absolute()
    entries: list[dict[str, Any]] = []

    def visit(path: Path, relative_path: str) -> None:
        metadata = os.lstat(path)
        mode = metadata.st_mode
        if stat.S_ISREG(mode):
            file_type = "file"
        elif stat.S_ISDIR(mode):
            file_type = "directory"
        elif stat.S_ISLNK(mode):
            file_type = "symlink"
        else:
            raise ValueError(f"unsupported profile entry type: {relative_path}")

        entries.append(_entry(path, relative_path, file_type, metadata))
        if file_type != "directory":
            return

        children = sorted(os.scandir(path), key=lambda child: child.name)
        for child in children:
            child_relative = (
                child.name if relative_path == "." else f"{relative_path}/{child.name}"
            )
            visit(Path(child.path), child_relative)

    visit(root, ".")
    entries.sort(key=lambda item: item["path"])
    return {"format_version": FORMAT_VERSION, "entries": entries}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path)
    args = parser.parse_args()
    try:
        manifest = build_manifest(args.profile)
    except (OSError, ValueError) as exc:
        print(f"profile manifest: {exc}", file=sys.stderr)
        return 1

    encoded = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    sys.stdout.buffer.write(encoded + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
