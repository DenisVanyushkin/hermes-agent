"""Adilet (adilet.zan.kz) legal research tools for the lawyer role.

Kazakhstan normative legal acts: search, act text (with per-article
extraction), metadata, change history, cross-references, downloads.
All fetches go to the public government source adilet.zan.kz.
"""

import json

from tools.legal_research.adilet_client import AdiletClient, AdiletError
from tools.registry import registry
from tools.url_safety import is_safe_url

_client = AdiletClient()

_SOURCE_URL = "https://adilet.zan.kz/rus/index/docs"

_SCHEMAS = {schema["name"]: schema for schema in AdiletClient.tool_schemas()}


def _run(tool_name: str, args: dict) -> str:
    if not is_safe_url(_SOURCE_URL):
        return json.dumps(
            {"error": "source blocked by url safety policy", "tool": tool_name},
            ensure_ascii=False,
        )
    try:
        payload = _client.run_tool(tool_name, args or {})
        return json.dumps(payload, ensure_ascii=False)
    except AdiletError as exc:
        return json.dumps({"error": str(exc), "tool": tool_name}, ensure_ascii=False)


def _check_legal_research() -> bool:
    return True


def _schema(name: str) -> dict:
    spec = _SCHEMAS[name]
    return {
        "name": name,
        "description": spec["description"],
        "parameters": spec["inputSchema"],
    }


registry.register(
    name="search_acts",
    toolset="legal_research",
    schema=_schema("search_acts"),
    handler=lambda args, **kw: _run("search_acts", args),
    check_fn=_check_legal_research,
    requires_env=[],
    is_async=False,
    emoji="⚖️",
    max_result_size_chars=60_000,
)

registry.register(
    name="get_act_text",
    toolset="legal_research",
    schema=_schema("get_act_text"),
    handler=lambda args, **kw: _run("get_act_text", args),
    check_fn=_check_legal_research,
    requires_env=[],
    is_async=False,
    emoji="⚖️",
    max_result_size_chars=120_000,
)

registry.register(
    name="get_act_info",
    toolset="legal_research",
    schema=_schema("get_act_info"),
    handler=lambda args, **kw: _run("get_act_info", args),
    check_fn=_check_legal_research,
    requires_env=[],
    is_async=False,
    emoji="⚖️",
    max_result_size_chars=30_000,
)

registry.register(
    name="get_act_history",
    toolset="legal_research",
    schema=_schema("get_act_history"),
    handler=lambda args, **kw: _run("get_act_history", args),
    check_fn=_check_legal_research,
    requires_env=[],
    is_async=False,
    emoji="⚖️",
    max_result_size_chars=60_000,
)

registry.register(
    name="get_act_links",
    toolset="legal_research",
    schema=_schema("get_act_links"),
    handler=lambda args, **kw: _run("get_act_links", args),
    check_fn=_check_legal_research,
    requires_env=[],
    is_async=False,
    emoji="⚖️",
    max_result_size_chars=60_000,
)

registry.register(
    name="get_act_downloads",
    toolset="legal_research",
    schema=_schema("get_act_downloads"),
    handler=lambda args, **kw: _run("get_act_downloads", args),
    check_fn=_check_legal_research,
    requires_env=[],
    is_async=False,
    emoji="⚖️",
    max_result_size_chars=10_000,
)

registry.register(
    name="healthcheck_source",
    toolset="legal_research",
    schema=_schema("healthcheck_source"),
    handler=lambda args, **kw: _run("healthcheck_source", args),
    check_fn=_check_legal_research,
    requires_env=[],
    is_async=False,
    emoji="⚖️",
    max_result_size_chars=10_000,
)
