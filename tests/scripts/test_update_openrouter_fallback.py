import importlib.util
from pathlib import Path

import yaml


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "update_openrouter_fallback.py"
SPEC = importlib.util.spec_from_file_location("update_openrouter_fallback", SCRIPT_PATH)
update_openrouter_fallback = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(update_openrouter_fallback)


def test_update_config_replaces_existing_fallback_providers(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "model:\n"
        "  default: gpt-5.4-mini\n"
        "providers: {}\n"
        "fallback_providers:\n"
        "  - provider: openrouter\n"
        "    model: old/model\n"
        "agent:\n"
        "  max_turns: 120\n",
        encoding="utf-8",
    )

    update_openrouter_fallback.update_config(config, ["new/model", "second/model"])

    loaded = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert loaded["fallback_providers"] == [
        {"provider": "openrouter", "model": "new/model"},
        {"provider": "openrouter", "model": "second/model"},
    ]
    assert loaded["agent"]["max_turns"] == 120


def test_update_config_repairs_orphaned_top_level_fallback_provider_items(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "model:\n"
        "  default: gpt-5.4-mini\n"
        "providers: {}\n"
        "fallback_providers:\n"
        "  - provider: openrouter\n"
        "    model: old/model\n"
        "- provider: openrouter\n"
        "  model: orphan/model\n"
        "credential_pool_strategies: {}\n"
        "toolsets:\n"
        "- hermes-cli\n",
        encoding="utf-8",
    )

    update_openrouter_fallback.update_config(config, ["new/model"])

    loaded = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert loaded["fallback_providers"] == [{"provider": "openrouter", "model": "new/model"}]
    assert loaded["credential_pool_strategies"] == {}
    assert loaded["toolsets"] == ["hermes-cli"]
    assert "orphan/model" not in config.read_text(encoding="utf-8")


def test_update_config_preserves_quoted_top_level_key_after_fallback_providers(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "model:\n"
        "  default: gpt-5.4-mini\n"
        "fallback_providers:\n"
        "  - provider: openrouter\n"
        "    model: old/model\n"
        "'quoted key':\n"
        "  value: preserved\n",
        encoding="utf-8",
    )

    update_openrouter_fallback.update_config(config, ["new/model"])

    loaded = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert loaded["fallback_providers"] == [{"provider": "openrouter", "model": "new/model"}]
    assert loaded["quoted key"] == {"value": "preserved"}


def test_find_block_keeps_flush_left_sequence_items_with_parent_key():
    lines = [
        "toolsets:",
        "- hermes-cli",
        "agent:",
        "  max_turns: 120",
    ]

    assert update_openrouter_fallback._find_block(lines, "toolsets") == (0, 2)


def test_find_block_stops_at_top_level_comment_before_next_key():
    lines = [
        "fallback_providers:",
        "  - provider: openrouter",
        "    model: old/model",
        "# next section comment",
        "agent:",
        "  max_turns: 120",
    ]

    assert update_openrouter_fallback._find_block(lines, "fallback_providers") == (0, 3)
