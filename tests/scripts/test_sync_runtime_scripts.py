"""Tests for runtime-sync inclusion of the idea source registry."""

from pathlib import Path


SYNC_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sync-runtime-scripts.sh"


def test_runtime_sync_copies_idea_source_registry_beside_collector():
    text = SYNC_SCRIPT.read_text(encoding="utf-8")

    assert '"$REPO/config/idea_sources.yaml"' in text
    assert '"$TARGET_DIR/idea_sources.yaml"' in text
