#!/usr/bin/env python3
"""Print the latest diagnostics digest for injection into the morning reporter prompt."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

STALE_HOURS = 12
MAX_CHARS = 24000


def render(digest_path: Path, now: datetime) -> str:
    if not digest_path.exists():
        return f"DIGEST MISSING: collector did not produce {digest_path}"
    try:
        digest = json.loads(digest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return f"DIGEST MISSING: unreadable ({exc})"
    body = json.dumps(digest, ensure_ascii=False, indent=1)[:MAX_CHARS]
    generated_raw = str(digest.get("generated_at", ""))
    try:
        generated = datetime.fromisoformat(generated_raw)
    except ValueError:
        return f"DIGEST STALE (generated_at unparseable: {generated_raw!r})\n{body}"
    age_hours = (now - generated).total_seconds() / 3600
    if age_hours > STALE_HOURS:
        return f"DIGEST STALE (generated_at={generated_raw}, age={age_hours:.0f}h)\n{body}"
    return body


def main() -> int:
    home = Path(os.environ.get("HERMES_HOME", "").strip() or "/home/hermes/.hermes")
    print(render(home / "diagnostics" / "digest-latest.json", datetime.now()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
