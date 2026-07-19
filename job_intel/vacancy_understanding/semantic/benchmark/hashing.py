"""Content-hashing helpers shared by the benchmark package (Step 5B)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path | str) -> str:
    return sha256_bytes(Path(path).read_bytes())


def sha256_json(obj: Any) -> str:
    return sha256_text(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
