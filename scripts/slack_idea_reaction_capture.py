#!/usr/bin/env python3
"""CLI wrapper for Slack idea-reaction capture.

Reads a Slack reaction event JSON payload from stdin or --event-file and, when
one of the configured 👍 reactions is added to a bot-authored idea post,
appends that idea to the Google Doc "Идеи и улучшения".
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from job_intel.idea_reaction_capture import main


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
