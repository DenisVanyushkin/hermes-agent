"""Registration tests for the legal_research toolset."""

from tools.registry import registry
import tools.legal_research_tool  # noqa: F401  (top-level register calls)

EXPECTED = {
    "search_acts", "get_act_text", "get_act_info", "get_act_history",
    "get_act_links", "get_act_downloads", "healthcheck_source",
    "legal_answer_review",
}


def test_all_legal_tools_registered_in_toolset():
    registered = set(registry.get_tool_names_for_toolset("legal_research"))
    assert EXPECTED <= registered


def test_toolset_listed_in_toolsets_module():
    from toolsets import TOOLSETS
    assert "legal_research" in TOOLSETS
    assert EXPECTED <= set(TOOLSETS["legal_research"]["tools"])


def test_discovery_picks_up_the_module():
    from tools.registry import discover_builtin_tools
    from pathlib import Path
    import tools as tools_pkg
    tools_dir = Path(tools_pkg.__file__).resolve().parent
    names = [
        f"tools.{p.stem}" for p in sorted(tools_dir.glob("*.py"))
        if p.name == "legal_research_tool.py"
    ]
    assert names == ["tools.legal_research_tool"]
    # AST check: the module must contain top-level registry.register calls
    from tools.registry import _module_registers_tools
    assert _module_registers_tools(tools_dir / "legal_research_tool.py") is True
