import importlib.util
from pathlib import Path

import yaml


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "update_openrouter_fallback.py"
SPEC = importlib.util.spec_from_file_location("update_openrouter_fallback", SCRIPT_PATH)
update_openrouter_fallback = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(update_openrouter_fallback)


SEL = {
    "fallback": ["new/model", "second/model"],
    "compression": "big/model",
    "web_extract": "big/model",
    "title_generation": "small/model",
}


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

    update_openrouter_fallback.update_config(config, SEL)

    loaded = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert loaded["fallback_providers"] == [
        {"provider": "openrouter", "model": "new/model"},
        {"provider": "openrouter", "model": "second/model"},
    ]
    assert loaded["agent"]["max_turns"] == 120
    assert loaded["auxiliary"]["compression"]["model"] == "big/model"
    assert loaded["auxiliary"]["title_generation"]["provider"] == "openrouter"


def test_update_config_reverts_aux_to_primary_when_no_free_model(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "model:\n"
        "  default: gpt-5.4-mini\n"
        "fallback_providers: []\n",
        encoding="utf-8",
    )
    sel = dict(SEL, title_generation=None)

    update_openrouter_fallback.update_config(config, sel)

    loaded = yaml.safe_load(config.read_text(encoding="utf-8"))
    title = loaded["auxiliary"]["title_generation"]
    assert title["provider"] == update_openrouter_fallback.PRIMARY["provider"]
    assert title["model"] == update_openrouter_fallback.PRIMARY["model"]


def test_update_config_preserves_comments_and_quoted_keys(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "model:\n"
        "  default: gpt-5.4-mini\n"
        "fallback_providers:\n"
        "- provider: openrouter\n"
        "  model: old/model\n"
        "pipelines:\n"
        "  # Channel gate (host-local, not in git): only these platforms\n"
        "  # use the pipeline router.\n"
        "  channels:\n"
        "  - telegram\n"
        "'quoted key':\n"
        "  value: preserved\n",
        encoding="utf-8",
    )

    update_openrouter_fallback.update_config(config, SEL)

    text = config.read_text(encoding="utf-8")
    loaded = yaml.safe_load(text)
    assert loaded["fallback_providers"] == [
        {"provider": "openrouter", "model": "new/model"},
        {"provider": "openrouter", "model": "second/model"},
    ]
    assert "# Channel gate (host-local, not in git)" in text
    assert "# use the pipeline router." in text
    assert loaded["pipelines"]["channels"] == ["telegram"]
    assert loaded["quoted key"] == {"value": "preserved"}


def test_update_config_restores_backup_on_validation_failure(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    original = "model:\n  default: gpt-5.4-mini\nfallback_providers: []\n"
    config.write_text(original, encoding="utf-8")

    def broken_apply(cfg, sel):
        cfg["fallback_providers"] = [{"provider": "openrouter", "model": "wrong/model"}]
        return cfg

    monkeypatch.setattr(update_openrouter_fallback, "apply_selection", broken_apply)

    try:
        update_openrouter_fallback.update_config(config, SEL)
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass

    assert config.read_text(encoding="utf-8") == original
